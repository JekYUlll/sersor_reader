#include "protocol.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void append_crc(uint8_t *frame, size_t payload_length) {
    uint16_t crc = modbus_crc16(frame, payload_length);
    frame[payload_length] = (uint8_t)(crc & 0xffU);
    frame[payload_length + 1U] = (uint8_t)(crc >> 8U);
}

int main(void) {
    uint8_t request[8];
    const uint8_t expected_request[] = {0x01, 0x03, 0x00, 0x00, 0x00, 0x2a, 0xc4, 0x15};
    uint8_t response[89] = {0};
    uint8_t exception[5] = {0x01, 0x83, 0x02, 0x00, 0x00};
    const uint8_t cdab_12_5[] = {0x00, 0x00, 0x41, 0x48};
    modbus_validation_t validation;

    assert(modbus_build_read_request(request, sizeof(request), 1U, 0U, 42U) == 8U);
    assert(memcmp(request, expected_request, sizeof(request)) == 0);

    response[0] = 0x01;
    response[1] = 0x03;
    response[2] = 84U;
    memcpy(response + 3, cdab_12_5, sizeof(cdab_12_5));
    append_crc(response, sizeof(response) - 2U);
    validation = modbus_validate_read_response(response, sizeof(response), 1U, 42U);
    assert(validation.status == TXN_OK);
    assert(fabsf(modbus_decode_float_cdab(response + 3) - 12.5F) < 0.0001F);

    response[20] ^= 0x01U;
    validation = modbus_validate_read_response(response, sizeof(response), 1U, 42U);
    assert(validation.status == TXN_CRC_ERROR);

    append_crc(exception, 3U);
    validation = modbus_validate_read_response(exception, sizeof(exception), 1U, 42U);
    assert(validation.status == TXN_MODBUS_EXCEPTION);
    assert(validation.exception_code == 2);

    assert(bytes_contains((const uint8_t *)"abc TYP OP4A xyz", 16U,
                          (const uint8_t *)"TYP OP4A", 8U));
    assert(!bytes_contains((const uint8_t *)"abc", 3U,
                           (const uint8_t *)"OP4A", 4U));
    assert(strcmp(transaction_status_name(TXN_INTERRUPTED), "interrupted") == 0);
    (void)puts("test_protocol: OK");
    return 0;
}
