#ifndef AWS_READER_CONFIG_H
#define AWS_READER_CONFIG_H

#include <limits.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    bool enabled;
    char device[PATH_MAX];
    int baud;
    unsigned int interval_ms;
    unsigned int timeout_ms;
    unsigned int quiet_ms;
    size_t max_response_bytes;
    int direction_gpio;
} p2_config_t;

typedef struct {
    bool enabled;
    char device[PATH_MAX];
    int baud;
    unsigned int interval_ms;
    unsigned int timeout_ms;
    unsigned int slave;
    unsigned int start_register;
    unsigned int register_count;
    int direction_gpio;
} modbus_config_t;

typedef struct {
    char data_dir[PATH_MAX];
    char required_mountpoint[PATH_MAX];
    unsigned int rotate_minutes;
    unsigned int sync_interval_sec;
    uint64_t min_free_mb;
    bool compression_enabled;
    unsigned int reconnect_delay_ms;
    p2_config_t p2;
    modbus_config_t modbus;
} reader_config_t;

void config_set_defaults(reader_config_t *config);
int config_load_file(const char *path, reader_config_t *config,
                     char *error, size_t error_size);
int config_validate(const reader_config_t *config, char *error, size_t error_size);

#endif
