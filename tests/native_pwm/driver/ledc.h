#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef int ledc_timer_t;
typedef int ledc_timer_bit_t;
typedef int ledc_channel_t;
#define LEDC_LOW_SPEED_MODE 0
#define LEDC_INTR_DISABLE 0
#define LEDC_USE_XTAL_CLK 1
typedef struct {
    int speed_mode;
    ledc_timer_bit_t duty_resolution;
    ledc_timer_t timer_num;
    uint32_t freq_hz;
    int clk_cfg;
} ledc_timer_config_t;
typedef struct {
    int gpio_num, speed_mode;
    ledc_channel_t channel;
    int intr_type;
    ledc_timer_t timer_sel;
    uint32_t duty, hpoint;
} ledc_channel_config_t;
esp_err_t ledc_timer_config(const ledc_timer_config_t *cfg);
esp_err_t ledc_channel_config(const ledc_channel_config_t *cfg);
esp_err_t ledc_stop(int speed, ledc_channel_t channel, uint32_t idle);
uint32_t ledc_find_suitable_duty_resolution(uint32_t source, uint32_t frequency);
