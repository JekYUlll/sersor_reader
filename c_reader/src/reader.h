#ifndef AWS_READER_READER_H
#define AWS_READER_READER_H

#include "config.h"
#include "protocol.h"
#include "storage.h"

#include <signal.h>
#include <stdbool.h>
#include <stdint.h>

typedef enum {
    READER_PARSIVEL2 = 0,
    READER_MODBUS = 1
} reader_kind_t;

typedef struct reader_worker reader_worker_t;

reader_worker_t *reader_worker_create(reader_kind_t kind,
                                      const reader_config_t *config,
                                      const char *session_id,
                                      storage_logger_t *raw_logger,
                                      storage_logger_t *health_logger,
                                      volatile sig_atomic_t *stop_flag,
                                      bool once);
int reader_worker_start(reader_worker_t *worker);
int reader_worker_join(reader_worker_t *worker);
void reader_worker_destroy(reader_worker_t *worker);
bool reader_worker_is_running(const reader_worker_t *worker);
uint64_t reader_worker_last_progress_ns(const reader_worker_t *worker);
transaction_status_t reader_worker_last_status(const reader_worker_t *worker);

#endif
