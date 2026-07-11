#include "reader.h"

#include "serial_io.h"
#include "util.h"

#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    const char *name;
    unsigned int register_offset;
} preview_field_t;

static const preview_field_t preview_fields[] = {
    {"Batt_volt_Min", 0U},
    {"PTemp", 2U},
    {"WD", 4U},
    {"WS_Avg", 6U},
    {"Airtemp_Avg", 8U},
    {"RH_Avg", 10U},
    {"BP_Avg", 12U},
    {"Dew_temp_Avg", 14U},
    {"LPS_GHI_Avg", 16U},
    {"LPS_GHI_Max", 18U},
    {"Flux_min", 20U},
    {"Flux_avg", 22U},
    {"Flux_max", 24U},
    {"Flux_std", 26U},
    {"Flux_cum", 28U},
    {"wind_min", 30U},
    {"wind_avg", 32U},
    {"wind_max", 34U},
    {"TargetmV_Avg", 36U},
    {"DetectorTC_Avg", 38U},
    {"TargetTC_Avg", 40U},
};

struct reader_worker {
    reader_kind_t kind;
    const reader_config_t *config;
    char session_id[128];
    storage_logger_t *raw_logger;
    storage_logger_t *health_logger;
    volatile sig_atomic_t *stop_flag;
    bool once;
    pthread_t thread;
    bool thread_started;
    atomic_bool running;
    atomic_uint_fast64_t last_progress_ns;
    atomic_int last_status;
    uint64_t sequence;
};

static const char *worker_sensor_name(const reader_worker_t *worker) {
    return worker->kind == READER_PARSIVEL2 ? "parsivel2" : "modbus_rtu";
}

static const char *worker_device(const reader_worker_t *worker) {
    return worker->kind == READER_PARSIVEL2 ? worker->config->p2.device :
                                              worker->config->modbus.device;
}

static int worker_baud(const reader_worker_t *worker) {
    return worker->kind == READER_PARSIVEL2 ? worker->config->p2.baud :
                                              worker->config->modbus.baud;
}

static unsigned int worker_interval_ms(const reader_worker_t *worker) {
    return worker->kind == READER_PARSIVEL2 ? worker->config->p2.interval_ms :
                                              worker->config->modbus.interval_ms;
}

static int append_common_record(strbuf_t *record, const reader_worker_t *worker,
                                const char *schema, const char *wall_time,
                                const struct timespec *realtime,
                                uint64_t monotonic_ns, uint64_t duration_ns,
                                transaction_status_t status) {
    uint64_t realtime_ns = (uint64_t)realtime->tv_sec * UINT64_C(1000000000) +
                           (uint64_t)realtime->tv_nsec;

    if (strbuf_append(record, "{\"schema\":") != 0 ||
        strbuf_append_json_string(record, schema) != 0 ||
        strbuf_append(record, ",\"session_id\":") != 0 ||
        strbuf_append_json_string(record, worker->session_id) != 0 ||
        strbuf_append(record, ",\"sensor\":") != 0 ||
        strbuf_append_json_string(record, worker_sensor_name(worker)) != 0 ||
        strbuf_appendf(record,
                       ",\"sequence\":%" PRIu64
                       ",\"wall_time\":\"%s\",\"realtime_ns\":%" PRIu64
                       ",\"monotonic_ns\":%" PRIu64 ",\"duration_ms\":%.3f"
                       ",\"status\":\"%s\"",
                       worker->sequence, wall_time, realtime_ns,
                       monotonic_ns, (double)duration_ns / 1000000.0,
                       transaction_status_name(status)) != 0) {
        return -1;
    }
    return 0;
}

static int write_raw_record(reader_worker_t *worker,
                            const uint8_t *request, size_t request_length,
                            const uint8_t *response, size_t response_length,
                            transaction_status_t status, int system_errno,
                            bool truncated, uint64_t started_ns,
                            uint64_t finished_ns, const struct timespec *realtime) {
    strbuf_t record;
    char wall_time[48];
    char *request_hex = NULL;
    char *response_hex = NULL;
    int result = -1;

    request_hex = hex_encode_alloc(request, request_length);
    response_hex = hex_encode_alloc(response, response_length);
    if (request_hex == NULL || response_hex == NULL || strbuf_init(&record, 1024U + response_length * 2U) != 0) {
        goto done;
    }
    format_iso8601_utc(realtime, wall_time, sizeof(wall_time));
    if (append_common_record(&record, worker, "aws.raw.v1", wall_time, realtime,
                             finished_ns, finished_ns - started_ns, status) != 0 ||
        strbuf_append(&record, ",\"device\":") != 0 ||
        strbuf_append_json_string(&record, worker_device(worker)) != 0 ||
        strbuf_appendf(&record,
                       ",\"baud\":%d,\"request_len\":%zu,\"request_hex\":\"%s\""
                       ",\"response_len\":%zu,\"response_hex\":\"%s\""
                       ",\"errno\":%d,\"truncated\":%s",
                       worker_baud(worker), request_length, request_hex,
                       response_length, response_hex, system_errno,
                       truncated ? "true" : "false") != 0) {
        strbuf_free(&record);
        goto done;
    }
    if (worker->kind == READER_MODBUS &&
        strbuf_appendf(&record,
                       ",\"slave\":%u,\"function\":3,\"start_register\":%u"
                       ",\"register_count\":%u",
                       worker->config->modbus.slave,
                       worker->config->modbus.start_register,
                       worker->config->modbus.register_count) != 0) {
        strbuf_free(&record);
        goto done;
    }
    if (strbuf_append(&record, ",\"error\":") != 0) {
        strbuf_free(&record);
        goto done;
    }
    if (system_errno == 0) {
        if (strbuf_append(&record, "null") != 0) {
            strbuf_free(&record);
            goto done;
        }
    } else if (strbuf_append_json_string(&record, strerror(system_errno)) != 0) {
        strbuf_free(&record);
        goto done;
    }
    if (strbuf_append(&record, "}") != 0) {
        strbuf_free(&record);
        goto done;
    }
    result = storage_logger_write(worker->raw_logger, record.data, finished_ns) == STORAGE_WRITE_ERROR ? -1 : 0;
    strbuf_free(&record);

done:
    free(request_hex);
    free(response_hex);
    return result;
}

static int write_p2_health(reader_worker_t *worker, const uint8_t *response,
                           size_t response_length, transaction_status_t status,
                           uint64_t started_ns, uint64_t finished_ns,
                           const struct timespec *realtime) {
    static const uint8_t marker[] = "TYP OP4A";
    strbuf_t record;
    char wall_time[48];
    int result;

    if (strbuf_init(&record, 512U) != 0) {
        return -1;
    }
    format_iso8601_utc(realtime, wall_time, sizeof(wall_time));
    if (append_common_record(&record, worker, "aws.health.v1", wall_time, realtime,
                             finished_ns, finished_ns - started_ns, status) != 0 ||
        strbuf_appendf(&record, ",\"response_len\":%zu,\"typ_op4a\":%s}",
                       response_length,
                       bytes_contains(response, response_length, marker, sizeof(marker) - 1U) ?
                           "true" : "false") != 0) {
        strbuf_free(&record);
        return -1;
    }
    result = storage_logger_write(worker->health_logger, record.data, finished_ns) ==
                     STORAGE_WRITE_ERROR
                 ? -1
                 : 0;
    strbuf_free(&record);
    return result;
}

static int append_modbus_preview(strbuf_t *record, const uint8_t *response,
                                 size_t response_length, unsigned int start_register,
                                 unsigned int register_count, unsigned int *nan_count) {
    size_t index;
    bool first = true;

    *nan_count = 0U;
    if (strbuf_append(record, "{ ") != 0) {
        return -1;
    }
    for (index = 0; index < sizeof(preview_fields) / sizeof(preview_fields[0]); ++index) {
        const preview_field_t *field = &preview_fields[index];
        size_t data_offset;
        float value;

        if (field->register_offset < start_register ||
            field->register_offset + 1U >= start_register + register_count) {
            continue;
        }
        data_offset = 3U + (size_t)(field->register_offset - start_register) * 2U;
        if (data_offset + 4U > response_length - 2U) {
            continue;
        }
        value = modbus_decode_float_cdab(response + data_offset);
        if (!first && strbuf_append(record, ",") != 0) {
            return -1;
        }
        first = false;
        if (strbuf_append_json_string(record, field->name) != 0 ||
            strbuf_append(record, ":") != 0) {
            return -1;
        }
        if (isfinite(value) == 0) {
            ++*nan_count;
            if (strbuf_append(record, "null") != 0) {
                return -1;
            }
        } else if (strbuf_appendf(record, "%.9g", (double)value) != 0) {
            return -1;
        }
    }
    return strbuf_append(record, " }");
}

static int write_modbus_health(reader_worker_t *worker, const uint8_t *response,
                               size_t response_length, transaction_status_t status,
                               int exception_code, uint64_t started_ns,
                               uint64_t finished_ns, const struct timespec *realtime) {
    strbuf_t record;
    char wall_time[48];
    unsigned int nan_count = 0U;
    int result;

    if (strbuf_init(&record, 1024U) != 0) {
        return -1;
    }
    format_iso8601_utc(realtime, wall_time, sizeof(wall_time));
    if (append_common_record(&record, worker, "aws.health.v1", wall_time, realtime,
                             finished_ns, finished_ns - started_ns, status) != 0 ||
        strbuf_appendf(&record, ",\"response_len\":%zu,\"exception_code\":%d,\"preview\":",
                       response_length, exception_code) != 0) {
        strbuf_free(&record);
        return -1;
    }
    if (status == TXN_OK) {
        if (append_modbus_preview(&record, response, response_length,
                                  worker->config->modbus.start_register,
                                  worker->config->modbus.register_count, &nan_count) != 0) {
            strbuf_free(&record);
            return -1;
        }
    } else if (strbuf_append(&record, "null") != 0) {
        strbuf_free(&record);
        return -1;
    }
    if (strbuf_appendf(&record, ",\"nan_fields\":%u}", nan_count) != 0) {
        strbuf_free(&record);
        return -1;
    }
    result = storage_logger_write(worker->health_logger, record.data, finished_ns) ==
                     STORAGE_WRITE_ERROR
                 ? -1
                 : 0;
    strbuf_free(&record);
    return result;
}

static void sleep_until(reader_worker_t *worker, uint64_t deadline_ns) {
    while (*worker->stop_flag == 0) {
        uint64_t now = clock_now_ns(CLOCK_MONOTONIC);
        struct timespec delay;
        uint64_t remaining;

        if (now >= deadline_ns) {
            break;
        }
        remaining = deadline_ns - now;
        if (remaining > 200000000ULL) {
            remaining = 200000000ULL;
        }
        delay.tv_sec = (time_t)(remaining / 1000000000ULL);
        delay.tv_nsec = (long)(remaining % 1000000000ULL);
        (void)nanosleep(&delay, NULL);
    }
}

static void update_next_deadline(uint64_t *deadline_ns, uint64_t interval_ns) {
    uint64_t now = clock_now_ns(CLOCK_MONOTONIC);
    do {
        *deadline_ns += interval_ns;
    } while (*deadline_ns <= now);
}

static transaction_status_t run_p2_sample(reader_worker_t *worker,
                                          serial_port_t *port) {
    static const uint8_t request[] = {'C', 'S', '/', 'P', 'A', '\r'};
    const p2_config_t *config = &worker->config->p2;
    uint8_t *response = calloc(config->max_response_bytes, 1U);
    serial_result_t serial_result;
    transaction_status_t status;
    struct timespec realtime;
    uint64_t started_ns = clock_now_ns(CLOCK_MONOTONIC);
    uint64_t finished_ns;
    int system_errno = 0;

    memset(&serial_result, 0, sizeof(serial_result));
    if (response == NULL) {
        app_log("ERROR", "cannot allocate p2 response buffer");
        return TXN_IO_ERROR;
    }
    if (port->fd < 0 && serial_port_open(port, config->device, config->baud,
                                         config->direction_gpio) != 0) {
        system_errno = errno;
        status = TXN_OPEN_ERROR;
    } else if (serial_transaction(port, request, sizeof(request), response,
                                  config->max_response_bytes, config->timeout_ms,
                                  config->quiet_ms, 0U, 0x03, worker->stop_flag,
                                  &serial_result) != 0) {
        system_errno = serial_result.system_errno != 0 ? serial_result.system_errno : errno;
        status = TXN_IO_ERROR;
        serial_port_close(port);
    } else if (serial_result.truncated) {
        status = TXN_TRUNCATED;
    } else if (serial_result.response_length == 0U) {
        status = TXN_TIMEOUT;
    } else if (!serial_result.terminated) {
        status = *worker->stop_flag != 0 ? TXN_INTERRUPTED : TXN_SHORT_FRAME;
    } else {
        status = TXN_OK;
    }
    finished_ns = clock_now_ns(CLOCK_MONOTONIC);
    (void)clock_gettime(CLOCK_REALTIME, &realtime);
    if (*worker->stop_flag == 0 || status != TXN_IO_ERROR || system_errno != EINTR) {
        if (write_raw_record(worker, request, sizeof(request), response,
                             serial_result.response_length, status, system_errno,
                             serial_result.truncated, started_ns, finished_ns, &realtime) != 0 ||
            write_p2_health(worker, response, serial_result.response_length, status,
                            started_ns, finished_ns, &realtime) != 0) {
            app_log("ERROR", "failed to store p2 record");
        }
    }
    free(response);
    return status;
}

static transaction_status_t run_modbus_sample(reader_worker_t *worker,
                                              serial_port_t *port) {
    const modbus_config_t *config = &worker->config->modbus;
    uint8_t request[8];
    uint8_t response[260];
    size_t request_length;
    size_t expected_length = (size_t)config->register_count * 2U + 5U;
    serial_result_t serial_result;
    modbus_validation_t validation = {.status = TXN_TIMEOUT, .exception_code = -1,
                                      .expected_length = expected_length};
    transaction_status_t status;
    struct timespec realtime;
    uint64_t started_ns = clock_now_ns(CLOCK_MONOTONIC);
    uint64_t finished_ns;
    int system_errno = 0;

    memset(&serial_result, 0, sizeof(serial_result));
    request_length = modbus_build_read_request(request, sizeof(request),
                                                (uint8_t)config->slave,
                                                (uint16_t)config->start_register,
                                                (uint16_t)config->register_count);
    if (port->fd < 0 && serial_port_open(port, config->device, config->baud,
                                         config->direction_gpio) != 0) {
        system_errno = errno;
        status = TXN_OPEN_ERROR;
    } else if (serial_transaction(port, request, request_length, response, sizeof(response),
                                  config->timeout_ms, 50U, expected_length,
                                  -1, worker->stop_flag, &serial_result) != 0) {
        system_errno = serial_result.system_errno != 0 ? serial_result.system_errno : errno;
        status = TXN_IO_ERROR;
        serial_port_close(port);
    } else if (serial_result.truncated) {
        status = TXN_TRUNCATED;
    } else if (*worker->stop_flag != 0 && serial_result.response_length < expected_length) {
        status = TXN_INTERRUPTED;
    } else {
        validation = modbus_validate_read_response(response, serial_result.response_length,
                                                    (uint8_t)config->slave,
                                                    (uint16_t)config->register_count);
        status = validation.status;
    }
    finished_ns = clock_now_ns(CLOCK_MONOTONIC);
    (void)clock_gettime(CLOCK_REALTIME, &realtime);
    if (*worker->stop_flag == 0 || status != TXN_IO_ERROR || system_errno != EINTR) {
        if (write_raw_record(worker, request, request_length, response,
                             serial_result.response_length, status, system_errno,
                             serial_result.truncated, started_ns, finished_ns, &realtime) != 0 ||
            write_modbus_health(worker, response, serial_result.response_length, status,
                                 validation.exception_code, started_ns, finished_ns,
                                 &realtime) != 0) {
            app_log("ERROR", "failed to store Modbus record");
        }
    }
    return status;
}

static void *reader_main(void *argument) {
    reader_worker_t *worker = argument;
    serial_port_t port;
    uint64_t interval_ns = (uint64_t)worker_interval_ms(worker) * 1000000ULL;
    uint64_t next_deadline = clock_now_ns(CLOCK_MONOTONIC);

    serial_port_reset(&port);
    atomic_store(&worker->running, true);
    atomic_store(&worker->last_progress_ns, next_deadline);
    app_log("INFO", "%s reader started on %s at %d baud",
            worker_sensor_name(worker), worker_device(worker), worker_baud(worker));

    while (*worker->stop_flag == 0) {
        transaction_status_t status;

        ++worker->sequence;
        status = worker->kind == READER_PARSIVEL2 ? run_p2_sample(worker, &port) :
                                                    run_modbus_sample(worker, &port);
        atomic_store(&worker->last_status, (int)status);
        atomic_store(&worker->last_progress_ns, clock_now_ns(CLOCK_MONOTONIC));
        if (status != TXN_OK) {
            app_log("WARN", "%s sample %" PRIu64 " status=%s",
                    worker_sensor_name(worker), worker->sequence,
                    transaction_status_name(status));
        }
        if (worker->once) {
            break;
        }
        update_next_deadline(&next_deadline, interval_ns);
        if (status == TXN_OPEN_ERROR || status == TXN_IO_ERROR) {
            uint64_t reconnect_deadline = clock_now_ns(CLOCK_MONOTONIC) +
                                          (uint64_t)worker->config->reconnect_delay_ms * 1000000ULL;
            if (next_deadline < reconnect_deadline) {
                next_deadline = reconnect_deadline;
            }
        }
        sleep_until(worker, next_deadline);
    }
    serial_port_close(&port);
    atomic_store(&worker->running, false);
    app_log("INFO", "%s reader stopped", worker_sensor_name(worker));
    return NULL;
}

reader_worker_t *reader_worker_create(reader_kind_t kind,
                                      const reader_config_t *config,
                                      const char *session_id,
                                      storage_logger_t *raw_logger,
                                      storage_logger_t *health_logger,
                                      volatile sig_atomic_t *stop_flag,
                                      bool once) {
    reader_worker_t *worker = calloc(1U, sizeof(*worker));

    if (worker == NULL) {
        return NULL;
    }
    worker->kind = kind;
    worker->config = config;
    worker->raw_logger = raw_logger;
    worker->health_logger = health_logger;
    worker->stop_flag = stop_flag;
    worker->once = once;
    atomic_init(&worker->running, false);
    atomic_init(&worker->last_progress_ns, 0U);
    atomic_init(&worker->last_status, (int)TXN_TIMEOUT);
    if (copy_string(worker->session_id, sizeof(worker->session_id), session_id) != 0) {
        free(worker);
        return NULL;
    }
    return worker;
}

int reader_worker_start(reader_worker_t *worker) {
    if (pthread_create(&worker->thread, NULL, reader_main, worker) != 0) {
        errno = EAGAIN;
        return -1;
    }
    worker->thread_started = true;
    return 0;
}

int reader_worker_join(reader_worker_t *worker) {
    if (worker == NULL) {
        return 0;
    }
    if (!worker->thread_started) {
        return 0;
    }
    if (pthread_join(worker->thread, NULL) != 0) {
        errno = EINVAL;
        return -1;
    }
    worker->thread_started = false;
    return 0;
}

void reader_worker_destroy(reader_worker_t *worker) {
    free(worker);
}

bool reader_worker_is_running(const reader_worker_t *worker) {
    return atomic_load(&worker->running);
}

uint64_t reader_worker_last_progress_ns(const reader_worker_t *worker) {
    return atomic_load(&worker->last_progress_ns);
}

transaction_status_t reader_worker_last_status(const reader_worker_t *worker) {
    return (transaction_status_t)atomic_load(&worker->last_status);
}
