#ifndef AWS_READER_PROTOCOL_H
#define AWS_READER_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    TXN_OK = 0,
    TXN_OPEN_ERROR,
    TXN_IO_ERROR,
    TXN_TIMEOUT,
    TXN_SHORT_FRAME,
    TXN_CRC_ERROR,
    TXN_PROTOCOL_ERROR,
    TXN_MODBUS_EXCEPTION,
    TXN_TRUNCATED,
    TXN_INTERRUPTED
} transaction_status_t;

typedef struct {
    transaction_status_t status;
    int exception_code;
    size_t expected_length;
} modbus_validation_t;

const char *transaction_status_name(transaction_status_t status);
uint16_t modbus_crc16(const uint8_t *data, size_t length);
size_t modbus_build_read_request(uint8_t *output, size_t capacity,
                                  uint8_t slave, uint16_t start_register,
                                  uint16_t register_count);
modbus_validation_t modbus_validate_read_response(const uint8_t *response,
                                                   size_t response_length,
                                                   uint8_t slave,
                                                   uint16_t register_count);
float modbus_decode_float_cdab(const uint8_t bytes[4]);
bool bytes_contains(const uint8_t *haystack, size_t haystack_length,
                    const uint8_t *needle, size_t needle_length);

#endif
