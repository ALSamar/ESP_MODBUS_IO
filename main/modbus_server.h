#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MODBUS_RTU_MAX_ADU_SIZE 256U

uint16_t modbus_rtu_crc16(const uint8_t *data, size_t length);
bool modbus_rtu_crc_valid(const uint8_t *frame, size_t length);

/*
 * Returns zero while more bytes are needed, SIZE_MAX for an impossible length,
 * or the complete request length inferred from the function code.
 */
size_t modbus_server_expected_request_length(const uint8_t *data, size_t length);

/*
 * Processes one complete Modbus RTU ADU. Frames for another slave and broadcast
 * writes produce no response and return ESP_OK.
 */
esp_err_t modbus_server_process(const uint8_t *request, size_t request_length,
                                uint8_t *response, size_t response_capacity,
                                size_t *response_length);

#ifdef __cplusplus
}
#endif
