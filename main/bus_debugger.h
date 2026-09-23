#pragma once

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define DEBUGGER_MAX_TRANSFER 128U

/* Vendor Modbus function 0x41. The payload is op-specific; errors are
 * returned as ordinary Modbus exceptions by modbus_server.c. */
enum {
    DBG_I2C_CONFIG = 1,
    DBG_I2C_DISABLE = 2,
    DBG_I2C_SCAN = 3,
    DBG_I2C_TRANSFER = 4,
    DBG_SPI_CONFIG = 5,
    DBG_SPI_DISABLE = 6,
    DBG_SPI_TRANSFER = 7,
    DBG_UART_CONFIG = 8,
    DBG_UART_DISABLE = 9,
    DBG_STATUS = 10,
};

esp_err_t bus_debugger_init(void);
esp_err_t bus_debugger_command(uint8_t op, const uint8_t *payload, size_t payload_length,
                               uint8_t *reply, size_t reply_capacity, size_t *reply_length);
int bus_uart_read(uint8_t *buffer, size_t capacity);
int bus_uart_write(const uint8_t *buffer, size_t length);
