#include "io_pwm.h"

#include <stdbool.h>
#include <string.h>

#include "driver/ledc.h"
#include "esp_rom_gpio.h"
#include "io_model.h"
#include "soc/gpio_sig_map.h"

#define PWM_SOURCE_CLOCK_HZ 40000000U
#define PWM_MAX_RESOLUTION_BITS 14U

typedef struct {
    uint32_t frequency_hz;
    uint32_t resolution_bits;
    uint8_t users;
} pwm_timer_t;

typedef struct {
    bool used;
    uint16_t io_channel;
    gpio_num_t gpio_num;
    uint16_t duty_bp;
    uint8_t timer;
} pwm_channel_t;

static pwm_timer_t s_timers[IO_PWM_MAX_TIMERS];
static pwm_channel_t s_channels[IO_PWM_MAX_CHANNELS];

void io_pwm_init(void)
{
    memset(s_timers, 0, sizeof(s_timers));
    memset(s_channels, 0, sizeof(s_channels));
}

static int find_channel(uint16_t channel)
{
    for (unsigned i = 0; i < IO_PWM_MAX_CHANNELS; ++i) {
        if (s_channels[i].used && s_channels[i].io_channel == channel) {
            return (int)i;
        }
    }
    return -1;
}

static esp_err_t configure_timer(unsigned timer, uint32_t frequency_hz,
                                  uint32_t resolution_bits)
{
    const ledc_timer_config_t cfg = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = (ledc_timer_bit_t)resolution_bits,
        .timer_num = (ledc_timer_t)timer,
        .freq_hz = frequency_hz,
        .clk_cfg = LEDC_USE_XTAL_CLK,
    };
    return ledc_timer_config(&cfg);
}

static esp_err_t configure_channel(unsigned slot, const pwm_channel_t *channel,
                                    uint32_t resolution_bits)
{
    const gpio_config_t gpio_cfg = {
        .pin_bit_mask = 1ULL << channel->gpio_num,
        .mode = GPIO_MODE_INPUT_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&gpio_cfg);
    if (err != ESP_OK) {
        return err;
    }

    const uint32_t period = 1U << resolution_bits;
    uint32_t duty = ((uint32_t)channel->duty_bp * period + IO_PWM_DUTY_MAX / 2U)
                    / IO_PWM_DUTY_MAX;
    /* The maximum-resolution hardware counter must never receive 2^bits. */
    if (duty >= period) {
        duty = period - 1U;
    }
    const ledc_channel_config_t cfg = {
        .gpio_num = channel->gpio_num,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = (ledc_channel_t)slot,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = (ledc_timer_t)channel->timer,
        .duty = (channel->duty_bp == IO_PWM_DUTY_MAX) ? 0U : duty,
        .hpoint = 0,
    };
    err = ledc_channel_config(&cfg);
    if (err != ESP_OK) {
        return err;
    }
    /* Static endpoints are exact and still reserve their channel and timer.
     * A later channel_config calls ledc_update_duty and restarts the signal. */
    if (channel->duty_bp == 0U || channel->duty_bp == IO_PWM_DUTY_MAX) {
        return ledc_stop(LEDC_LOW_SPEED_MODE, (ledc_channel_t)slot,
                         channel->duty_bp == IO_PWM_DUTY_MAX ? 1U : 0U);
    }
    return ESP_OK;
}

static esp_err_t detach_gpio(gpio_num_t gpio_num)
{
    esp_err_t err = gpio_reset_pin(gpio_num);
    if (err != ESP_OK) {
        return err;
    }
    /* Reset/config alone leaves the old peripheral matrix route attached.
     * Remove it before reusing that LEDC channel for another physical pin. */
    esp_rom_gpio_connect_out_signal(gpio_num, SIG_GPIO_OUT_IDX, false, false);
    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << gpio_num,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    return gpio_config(&cfg);
}

esp_err_t io_pwm_start(uint16_t channel, gpio_num_t gpio_num,
                       uint32_t frequency_hz, uint16_t duty_bp)
{
    if (channel >= IO_DIGITAL_CHANNEL_COUNT || !GPIO_IS_VALID_OUTPUT_GPIO(gpio_num)
        || frequency_hz < IO_PWM_MIN_FREQUENCY_HZ
        || frequency_hz > IO_PWM_MAX_FREQUENCY_HZ || duty_bp > IO_PWM_DUTY_MAX) {
        return ESP_ERR_INVALID_ARG;
    }

    const int existing = find_channel(channel);
    int slot = existing;
    if (slot < 0) {
        for (unsigned i = 0; i < IO_PWM_MAX_CHANNELS; ++i) {
            if (!s_channels[i].used) {
                slot = (int)i;
                break;
            }
        }
    }
    if (slot < 0) {
        return ESP_ERR_NOT_FOUND;
    }

    int timer = -1;
    for (unsigned i = 0; i < IO_PWM_MAX_TIMERS; ++i) {
        if (s_timers[i].users && s_timers[i].frequency_hz == frequency_hz) {
            timer = (int)i;
            break;
        }
    }
    if (timer < 0 && existing >= 0 && s_timers[s_channels[slot].timer].users == 1U) {
        timer = s_channels[slot].timer;
    }
    if (timer < 0) {
        for (unsigned i = 0; i < IO_PWM_MAX_TIMERS; ++i) {
            if (!s_timers[i].users) {
                timer = (int)i;
                break;
            }
        }
    }
    if (timer < 0) {
        return ESP_ERR_NOT_FOUND;
    }

    uint32_t resolution = ledc_find_suitable_duty_resolution(PWM_SOURCE_CLOCK_HZ, frequency_hz);
    if (resolution > PWM_MAX_RESOLUTION_BITS) {
        resolution = PWM_MAX_RESOLUTION_BITS;
    }
    if (resolution == 0U) {
        return ESP_ERR_INVALID_ARG;
    }

    /* No pin or timer is touched until every resource has been located. */
    const pwm_channel_t previous = s_channels[slot];
    const pwm_timer_t previous_timer = s_timers[timer];
    const bool retune = !previous_timer.users || previous_timer.frequency_hz != frequency_hz;
    if (retune) {
        const esp_err_t err = configure_timer((unsigned)timer, frequency_hz, resolution);
        if (err != ESP_OK) {
            if (existing >= 0 && previous.timer == timer) {
                (void)configure_timer((unsigned)timer, previous_timer.frequency_hz,
                                       previous_timer.resolution_bits);
            }
            return err;
        }
    }

    const pwm_channel_t next = {
        .used = true,
        .io_channel = channel,
        .gpio_num = gpio_num,
        .duty_bp = duty_bp,
        .timer = (uint8_t)timer,
    };
    const esp_err_t err = configure_channel((unsigned)slot, &next, resolution);
    if (err != ESP_OK) {
        if (existing >= 0) {
            if (retune && previous.timer == timer) {
                (void)configure_timer((unsigned)timer, previous_timer.frequency_hz,
                                       previous_timer.resolution_bits);
            }
            (void)configure_channel((unsigned)slot, &previous,
                                     s_timers[previous.timer].resolution_bits);
        } else {
            (void)ledc_stop(LEDC_LOW_SPEED_MODE, (ledc_channel_t)slot, 0);
            (void)detach_gpio(gpio_num);
        }
        return err;
    }

    if (existing >= 0) {
        --s_timers[previous.timer].users;
    }
    s_timers[timer].frequency_hz = frequency_hz;
    s_timers[timer].resolution_bits = resolution;
    ++s_timers[timer].users;
    s_channels[slot] = next;
    return ESP_OK;
}

esp_err_t io_pwm_stop(uint16_t channel)
{
    const int slot = find_channel(channel);
    if (slot < 0) {
        return ESP_OK;
    }
    const pwm_channel_t previous = s_channels[slot];
    esp_err_t err = ledc_stop(LEDC_LOW_SPEED_MODE, (ledc_channel_t)slot, 0);
    if (err != ESP_OK) {
        return err;
    }
    err = detach_gpio(previous.gpio_num);
    if (err != ESP_OK) {
        (void)configure_channel((unsigned)slot, &previous,
                                 s_timers[previous.timer].resolution_bits);
        return err;
    }
    --s_timers[previous.timer].users;
    s_channels[slot].used = false;
    return ESP_OK;
}
