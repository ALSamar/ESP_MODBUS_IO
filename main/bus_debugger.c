#include "bus_debugger.h"

#include <stdbool.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "io_model.h"

#define PIN_UNUSED 0xFFU
#define I2C_TIMEOUT_MS 500
#define I2C_PROBE_TIMEOUT_MS 5

static i2c_master_bus_handle_t s_i2c;
static spi_device_handle_t s_spi;
static bool s_uart;
static SemaphoreHandle_t s_uart_lock;
static uint8_t s_i2c_pins[2] = {PIN_UNUSED, PIN_UNUSED};
static uint8_t s_spi_pins[4] = {PIN_UNUSED, PIN_UNUSED, PIN_UNUSED, PIN_UNUSED};
static uint8_t s_uart_pins[2] = {PIN_UNUSED, PIN_UNUSED};
static uint32_t s_i2c_hz, s_spi_hz, s_uart_baud;
static uint8_t s_spi_mode, s_uart_data, s_uart_parity, s_uart_stop;

static uint32_t be32(const uint8_t *value)
{
    return ((uint32_t)value[0] << 24) | ((uint32_t)value[1] << 16)
           | ((uint32_t)value[2] << 8) | value[3];
}

static void put32(uint8_t *out, uint32_t value)
{
    out[0] = (uint8_t)(value >> 24);
    out[1] = (uint8_t)(value >> 16);
    out[2] = (uint8_t)(value >> 8);
    out[3] = (uint8_t)value;
}

/* All arguments are digital channel numbers, not raw GPIO numbers. A 0xFF
 * MISO or CS omits that optional SPI signal. Reserve all before touching a
 * peripheral; if any pin is busy, restore every reservation made here. */
static esp_err_t reserve(const uint8_t *pins, const bool *output, size_t count,
                         io_mode_t owner, int *gpio)
{
    for (size_t i = 0; i < count; ++i) {
        gpio[i] = -1;
        if (pins[i] == PIN_UNUSED) {
            continue;
        }
        for (size_t j = 0; j < i; ++j) {
            if (pins[j] == pins[i]) {
                for (size_t k = 0; k < i; ++k) {
                    if (gpio[k] >= 0) {
                        (void)io_model_release_pin(pins[k], owner);
                    }
                }
                return ESP_ERR_INVALID_ARG;
            }
        }
        uint16_t number;
        esp_err_t error = io_model_get_gpio(pins[i], &number);
        if (error == ESP_OK) {
            error = io_model_reserve_pin(pins[i], owner, output[i]);
        }
        if (error != ESP_OK) {
            for (size_t j = 0; j < i; ++j) {
                if (gpio[j] >= 0) {
                    (void)io_model_release_pin(pins[j], owner);
                }
            }
            return error;
        }
        gpio[i] = number;
    }
    return ESP_OK;
}

static void release(const uint8_t *pins, size_t count, io_mode_t owner)
{
    for (size_t i = 0; i < count; ++i) {
        if (pins[i] != PIN_UNUSED) {
            (void)io_model_release_pin(pins[i], owner);
        }
    }
}

static esp_err_t i2c_config(const uint8_t *p, size_t n)
{
    if (n != 6 || p[0] == PIN_UNUSED || p[1] == PIN_UNUSED) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_i2c) {
        return ESP_ERR_INVALID_STATE;
    }
    const uint32_t hz = be32(&p[2]);
    if (hz < 10000 || hz > 400000) {
        return ESP_ERR_INVALID_ARG;
    }
    const bool output[2] = {true, true};
    int gpio[2];
    esp_err_t error = reserve(p, output, 2, IO_MODE_I2C, gpio);
    if (error != ESP_OK) {
        return error;
    }
    const i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = (gpio_num_t)gpio[0],
        .scl_io_num = (gpio_num_t)gpio[1],
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    error = i2c_new_master_bus(&cfg, &s_i2c);
    if (error != ESP_OK) {
        release(p, 2, IO_MODE_I2C);
        return error;
    }
    memcpy(s_i2c_pins, p, 2);
    s_i2c_hz = hz;
    return ESP_OK;
}

static esp_err_t i2c_disable(void)
{
    if (!s_i2c) {
        return ESP_OK;
    }
    esp_err_t error = i2c_del_master_bus(s_i2c);
    if (error != ESP_OK) {
        return error;
    }
    s_i2c = NULL;
    release(s_i2c_pins, 2, IO_MODE_I2C);
    memset(s_i2c_pins, PIN_UNUSED, sizeof(s_i2c_pins));
    s_i2c_hz = 0;
    return ESP_OK;
}

static esp_err_t i2c_transfer(const uint8_t *p, size_t n, uint8_t *reply,
                              size_t capacity, size_t *reply_length)
{
    if (!s_i2c) {
        return ESP_ERR_INVALID_STATE;
    }
    if (n < 3 || p[0] < 0x08 || p[0] > 0x77 || p[1] > DEBUGGER_MAX_TRANSFER
        || p[2] > DEBUGGER_MAX_TRANSFER || (p[1] == 0 && p[2] == 0)
        || n != (size_t)p[1] + 3 || p[2] > capacity) {
        return ESP_ERR_INVALID_ARG;
    }
    const i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = p[0],
        .scl_speed_hz = s_i2c_hz,
    };
    i2c_master_dev_handle_t device;
    esp_err_t error = i2c_master_bus_add_device(s_i2c, &cfg, &device);
    if (error != ESP_OK) {
        return error;
    }
    if (p[1] && p[2]) {
        error = i2c_master_transmit_receive(device, &p[3], p[1], reply,
                                            p[2], I2C_TIMEOUT_MS);
    } else if (p[1]) {
        error = i2c_master_transmit(device, &p[3], p[1], I2C_TIMEOUT_MS);
    } else {
        error = i2c_master_receive(device, reply, p[2], I2C_TIMEOUT_MS);
    }
    const esp_err_t remove_error = i2c_master_bus_rm_device(device);
    if (error == ESP_OK) {
        error = remove_error;
    }
    if (error == ESP_OK) {
        *reply_length = p[2];
    }
    return error;
}

static esp_err_t i2c_scan(uint8_t *reply, size_t capacity, size_t *reply_length)
{
    if (!s_i2c) {
        return ESP_ERR_INVALID_STATE;
    }
    if (capacity < 16) {
        return ESP_ERR_NO_MEM;
    }
    memset(reply, 0, 16);
    for (uint8_t address = 0x08; address <= 0x77; ++address) {
        const esp_err_t error = i2c_master_probe(s_i2c, address, I2C_PROBE_TIMEOUT_MS);
        if (error == ESP_OK) {
            reply[address / 8] |= (uint8_t)(1U << (address % 8));
        } else if (error != ESP_ERR_NOT_FOUND && error != ESP_ERR_TIMEOUT) {
            return error;
        }
    }
    *reply_length = 16;
    return ESP_OK;
}

static esp_err_t spi_config(const uint8_t *p, size_t n)
{
    if (n != 9) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_spi) {
        return ESP_ERR_INVALID_STATE;
    }
    const uint32_t hz = be32(&p[5]);
    if (p[0] == PIN_UNUSED || p[1] == PIN_UNUSED || p[4] > 3
        || hz < 10000 || hz > 10000000) {
        return ESP_ERR_INVALID_ARG;
    }
    const bool output[4] = {true, true, false, true};
    int gpio[4];
    esp_err_t error = reserve(p, output, 4, IO_MODE_SPI, gpio);
    if (error != ESP_OK) {
        return error;
    }
    const spi_bus_config_t bus = {
        .sclk_io_num = gpio[0], .mosi_io_num = gpio[1],
        .miso_io_num = gpio[2], .quadwp_io_num = -1, .quadhd_io_num = -1,
        .max_transfer_sz = DEBUGGER_MAX_TRANSFER,
    };
    error = spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_DISABLED);
    if (error != ESP_OK) {
        release(p, 4, IO_MODE_SPI);
        return error;
    }
    const spi_device_interface_config_t device = {
        .clock_speed_hz = (int)hz, .mode = p[4],
        .spics_io_num = gpio[3], .queue_size = 1,
    };
    error = spi_bus_add_device(SPI2_HOST, &device, &s_spi);
    if (error != ESP_OK) {
        (void)spi_bus_free(SPI2_HOST);
        release(p, 4, IO_MODE_SPI);
        return error;
    }
    memcpy(s_spi_pins, p, 4);
    s_spi_mode = p[4];
    s_spi_hz = hz;
    return ESP_OK;
}

static esp_err_t spi_disable(void)
{
    if (!s_spi) {
        return ESP_OK;
    }
    esp_err_t error = spi_bus_remove_device(s_spi);
    if (error != ESP_OK) {
        return error;
    }
    s_spi = NULL;
    error = spi_bus_free(SPI2_HOST);
    release(s_spi_pins, 4, IO_MODE_SPI);
    memset(s_spi_pins, PIN_UNUSED, sizeof(s_spi_pins));
    s_spi_hz = 0;
    s_spi_mode = 0;
    return error;
}

static esp_err_t spi_transfer(const uint8_t *p, size_t n, uint8_t *reply,
                              size_t capacity, size_t *reply_length)
{
    if (!s_spi) {
        return ESP_ERR_INVALID_STATE;
    }
    if (n < 2 || p[0] == 0 || p[0] > DEBUGGER_MAX_TRANSFER
        || n != (size_t)p[0] + 1 || p[0] > capacity) {
        return ESP_ERR_INVALID_ARG;
    }
    spi_transaction_t transfer = {
        .length = (size_t)p[0] * 8,
        .tx_buffer = &p[1], .rx_buffer = reply,
    };
    esp_err_t error = spi_device_transmit(s_spi, &transfer);
    if (error == ESP_OK) {
        *reply_length = p[0];
    }
    return error;
}

static esp_err_t uart_config(const uint8_t *p, size_t n)
{
    if (n != 9) {
        return ESP_ERR_INVALID_ARG;
    }
    const uint32_t baud = be32(&p[2]);
    if (p[0] == PIN_UNUSED || p[1] == PIN_UNUSED || baud < 300
        || baud > 2000000 || (p[6] != 7 && p[6] != 8)
        || p[7] > 2 || (p[8] != 1 && p[8] != 2)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (xSemaphoreTake(s_uart_lock, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    if (s_uart) {
        xSemaphoreGive(s_uart_lock);
        return ESP_ERR_INVALID_STATE;
    }
    const bool output[2] = {true, false};
    int gpio[2];
    esp_err_t error = reserve(p, output, 2, IO_MODE_UART, gpio);
    if (error != ESP_OK) {
        xSemaphoreGive(s_uart_lock);
        return error;
    }
    const uart_config_t cfg = {
        .baud_rate = (int)baud,
        .data_bits = p[6] == 7 ? UART_DATA_7_BITS : UART_DATA_8_BITS,
        .parity = p[7] == 1 ? UART_PARITY_EVEN : p[7] == 2 ? UART_PARITY_ODD : UART_PARITY_DISABLE,
        .stop_bits = p[8] == 2 ? UART_STOP_BITS_2 : UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    error = uart_param_config(UART_NUM_1, &cfg);
    if (error == ESP_OK) {
        error = uart_set_pin(UART_NUM_1, gpio[0], gpio[1], UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    }
    if (error == ESP_OK) {
        error = uart_driver_install(UART_NUM_1, 4096, 4096, 0, NULL, 0);
    }
    if (error == ESP_OK) {
        s_uart = true;
        memcpy(s_uart_pins, p, 2);
        s_uart_baud = baud;
        s_uart_data = p[6]; s_uart_parity = p[7]; s_uart_stop = p[8];
    } else {
        release(p, 2, IO_MODE_UART);
    }
    xSemaphoreGive(s_uart_lock);
    return error;
}

static esp_err_t uart_disable(void)
{
    if (xSemaphoreTake(s_uart_lock, portMAX_DELAY) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t error = ESP_OK;
    if (s_uart) {
        error = uart_driver_delete(UART_NUM_1);
        if (error == ESP_OK) {
            s_uart = false;
            release(s_uart_pins, 2, IO_MODE_UART);
            memset(s_uart_pins, PIN_UNUSED, sizeof(s_uart_pins));
            s_uart_baud = 0;
        }
    }
    xSemaphoreGive(s_uart_lock);
    return error;
}

int bus_uart_read(uint8_t *buffer, size_t capacity)
{
    if (!s_uart_lock || !buffer || !capacity || xSemaphoreTake(s_uart_lock, 0) != pdTRUE) {
        return 0;
    }
    const int result = s_uart ? uart_read_bytes(UART_NUM_1, buffer, capacity, 0) : 0;
    xSemaphoreGive(s_uart_lock);
    return result;
}

int bus_uart_write(const uint8_t *buffer, size_t length)
{
    if (!s_uart_lock || !buffer || !length || xSemaphoreTake(s_uart_lock, pdMS_TO_TICKS(20)) != pdTRUE) {
        return 0;
    }
    const int result = s_uart ? uart_write_bytes(UART_NUM_1, buffer, length) : 0;
    xSemaphoreGive(s_uart_lock);
    return result;
}

esp_err_t bus_debugger_init(void)
{
    s_uart_lock = xSemaphoreCreateMutex();
    return s_uart_lock ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t bus_debugger_command(uint8_t op, const uint8_t *payload, size_t n,
                               uint8_t *reply, size_t capacity, size_t *reply_length)
{
    if (!payload || !reply || !reply_length) {
        return ESP_ERR_INVALID_ARG;
    }
    *reply_length = 0;
    switch (op) {
    case DBG_I2C_CONFIG: return i2c_config(payload, n);
    case DBG_I2C_DISABLE: return n ? ESP_ERR_INVALID_ARG : i2c_disable();
    case DBG_I2C_SCAN: return n ? ESP_ERR_INVALID_ARG : i2c_scan(reply, capacity, reply_length);
    case DBG_I2C_TRANSFER: return i2c_transfer(payload, n, reply, capacity, reply_length);
    case DBG_SPI_CONFIG: return spi_config(payload, n);
    case DBG_SPI_DISABLE: return n ? ESP_ERR_INVALID_ARG : spi_disable();
    case DBG_SPI_TRANSFER: return spi_transfer(payload, n, reply, capacity, reply_length);
    case DBG_UART_CONFIG: return uart_config(payload, n);
    case DBG_UART_DISABLE: return n ? ESP_ERR_INVALID_ARG : uart_disable();
    case DBG_STATUS:
        if (n || capacity < 25) return ESP_ERR_INVALID_ARG;
        reply[0] = (s_i2c ? 1 : 0) | (s_spi ? 2 : 0) | (s_uart ? 4 : 0);
        memcpy(&reply[1], s_i2c_pins, 2);
        memcpy(&reply[3], s_spi_pins, 4);
        memcpy(&reply[7], s_uart_pins, 2);
        put32(&reply[9], s_i2c_hz);
        put32(&reply[13], s_spi_hz);
        put32(&reply[17], s_uart_baud);
        reply[21] = s_spi_mode;
        reply[22] = s_uart_data;
        reply[23] = s_uart_parity;
        reply[24] = s_uart_stop;
        *reply_length = 25;
        return ESP_OK;
    default: return ESP_ERR_NOT_SUPPORTED;
    }
}
