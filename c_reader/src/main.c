#include "config.h"
#include "reader.h"
#include "storage.h"
#include "systemd_notify.h"
#include "util.h"

#include <errno.h>
#include <limits.h>
#include <locale.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define AWS_READER_VERSION "0.1.0"

static volatile sig_atomic_t stop_requested;

static void handle_signal(int signal_number) {
    (void)signal_number;
    stop_requested = 1;
}

static int install_signal_handlers(void) {
    struct sigaction action;

    memset(&action, 0, sizeof(action));
    action.sa_handler = handle_signal;
    (void)sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) != 0 ||
        sigaction(SIGTERM, &action, NULL) != 0 ||
        sigaction(SIGHUP, &action, NULL) != 0) {
        return -1;
    }
    action.sa_handler = SIG_IGN;
    return sigaction(SIGPIPE, &action, NULL);
}

static void print_usage(FILE *stream, const char *program) {
    (void)fprintf(stream,
                  "Usage: %s [--config PATH] [--once] [--validate-config] [--version]\n",
                  program);
}

static int read_boot_id(char *output, size_t output_size) {
    FILE *file = fopen("/proc/sys/kernel/random/boot_id", "r");
    size_t length;

    if (file == NULL) {
        return -1;
    }
    if (fgets(output, (int)output_size, file) == NULL) {
        (void)fclose(file);
        return -1;
    }
    (void)fclose(file);
    length = strlen(output);
    while (length > 0U && (output[length - 1U] == '\n' || output[length - 1U] == '\r')) {
        output[--length] = '\0';
    }
    return length == 0U ? -1 : 0;
}

static void build_session_id(char *output, size_t output_size) {
    char boot_id[64] = "unknown-boot";
    uint64_t monotonic_ns = clock_now_ns(CLOCK_MONOTONIC);

    (void)read_boot_id(boot_id, sizeof(boot_id));
    (void)snprintf(output, output_size, "%s-%ld-%llu", boot_id, (long)getpid(),
                   (unsigned long long)(monotonic_ns / 1000000ULL));
}

static bool worker_is_healthy(reader_worker_t *worker, uint64_t now_ns) {
    uint64_t progress;

    if (worker == NULL) {
        return true;
    }
    progress = reader_worker_last_progress_ns(worker);
    if (progress == 0U) {
        return true;
    }
    return reader_worker_is_running(worker) && now_ns - progress < 45000000000ULL;
}

static void monitor_workers(reader_worker_t *p2_worker, reader_worker_t *modbus_worker,
                            bool once, int *runtime_error) {
    if (once) {
        if (reader_worker_join(p2_worker) != 0 || reader_worker_join(modbus_worker) != 0) {
            *runtime_error = 1;
        }
        return;
    }

    while (stop_requested == 0) {
        struct timespec delay = {.tv_sec = 5, .tv_nsec = 0};
        uint64_t now_ns;
        bool healthy;

        while (nanosleep(&delay, &delay) != 0 && errno == EINTR && stop_requested == 0) {
        }
        now_ns = clock_now_ns(CLOCK_MONOTONIC);
        healthy = worker_is_healthy(p2_worker, now_ns) &&
                  worker_is_healthy(modbus_worker, now_ns);
        if (!healthy) {
            app_log("ERROR", "reader worker stopped or made no progress for 45 seconds");
            *runtime_error = 1;
            stop_requested = 1;
            break;
        }
        if (systemd_notify_message("WATCHDOG=1") != 0) {
            app_log("WARN", "cannot notify systemd watchdog: %s", strerror(errno));
        }
    }
    if (reader_worker_join(p2_worker) != 0 || reader_worker_join(modbus_worker) != 0) {
        *runtime_error = 1;
    }
}

int main(int argc, char **argv) {
    const char *config_path = "/etc/aws-reader.conf";
    bool once = false;
    bool validate_only = false;
    reader_config_t config;
    char config_error[512];
    char session_id[128];
    storage_manager_t *storage = NULL;
    storage_logger_t *p2_raw = NULL;
    storage_logger_t *modbus_raw = NULL;
    storage_logger_t *health = NULL;
    reader_worker_t *p2_worker = NULL;
    reader_worker_t *modbus_worker = NULL;
    int runtime_error = 0;
    int index;
    int exit_code = EXIT_FAILURE;

    for (index = 1; index < argc; ++index) {
        if (strcmp(argv[index], "--config") == 0 && index + 1 < argc) {
            config_path = argv[++index];
        } else if (strcmp(argv[index], "--once") == 0) {
            once = true;
        } else if (strcmp(argv[index], "--validate-config") == 0) {
            validate_only = true;
        } else if (strcmp(argv[index], "--version") == 0) {
            (void)printf("aws-reader %s\n", AWS_READER_VERSION);
            return EXIT_SUCCESS;
        } else if (strcmp(argv[index], "--help") == 0 || strcmp(argv[index], "-h") == 0) {
            print_usage(stdout, argv[0]);
            return EXIT_SUCCESS;
        } else {
            print_usage(stderr, argv[0]);
            return EXIT_FAILURE;
        }
    }

    (void)setlocale(LC_NUMERIC, "C");
    (void)umask(0027);
    config_set_defaults(&config);
    if (config_load_file(config_path, &config, config_error, sizeof(config_error)) != 0) {
        app_log("ERROR", "%s", config_error);
        return EXIT_FAILURE;
    }
    if (validate_only) {
        (void)printf("configuration valid: %s\n", config_path);
        return EXIT_SUCCESS;
    }
    if (install_signal_handlers() != 0) {
        app_log("ERROR", "cannot install signal handlers: %s", strerror(errno));
        return EXIT_FAILURE;
    }
    if (config.required_mountpoint[0] != '\0') {
        int mounted = storage_mountpoint_is_mounted(config.required_mountpoint);
        if (mounted <= 0) {
            app_log("ERROR", "required mountpoint is not mounted: %s",
                    config.required_mountpoint);
            return EXIT_FAILURE;
        }
    }

    build_session_id(session_id, sizeof(session_id));
    storage = storage_manager_create(&config, session_id);
    if (storage == NULL) {
        app_log("ERROR", "cannot initialize storage at %s: %s",
                config.data_dir, strerror(errno));
        goto cleanup;
    }
    health = storage_logger_create(storage, "health", "sensors");
    if (health == NULL) {
        app_log("ERROR", "cannot create health logger: %s", strerror(errno));
        goto cleanup;
    }
    if (config.p2.enabled) {
        p2_raw = storage_logger_create(storage, "raw", "parsivel2");
        if (p2_raw == NULL) {
            goto cleanup;
        }
        p2_worker = reader_worker_create(READER_PARSIVEL2, &config, session_id,
                                         p2_raw, health, &stop_requested, once);
        if (p2_worker == NULL || reader_worker_start(p2_worker) != 0) {
            app_log("ERROR", "cannot start p2 reader: %s", strerror(errno));
            goto cleanup;
        }
    }
    if (config.modbus.enabled) {
        modbus_raw = storage_logger_create(storage, "raw", "modbus");
        if (modbus_raw == NULL) {
            goto cleanup;
        }
        modbus_worker = reader_worker_create(READER_MODBUS, &config, session_id,
                                             modbus_raw, health, &stop_requested, once);
        if (modbus_worker == NULL || reader_worker_start(modbus_worker) != 0) {
            app_log("ERROR", "cannot start Modbus reader: %s", strerror(errno));
            goto cleanup;
        }
    }
    if (systemd_notify_message("READY=1\nSTATUS=Acquiring raw sensor data") != 0) {
        app_log("WARN", "cannot notify systemd readiness: %s", strerror(errno));
    }
    monitor_workers(p2_worker, modbus_worker, once, &runtime_error);
    if (once && ((p2_worker != NULL && reader_worker_last_status(p2_worker) != TXN_OK) ||
                 (modbus_worker != NULL && reader_worker_last_status(modbus_worker) != TXN_OK))) {
        runtime_error = 1;
    }
    exit_code = runtime_error == 0 ? EXIT_SUCCESS : EXIT_FAILURE;

cleanup:
    stop_requested = 1;
    (void)systemd_notify_message("STOPPING=1");
    if (p2_worker != NULL) {
        (void)reader_worker_join(p2_worker);
    }
    if (modbus_worker != NULL) {
        (void)reader_worker_join(modbus_worker);
    }
    reader_worker_destroy(p2_worker);
    reader_worker_destroy(modbus_worker);
    storage_logger_destroy(p2_raw);
    storage_logger_destroy(modbus_raw);
    storage_logger_destroy(health);
    storage_manager_destroy(storage);
    return exit_code;
}
