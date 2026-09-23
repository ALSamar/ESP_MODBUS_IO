/* Test only: execute the production Modbus server with deterministic IO. */
#include "io_model.h"
#include "modbus_server.h"
#include "bus_debugger.h"
#include <stddef.h>

void *memcpy(void *dest, const void *src, size_t n) {
    unsigned char *d = dest; const unsigned char *s = src;
    for (size_t i = 0; i < n; ++i) d[i] = s[i];
    return dest;
}
void *memset(void *dest, int c, size_t n) {
    unsigned char *d = dest;
    for (size_t i = 0; i < n; ++i) d[i] = (unsigned char)c;
    return dest;
}
static uint16_t modes[34], duties[34];
static uint32_t frequencies[34];
static bool outputs[34];
static uint8_t request[256], response[256];
static size_t response_length;
static unsigned writes;
static esp_err_t forced_error;

esp_err_t bus_debugger_command(uint8_t op, const uint8_t *payload, size_t n,
                               uint8_t *reply, size_t capacity, size_t *reply_length) {
    (void)payload;
    if (op != DBG_STATUS) return ESP_ERR_NOT_SUPPORTED;
    if (n != 0 || capacity < 25) return ESP_ERR_INVALID_ARG;
    memset(reply, 0, 25);
    *reply_length = 25;
    return ESP_OK;
}

void reset_mock(void) {
    memset(modes, 0, sizeof(modes));
    memset(outputs, 0, sizeof(outputs));
    for (int i = 0; i < 34; ++i) { frequencies[i] = 1000; duties[i] = 5000; }
    writes = 0; forced_error = ESP_OK;
}
uint8_t *request_buffer(void) { return request; }
uint8_t *response_buffer(void) { return response; }
unsigned response_size(void) { return (unsigned)response_length; }
unsigned write_count(void) { return writes; }
void force_error(int error) { forced_error = error; }
int process_request(unsigned size) {
    response_length = 0;
    return modbus_server_process(request, size, response, sizeof(response), &response_length);
}
bool io_model_channel_available(uint16_t c) { return c < 31; }
esp_err_t io_model_get_gpio(uint16_t c, uint16_t *v) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    *v = c; return ESP_OK;
}
esp_err_t io_model_read_output(uint16_t c, bool *v) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    *v = outputs[c]; return ESP_OK;
}
esp_err_t io_model_write_output(uint16_t c, bool v) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    writes++; outputs[c] = v; modes[c] = IO_MODE_OUTPUT; return ESP_OK;
}
esp_err_t io_model_read_digital(uint16_t c, bool *v) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    if (modes[c] == IO_MODE_ANALOG) return ESP_ERR_INVALID_STATE;
    *v = outputs[c]; return ESP_OK;
}
esp_err_t io_model_get_mode(uint16_t c, uint16_t *v) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    *v = modes[c]; return ESP_OK;
}
esp_err_t io_model_validate_mode(uint16_t c, uint16_t mode) {
    return io_model_channel_available(c) && mode <= IO_MODE_PWM ? ESP_OK : ESP_ERR_INVALID_ARG;
}
esp_err_t io_model_set_mode(uint16_t c, uint16_t mode) {
    if (forced_error) return forced_error;
    esp_err_t error = io_model_validate_mode(c, mode);
    if (error) return error;
    writes++; modes[c] = mode; return ESP_OK;
}
esp_err_t io_model_read_analog_raw(uint16_t c, uint16_t *v) {
    if (c >= 18) return ESP_ERR_INVALID_ARG;
    if (modes[c + 1] == IO_MODE_OUTPUT || modes[c + 1] == IO_MODE_PWM) return ESP_ERR_INVALID_STATE;
    *v = 2048; return ESP_OK;
}
esp_err_t io_model_read_analog_mv(uint16_t c, uint16_t *v) {
    return io_model_read_analog_raw(c, v);
}
bool io_model_calibration_supported(void) { return true; }
esp_err_t io_model_pwm_get(uint16_t c, uint32_t *frequency, uint16_t *duty, bool *enabled) {
    if (!io_model_channel_available(c)) return ESP_ERR_INVALID_ARG;
    *frequency = frequencies[c]; *duty = duties[c]; *enabled = modes[c] == IO_MODE_PWM;
    return ESP_OK;
}
esp_err_t io_model_pwm_configure(uint16_t c, uint32_t frequency, uint16_t duty, bool enabled) {
    if (forced_error) return forced_error;
    writes++; frequencies[c] = frequency; duties[c] = duty;
    if (enabled) modes[c] = IO_MODE_PWM;
    else if (modes[c] == IO_MODE_PWM) modes[c] = IO_MODE_INPUT_FLOATING;
    return ESP_OK;
}
