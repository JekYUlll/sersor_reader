#include "config.h"

#include "util.h"

#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void set_error(char *error, size_t error_size, const char *format, ...) {
    va_list args;

    if (error == NULL || error_size == 0U) {
        return;
    }
    va_start(args, format);
    (void)vsnprintf(error, error_size, format, args);
    va_end(args);
}

void config_set_defaults(reader_config_t *config) {
    memset(config, 0, sizeof(*config));
    (void)copy_string(config->data_dir, sizeof(config->data_dir), "/data/aws");
    (void)copy_string(config->required_mountpoint, sizeof(config->required_mountpoint), "/data");
    config->rotate_minutes = 60U;
    config->sync_interval_sec = 60U;
    config->min_free_mb = 1024U;
    config->compression_enabled = true;
    config->reconnect_delay_ms = 2000U;

    config->p2.enabled = true;
    (void)copy_string(config->p2.device, sizeof(config->p2.device),
                      "/dev/serial/by-id/usb-1a86_USB2.0-Serial-if00-port0");
    config->p2.baud = 9600;
    config->p2.interval_ms = 10000U;
    config->p2.timeout_ms = 7500U;
    config->p2.quiet_ms = 350U;
    config->p2.max_response_bytes = 65536U;
    config->p2.direction_gpio = -1;

    config->modbus.enabled = true;
    (void)copy_string(config->modbus.device, sizeof(config->modbus.device),
                      "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0");
    config->modbus.baud = 19200;
    config->modbus.interval_ms = 10000U;
    config->modbus.timeout_ms = 1500U;
    config->modbus.slave = 1U;
    config->modbus.start_register = 0U;
    config->modbus.register_count = 42U;
    config->modbus.direction_gpio = -1;
}

static char *trim(char *value) {
    char *end;

    while (isspace((unsigned char)*value) != 0) {
        ++value;
    }
    if (*value == '\0') {
        return value;
    }
    end = value + strlen(value) - 1;
    while (end > value && isspace((unsigned char)*end) != 0) {
        *end = '\0';
        --end;
    }
    return value;
}

static int parse_bool(const char *value, bool *output) {
    if (strcmp(value, "true") == 0 || strcmp(value, "yes") == 0 || strcmp(value, "1") == 0) {
        *output = true;
        return 0;
    }
    if (strcmp(value, "false") == 0 || strcmp(value, "no") == 0 || strcmp(value, "0") == 0) {
        *output = false;
        return 0;
    }
    return -1;
}

static int parse_u64(const char *value, uint64_t *output) {
    char *end = NULL;
    unsigned long long parsed;

    errno = 0;
    parsed = strtoull(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0') {
        return -1;
    }
    *output = (uint64_t)parsed;
    return 0;
}

static int parse_uint(const char *value, unsigned int *output) {
    uint64_t parsed;
    if (parse_u64(value, &parsed) != 0 || parsed > UINT_MAX) {
        return -1;
    }
    *output = (unsigned int)parsed;
    return 0;
}

static int parse_int(const char *value, int *output) {
    char *end = NULL;
    long parsed;

    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed < INT_MIN || parsed > INT_MAX) {
        return -1;
    }
    *output = (int)parsed;
    return 0;
}

static int assign_value(reader_config_t *config, const char *key, const char *value) {
#define ASSIGN_STRING(name, field) \
    if (strcmp(key, name) == 0) { \
        return copy_string(field, sizeof(field), value); \
    }
#define ASSIGN_BOOL(name, field) \
    if (strcmp(key, name) == 0) { \
        return parse_bool(value, &(field)); \
    }
#define ASSIGN_UINT(name, field) \
    if (strcmp(key, name) == 0) { \
        return parse_uint(value, &(field)); \
    }
#define ASSIGN_INT(name, field) \
    if (strcmp(key, name) == 0) { \
        return parse_int(value, &(field)); \
    }

    ASSIGN_STRING("data_dir", config->data_dir)
    ASSIGN_STRING("required_mountpoint", config->required_mountpoint)
    ASSIGN_UINT("rotate_minutes", config->rotate_minutes)
    ASSIGN_UINT("sync_interval_sec", config->sync_interval_sec)
    if (strcmp(key, "min_free_mb") == 0) {
        return parse_u64(value, &config->min_free_mb);
    }
    ASSIGN_BOOL("compression_enabled", config->compression_enabled)
    ASSIGN_UINT("reconnect_delay_ms", config->reconnect_delay_ms)

    ASSIGN_BOOL("p2_enabled", config->p2.enabled)
    ASSIGN_STRING("p2_device", config->p2.device)
    ASSIGN_INT("p2_baud", config->p2.baud)
    ASSIGN_UINT("p2_interval_ms", config->p2.interval_ms)
    ASSIGN_UINT("p2_timeout_ms", config->p2.timeout_ms)
    ASSIGN_UINT("p2_quiet_ms", config->p2.quiet_ms)
    if (strcmp(key, "p2_max_response_bytes") == 0) {
        uint64_t parsed;
        if (parse_u64(value, &parsed) != 0 || parsed > SIZE_MAX) {
            return -1;
        }
        config->p2.max_response_bytes = (size_t)parsed;
        return 0;
    }
    ASSIGN_INT("p2_direction_gpio", config->p2.direction_gpio)

    ASSIGN_BOOL("modbus_enabled", config->modbus.enabled)
    ASSIGN_STRING("modbus_device", config->modbus.device)
    ASSIGN_INT("modbus_baud", config->modbus.baud)
    ASSIGN_UINT("modbus_interval_ms", config->modbus.interval_ms)
    ASSIGN_UINT("modbus_timeout_ms", config->modbus.timeout_ms)
    ASSIGN_UINT("modbus_slave", config->modbus.slave)
    ASSIGN_UINT("modbus_start_register", config->modbus.start_register)
    ASSIGN_UINT("modbus_register_count", config->modbus.register_count)
    ASSIGN_INT("modbus_direction_gpio", config->modbus.direction_gpio)

#undef ASSIGN_STRING
#undef ASSIGN_BOOL
#undef ASSIGN_UINT
#undef ASSIGN_INT
    errno = ENOENT;
    return -1;
}

int config_load_file(const char *path, reader_config_t *config,
                     char *error, size_t error_size) {
    FILE *file;
    char *line = NULL;
    size_t capacity = 0;
    ssize_t length;
    unsigned long line_number = 0;
    int result = -1;

    file = fopen(path, "r");
    if (file == NULL) {
        set_error(error, error_size, "cannot open %s: %s", path, strerror(errno));
        return -1;
    }
    while ((length = getline(&line, &capacity, file)) >= 0) {
        char *key;
        char *value;
        char *equals;

        ++line_number;
        if (length > 0 && line[(size_t)length - 1U] == '\n') {
            line[(size_t)length - 1U] = '\0';
        }
        key = trim(line);
        if (*key == '\0' || *key == '#') {
            continue;
        }
        equals = strchr(key, '=');
        if (equals == NULL) {
            set_error(error, error_size, "%s:%lu: expected key=value", path, line_number);
            goto done;
        }
        *equals = '\0';
        value = trim(equals + 1);
        key = trim(key);
        errno = 0;
        if (*key == '\0' || assign_value(config, key, value) != 0) {
            if (errno == ENOENT) {
                set_error(error, error_size, "%s:%lu: unknown key '%s'", path, line_number, key);
            } else {
                set_error(error, error_size, "%s:%lu: invalid value for '%s'", path,
                          line_number, key);
            }
            goto done;
        }
    }
    if (ferror(file) != 0) {
        set_error(error, error_size, "cannot read %s: %s", path, strerror(errno));
        goto done;
    }
    result = config_validate(config, error, error_size);

done:
    free(line);
    (void)fclose(file);
    return result;
}

static bool supported_baud(int baud) {
    return baud == 1200 || baud == 2400 || baud == 4800 || baud == 9600 ||
           baud == 19200 || baud == 38400 || baud == 57600 || baud == 115200;
}

int config_validate(const reader_config_t *config, char *error, size_t error_size) {
    if (config->data_dir[0] != '/') {
        set_error(error, error_size, "data_dir must be an absolute path");
        return -1;
    }
    if (config->required_mountpoint[0] != '\0' && config->required_mountpoint[0] != '/') {
        set_error(error, error_size, "required_mountpoint must be empty or absolute");
        return -1;
    }
    if (config->rotate_minutes == 0U || config->rotate_minutes > 10080U) {
        set_error(error, error_size, "rotate_minutes must be in 1..10080");
        return -1;
    }
    if (config->sync_interval_sec == 0U || config->sync_interval_sec > 3600U) {
        set_error(error, error_size, "sync_interval_sec must be in 1..3600");
        return -1;
    }
    if (config->min_free_mb > UINT64_MAX / (1024U * 1024U)) {
        set_error(error, error_size, "min_free_mb is too large");
        return -1;
    }
    if (config->reconnect_delay_ms < 100U || config->reconnect_delay_ms > 600000U) {
        set_error(error, error_size, "reconnect_delay_ms must be in 100..600000");
        return -1;
    }
    if (!config->p2.enabled && !config->modbus.enabled) {
        set_error(error, error_size, "at least one sensor must be enabled");
        return -1;
    }
    if (config->p2.enabled) {
        if (config->p2.device[0] != '/' || !supported_baud(config->p2.baud)) {
            set_error(error, error_size, "p2 device or baud is invalid");
            return -1;
        }
        if (config->p2.interval_ms < 100U || config->p2.timeout_ms < 100U ||
            config->p2.quiet_ms < 10U || config->p2.quiet_ms > config->p2.timeout_ms) {
            set_error(error, error_size, "p2 interval/timeout/quiet values are invalid");
            return -1;
        }
        if (config->p2.max_response_bytes < 512U || config->p2.max_response_bytes > 4U * 1024U * 1024U) {
            set_error(error, error_size, "p2_max_response_bytes must be in 512..4194304");
            return -1;
        }
        if (config->p2.direction_gpio < -1) {
            set_error(error, error_size, "p2_direction_gpio must be -1 or nonnegative");
            return -1;
        }
    }
    if (config->modbus.enabled) {
        if (config->modbus.device[0] != '/' || !supported_baud(config->modbus.baud)) {
            set_error(error, error_size, "modbus device or baud is invalid");
            return -1;
        }
        if (config->modbus.interval_ms < 100U || config->modbus.timeout_ms < 100U) {
            set_error(error, error_size, "modbus interval/timeout values are invalid");
            return -1;
        }
        if (config->modbus.slave == 0U || config->modbus.slave > 247U ||
            config->modbus.register_count == 0U || config->modbus.register_count > 125U ||
            config->modbus.start_register > 65535U ||
            config->modbus.start_register + config->modbus.register_count > 65536U) {
            set_error(error, error_size, "modbus address/register values are invalid");
            return -1;
        }
        if (config->modbus.direction_gpio < -1) {
            set_error(error, error_size, "modbus_direction_gpio must be -1 or nonnegative");
            return -1;
        }
    }
    if (error != NULL && error_size > 0U) {
        error[0] = '\0';
    }
    return 0;
}
