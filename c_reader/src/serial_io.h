#ifndef AWS_READER_SERIAL_IO_H
#define AWS_READER_SERIAL_IO_H

#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    int fd;
    int gpio_value_fd;
    int direction_gpio;
    int baud;
    char device[4096];
} serial_port_t;

typedef struct {
    size_t response_length;
    int system_errno;
    bool timed_out;
    bool truncated;
    bool terminated;
} serial_result_t;

void serial_port_reset(serial_port_t *port);
int serial_port_open(serial_port_t *port, const char *device, int baud,
                     int direction_gpio);
void serial_port_close(serial_port_t *port);
int serial_transaction(serial_port_t *port,
                       const uint8_t *request, size_t request_length,
                       uint8_t *response, size_t response_capacity,
                       unsigned int timeout_ms, unsigned int quiet_ms,
                       size_t expected_length, int terminator_byte,
                       const volatile sig_atomic_t *stop_flag,
                       serial_result_t *result);

#endif
