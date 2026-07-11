#include "protocol.h"

#include <string.h>

const char *transaction_status_name(transaction_status_t status) {
    switch (status) {
        case TXN_OK:
            return "ok";
        case TXN_OPEN_ERROR:
            return "open_error";
        case TXN_IO_ERROR:
            return "io_error";
        case TXN_TIMEOUT:
            return "timeout";
        case TXN_SHORT_FRAME:
            return "short_frame";
        case TXN_CRC_ERROR:
            return "crc_error";
        case TXN_PROTOCOL_ERROR:
            return "protocol_error";
        case TXN_MODBUS_EXCEPTION:
            return "modbus_exception";
        case TXN_TRUNCATED:
            return "truncated";
        case TXN_INTERRUPTED:
            return "interrupted";
    }
    return "unknown";
}

uint16_t modbus_crc16(const uint8_t *data, size_t length) {
    uint16_t crc = 0xffffU;
    size_t index;
    unsigned int bit;

    for (index = 0; index < length; ++index) {
        crc ^= data[index];
        for (bit = 0; bit < 8U; ++bit) {
            if ((crc & 1U) != 0U) {
                crc = (uint16_t)((crc >> 1U) ^ 0xa001U);
            } else {
                crc >>= 1U;
            }
        }
    }
    return crc;
}

size_t modbus_build_read_request(uint8_t *output, size_t capacity,
                                  uint8_t slave, uint16_t start_register,
                                  uint16_t register_count) {
    uint16_t crc;

    if (output == NULL || capacity < 8U || register_count == 0U || register_count > 125U) {
        return 0;
    }
    output[0] = slave;
    output[1] = 0x03U;
    output[2] = (uint8_t)(start_register >> 8U);
    output[3] = (uint8_t)(start_register & 0xffU);
    output[4] = (uint8_t)(register_count >> 8U);
    output[5] = (uint8_t)(register_count & 0xffU);
    crc = modbus_crc16(output, 6U);
    output[6] = (uint8_t)(crc & 0xffU);
    output[7] = (uint8_t)(crc >> 8U);
    return 8U;
}

modbus_validation_t modbus_validate_read_response(const uint8_t *response,
                                                   size_t response_length,
                                                   uint8_t slave,
                                                   uint16_t register_count) {
    modbus_validation_t result = {
        .status = TXN_SHORT_FRAME,
        .exception_code = -1,
        .expected_length = (size_t)register_count * 2U + 5U,
    };
    uint16_t expected_crc;
    uint16_t received_crc;

    if (response == NULL || response_length == 0U) {
        result.status = TXN_TIMEOUT;
        return result;
    }
    if (response_length < 5U) {
        return result;
    }
    expected_crc = modbus_crc16(response, response_length - 2U);
    received_crc = (uint16_t)response[response_length - 2U] |
                   (uint16_t)((uint16_t)response[response_length - 1U] << 8U);
    if (expected_crc != received_crc) {
        result.status = TXN_CRC_ERROR;
        return result;
    }
    if (response[0] != slave) {
        result.status = TXN_PROTOCOL_ERROR;
        return result;
    }
    if (response[1] == (uint8_t)(0x03U | 0x80U)) {
        result.status = TXN_MODBUS_EXCEPTION;
        result.exception_code = response[2];
        result.expected_length = 5U;
        return result;
    }
    if (response[1] != 0x03U || response[2] != (uint8_t)(register_count * 2U)) {
        result.status = TXN_PROTOCOL_ERROR;
        return result;
    }
    if (response_length != result.expected_length) {
        result.status = response_length < result.expected_length ? TXN_SHORT_FRAME : TXN_PROTOCOL_ERROR;
        return result;
    }
    result.status = TXN_OK;
    return result;
}

float modbus_decode_float_cdab(const uint8_t bytes[4]) {
    uint8_t ordered[4] = {bytes[2], bytes[3], bytes[0], bytes[1]};
    uint32_t bits = ((uint32_t)ordered[0] << 24U) |
                    ((uint32_t)ordered[1] << 16U) |
                    ((uint32_t)ordered[2] << 8U) |
                    (uint32_t)ordered[3];
    float value;

    memcpy(&value, &bits, sizeof(value));
    return value;
}

bool bytes_contains(const uint8_t *haystack, size_t haystack_length,
                    const uint8_t *needle, size_t needle_length) {
    size_t index;

    if (needle_length == 0U) {
        return true;
    }
    if (haystack == NULL || needle == NULL || needle_length > haystack_length) {
        return false;
    }
    for (index = 0; index <= haystack_length - needle_length; ++index) {
        if (memcmp(haystack + index, needle, needle_length) == 0) {
            return true;
        }
    }
    return false;
}
