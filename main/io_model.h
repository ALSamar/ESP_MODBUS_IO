#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define IO_DIGITAL_CHANNEL_COUNT 34U
#define IO_SAFE_DIGITAL_CHANNEL_COUNT 31U
#define IO_ANALOG_CHANNEL_COUNT 18U
#define IO_ANALOG_MV_UNAVAILABLE UINT16_MAX

typedef enum {
    IO_MODE_INPUT_FLOATING = 0,
    IO_MODE_INPUT_PULLUP = 1,
    IO_MODE_INPUT_PULLDOWN = 2,
    IO_MODE_OUTPUT = 3,
    IO_MODE_ANALOG = 4,
} io_mode_t;

esp_err_t io_model_init(void);

bool io_model_channel_available(uint16_t channel);
esp_err_t io_model_get_gpio(uint16_t channel, uint16_t *gpio_num);

esp_err_t io_model_read_output(uint16_t channel, bool *level);
esp_err_t io_model_write_output(uint16_t channel, bool level);
esp_err_t io_model_read_digital(uint16_t channel, bool *level);

esp_err_t io_model_get_mode(uint16_t channel, uint16_t *mode);
esp_err_t io_model_validate_mode(uint16_t channel, uint16_t mode);
esp_err_t io_model_set_mode(uint16_t channel, uint16_t mode);

esp_err_t io_model_read_analog_raw(uint16_t channel, uint16_t *raw);
esp_err_t io_model_read_analog_mv(uint16_t channel, uint16_t *millivolts);

bool io_model_calibration_supported(void);

#ifdef __cplusplus
}
#endif
