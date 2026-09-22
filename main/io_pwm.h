#pragma once

#include <stdint.h>

#include "driver/gpio.h"
#include "esp_err.h"

/* Called by the single IO/Modbus worker; this allocator is not thread-safe. */
void io_pwm_init(void);
esp_err_t io_pwm_start(uint16_t channel, gpio_num_t gpio_num,
                       uint32_t frequency_hz, uint16_t duty_bp);
esp_err_t io_pwm_stop(uint16_t channel);
