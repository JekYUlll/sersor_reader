#ifndef AWS_READER_STORAGE_H
#define AWS_READER_STORAGE_H

#include "config.h"

#include <stdint.h>

typedef struct storage_manager storage_manager_t;
typedef struct storage_logger storage_logger_t;

typedef enum {
    STORAGE_WRITE_OK = 0,
    STORAGE_WRITE_LOW_SPACE = 1,
    STORAGE_WRITE_ERROR = -1
} storage_write_result_t;

int storage_mountpoint_is_mounted(const char *mountpoint);
storage_manager_t *storage_manager_create(const reader_config_t *config,
                                          const char *session_id);
void storage_manager_destroy(storage_manager_t *manager);

storage_logger_t *storage_logger_create(storage_manager_t *manager,
                                        const char *category,
                                        const char *name);
storage_write_result_t storage_logger_write(storage_logger_t *logger,
                                            const char *json_line,
                                            uint64_t monotonic_ns);
void storage_logger_destroy(storage_logger_t *logger);

#endif
