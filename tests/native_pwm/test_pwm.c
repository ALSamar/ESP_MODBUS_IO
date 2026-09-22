/* Tests compile the production IO model + allocator; only ESP-IDF hardware APIs
 * are replaced. Each exported test returns 0 or its failing source line. */
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "io_model.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_rom_gpio.h"
#include "soc/gpio_sig_map.h"

#define CHECK(expr) do { if (!(expr)) return __LINE__; } while (0)

typedef struct {
    ledc_channel_config_t config;
    bool running;
    uint32_t idle;
} mock_pwm_t;

static ledc_timer_config_t timers[4];
static mock_pwm_t channels[8];
static int routes[49], pin_modes[49], levels[49];
static unsigned hardware_calls;
static bool fail_timer, fail_channel, fail_stop, fail_gpio;
static unsigned timer_calls[4];

void *memset(void *dest, int value, size_t count)
{
    unsigned char *p = dest;
    for (size_t i = 0; i < count; ++i) p[i] = (unsigned char)value;
    return dest;
}

void *memcpy(void *dest, const void *src, size_t count)
{
    unsigned char *d = dest;
    const unsigned char *s = src;
    for (size_t i = 0; i < count; ++i) d[i] = s[i];
    return dest;
}

esp_err_t gpio_config(const gpio_config_t *cfg)
{
    ++hardware_calls;
    if (fail_gpio) { fail_gpio = false; return ESP_FAIL; }
    for (unsigned i = 0; i < 49; ++i)
        if (cfg->pin_bit_mask & (1ULL << i)) pin_modes[i] = cfg->mode;
    return ESP_OK;
}

esp_err_t gpio_reset_pin(gpio_num_t gpio)
{
    ++hardware_calls;
    pin_modes[gpio] = 0;
    /* Match IDF: reset alone does NOT detach the GPIO matrix route. */
    return ESP_OK;
}

esp_err_t gpio_set_level(gpio_num_t gpio, uint32_t level)
{
    ++hardware_calls;
    levels[gpio] = (int)level;
    return ESP_OK;
}

int gpio_get_level(gpio_num_t gpio)
{
    if (routes[gpio] >= 0)
        return channels[routes[gpio]].running ? 1 : (int)channels[routes[gpio]].idle;
    return levels[gpio];
}

void esp_rom_gpio_connect_out_signal(unsigned gpio, unsigned signal, bool invert, bool output_invert)
{
    (void)invert;
    (void)output_invert;
    ++hardware_calls;
    if (signal == SIG_GPIO_OUT_IDX) routes[gpio] = -1;
}

uint32_t ledc_find_suitable_duty_resolution(uint32_t source, uint32_t frequency)
{
    uint32_t result = 0;
    while ((source / frequency) >> (result + 1U)) ++result;
    return result > 14U ? 14U : result;
}

esp_err_t ledc_timer_config(const ledc_timer_config_t *cfg)
{
    ++hardware_calls;
    ++timer_calls[cfg->timer_num];
    if (fail_timer) { fail_timer = false; return ESP_FAIL; }
    if (cfg->clk_cfg != LEDC_USE_XTAL_CLK || cfg->duty_resolution > 14)
        return ESP_ERR_INVALID_ARG;
    timers[cfg->timer_num] = *cfg;
    return ESP_OK;
}

esp_err_t ledc_channel_config(const ledc_channel_config_t *cfg)
{
    ++hardware_calls;
    if (fail_channel) { fail_channel = false; return ESP_FAIL; }
    if (cfg->duty >= (1U << timers[cfg->timer_sel].duty_resolution))
        return ESP_ERR_INVALID_ARG;
    channels[cfg->channel].config = *cfg;
    channels[cfg->channel].running = true;
    routes[cfg->gpio_num] = cfg->channel;
    return ESP_OK;
}

esp_err_t ledc_stop(int speed, ledc_channel_t channel, uint32_t idle)
{
    (void)speed;
    ++hardware_calls;
    if (fail_stop) { fail_stop = false; return ESP_FAIL; }
    channels[channel].running = false;
    channels[channel].idle = idle;
    return ESP_OK;
}

esp_err_t adc_oneshot_new_unit(const adc_oneshot_unit_init_cfg_t *cfg, adc_oneshot_unit_handle_t *handle)
{
    *handle = (void *)(uintptr_t)cfg->unit_id;
    return ESP_OK;
}

esp_err_t adc_oneshot_config_channel(adc_oneshot_unit_handle_t handle, adc_channel_t channel,
                                    const adc_oneshot_chan_cfg_t *cfg)
{
    (void)cfg;
    const int gpio = (uintptr_t)handle == ADC_UNIT_1 ? channel + 1 : channel + 11;
    pin_modes[gpio] = 0;
    return ESP_OK;
}

esp_err_t adc_oneshot_read(adc_oneshot_unit_handle_t handle, adc_channel_t channel, int *value)
{
    (void)handle;
    (void)channel;
    *value = 1234;
    return ESP_OK;
}

esp_err_t adc_cali_create_scheme_curve_fitting(const adc_cali_curve_fitting_config_t *cfg,
                                               adc_cali_handle_t *handle)
{
    (void)cfg;
    *handle = (void *)(uintptr_t)1;
    return ESP_OK;
}

esp_err_t adc_cali_raw_to_voltage(adc_cali_handle_t handle, int raw, int *voltage)
{
    (void)handle;
    *voltage = raw;
    return ESP_OK;
}

static void reset(void)
{
    memset(timers, 0, sizeof(timers));
    memset(channels, 0, sizeof(channels));
    memset(timer_calls, 0, sizeof(timer_calls));
    memset(pin_modes, 0, sizeof(pin_modes));
    memset(levels, 0, sizeof(levels));
    for (unsigned i = 0; i < 49; ++i) routes[i] = -1;
    fail_timer = fail_channel = fail_stop = fail_gpio = false;
    (void)io_model_init();
    hardware_calls = 0;
}

static bool state(uint16_t channel, uint32_t frequency, uint16_t duty, bool enabled)
{
    uint32_t read_frequency = 0;
    uint16_t read_duty = 0;
    bool read_enabled = false;
    return io_model_pwm_get(channel, &read_frequency, &read_duty, &read_enabled) == ESP_OK
        && read_frequency == frequency && read_duty == duty && read_enabled == enabled;
}

static unsigned mode(uint16_t channel)
{
    uint16_t result = 999;
    (void)io_model_get_mode(channel, &result);
    return result;
}

int test_defaults_and_disabled_configuration(void)
{
    reset();
    CHECK(state(0, 1000, 5000, false));
    CHECK(state(30, 1000, 5000, false));
    CHECK(io_model_pwm_configure(31, 1000, 5000, true) == ESP_ERR_INVALID_ARG);
    CHECK(io_model_pwm_configure(34, 1000, 5000, true) == ESP_ERR_INVALID_ARG);
    CHECK(io_model_write_output(2, true) == ESP_OK);
    const unsigned before = hardware_calls;
    CHECK(io_model_pwm_configure(2, 12000, 3750, false) == ESP_OK);
    CHECK(hardware_calls == before);
    CHECK(state(2, 12000, 3750, false));
    CHECK(mode(2) == IO_MODE_OUTPUT && levels[2] == 1);
    CHECK(io_model_set_mode(2, IO_MODE_PWM) == ESP_OK);
    CHECK(state(2, 12000, 3750, true));
    CHECK(timers[channels[routes[2]].config.timer_sel].freq_hz == 12000);
    CHECK(io_model_pwm_get(2, NULL, NULL, NULL) == ESP_ERR_INVALID_ARG);
    return 0;
}

int test_eight_channels_and_safe_reuse(void)
{
    reset();
    for (unsigned i = 0; i < 8; ++i)
        CHECK(io_model_pwm_configure(i, 1000, 5000, true) == ESP_OK);
    CHECK(timer_calls[0] == 1);
    const unsigned before = hardware_calls;
    CHECK(io_model_pwm_configure(8, 1000, 5000, true) == ESP_ERR_NOT_FOUND);
    CHECK(hardware_calls == before && state(8, 1000, 5000, false));
    CHECK(mode(8) == IO_MODE_INPUT_FLOATING);
    CHECK(io_model_pwm_configure(3, 1000, 5000, false) == ESP_OK);
    CHECK(routes[3] == -1 && pin_modes[3] == GPIO_MODE_INPUT);
    CHECK(io_model_pwm_configure(8, 1000, 5000, true) == ESP_OK);
    CHECK(routes[8] == 3 && routes[3] == -1);
    return 0;
}

int test_four_frequencies_and_shared_timer_protection(void)
{
    reset();
    for (unsigned i = 0; i < 4; ++i)
        CHECK(io_model_pwm_configure(i, 1000U * (i + 1U), 5000, true) == ESP_OK);
    CHECK(io_model_pwm_configure(4, 1000, 1000, true) == ESP_OK);
    CHECK(channels[routes[4]].config.timer_sel == channels[routes[0]].config.timer_sel);
    unsigned before = hardware_calls;
    CHECK(io_model_pwm_configure(5, 5000, 5000, true) == ESP_ERR_NOT_FOUND);
    CHECK(io_model_pwm_configure(0, 5000, 5000, true) == ESP_ERR_NOT_FOUND);
    CHECK(hardware_calls == before && state(0, 1000, 5000, true));
    CHECK(timers[channels[routes[4]].config.timer_sel].freq_hz == 1000);
    /* An exclusively owned timer may be retuned even with all timers occupied. */
    CHECK(io_model_pwm_configure(1, 5000, 2500, true) == ESP_OK);
    CHECK(timers[channels[routes[1]].config.timer_sel].freq_hz == 5000);
    CHECK(timers[channels[routes[0]].config.timer_sel].freq_hz == 1000);
    /* Joining another frequency group frees the old timer for another output. */
    CHECK(io_model_pwm_configure(1, 1000, 2500, true) == ESP_OK);
    CHECK(io_model_pwm_configure(5, 6000, 5000, true) == ESP_OK);
    CHECK(timers[channels[routes[5]].config.timer_sel].freq_hz == 6000);
    return 0;
}

int test_frequency_and_duty_boundaries(void)
{
    reset();
    CHECK(io_model_pwm_configure(0, 10, 1, true) == ESP_OK);
    CHECK(timers[channels[routes[0]].config.timer_sel].duty_resolution == 14);
    CHECK(io_model_pwm_configure(1, 100000, 9999, true) == ESP_OK);
    CHECK(timers[channels[routes[1]].config.timer_sel].duty_resolution == 8);
    CHECK(channels[routes[1]].config.duty == 255);
    const unsigned before = hardware_calls;
    CHECK(io_model_pwm_configure(0, 9, 5000, true) == ESP_ERR_INVALID_ARG);
    CHECK(io_model_pwm_configure(0, 100001, 5000, true) == ESP_ERR_INVALID_ARG);
    CHECK(io_model_pwm_configure(0, 10, 10001, true) == ESP_ERR_INVALID_ARG);
    CHECK(io_model_pwm_configure(0, 0, 0, false) == ESP_ERR_INVALID_ARG);
    CHECK(hardware_calls == before && state(0, 10, 1, true));
    return 0;
}

int test_static_endpoints_restart_and_readback(void)
{
    reset();
    bool level = true;
    CHECK(io_model_pwm_configure(1, 1000, 0, true) == ESP_OK);
    CHECK(!channels[routes[1]].running && channels[routes[1]].idle == 0);
    CHECK(io_model_read_digital(1, &level) == ESP_OK && !level);
    CHECK(state(1, 1000, 0, true));
    CHECK(io_model_pwm_configure(1, 1000, 10000, true) == ESP_OK);
    CHECK(!channels[routes[1]].running && channels[routes[1]].idle == 1);
    CHECK(io_model_read_digital(1, &level) == ESP_OK && level);
    CHECK(io_model_pwm_configure(1, 1000, 5000, true) == ESP_OK);
    CHECK(channels[routes[1]].running);
    CHECK(channels[routes[1]].config.duty == 8192);
    return 0;
}

int test_modes_coils_and_adc_do_not_steal_pwm(void)
{
    reset();
    uint16_t raw = 0;
    bool output = false;
    CHECK(io_model_pwm_configure(1, 2500, 1250, true) == ESP_OK);
    const unsigned before = hardware_calls;
    CHECK(io_model_read_analog_raw(0, &raw) == ESP_ERR_INVALID_STATE);
    CHECK(hardware_calls == before && mode(1) == IO_MODE_PWM);
    CHECK(io_model_write_output(1, true) == ESP_OK);
    CHECK(routes[1] == -1 && mode(1) == IO_MODE_OUTPUT && levels[1] == 1);
    CHECK(io_model_read_output(1, &output) == ESP_OK && output);
    CHECK(state(1, 2500, 1250, false));
    CHECK(io_model_set_mode(1, IO_MODE_PWM) == ESP_OK);
    CHECK(io_model_set_mode(1, IO_MODE_ANALOG) == ESP_OK);
    CHECK(routes[1] == -1 && state(1, 2500, 1250, false));
    CHECK(io_model_read_analog_raw(0, &raw) == ESP_OK && raw == 1234);
    CHECK(io_model_set_mode(1, IO_MODE_PWM) == ESP_OK);
    CHECK(io_model_set_mode(1, IO_MODE_INPUT_PULLUP) == ESP_OK);
    CHECK(routes[1] == -1 && mode(1) == IO_MODE_INPUT_PULLUP);
    CHECK(io_model_pwm_configure(1, 4000, 2000, false) == ESP_OK);
    CHECK(mode(1) == IO_MODE_INPUT_PULLUP && state(1, 4000, 2000, false));
    CHECK(io_model_set_mode(1, IO_MODE_PWM) == ESP_OK);
    CHECK(io_model_pwm_configure(1, 4000, 2000, false) == ESP_OK);
    CHECK(mode(1) == IO_MODE_INPUT_FLOATING && pin_modes[1] == GPIO_MODE_INPUT);
    return 0;
}

int test_timer_failure_preserves_configuration(void)
{
    reset();
    CHECK(io_model_pwm_configure(0, 1000, 5000, true) == ESP_OK);
    const int timer = channels[routes[0]].config.timer_sel;
    fail_timer = true;
    CHECK(io_model_pwm_configure(0, 2000, 1000, true) == ESP_FAIL);
    CHECK(state(0, 1000, 5000, true) && timers[timer].freq_hz == 1000);
    CHECK(channels[routes[0]].running);
    CHECK(io_model_write_output(1, true) == ESP_OK);
    fail_timer = true;
    CHECK(io_model_pwm_configure(1, 2000, 1000, true) == ESP_FAIL);
    CHECK(mode(1) == IO_MODE_OUTPUT && levels[1] == 1 && routes[1] == -1);
    CHECK(state(1, 1000, 5000, false));
    return 0;
}

int test_channel_failure_rolls_back_and_does_not_leak(void)
{
    reset();
    CHECK(io_model_pwm_configure(0, 1000, 5000, true) == ESP_OK);
    fail_channel = true;
    CHECK(io_model_pwm_configure(0, 2000, 1000, true) == ESP_FAIL);
    CHECK(state(0, 1000, 5000, true));
    CHECK(timers[channels[routes[0]].config.timer_sel].freq_hz == 1000);
    CHECK(channels[routes[0]].config.duty == 8192 && channels[routes[0]].running);
    CHECK(io_model_write_output(1, true) == ESP_OK);
    fail_channel = true;
    CHECK(io_model_pwm_configure(1, 2000, 5000, true) == ESP_FAIL);
    CHECK(state(1, 1000, 5000, false) && mode(1) == IO_MODE_OUTPUT);
    CHECK(routes[1] == -1 && levels[1] == 1);
    for (unsigned i = 1; i < 8; ++i)
        CHECK(io_model_pwm_configure(i, 1000, 5000, true) == ESP_OK);
    return 0;
}

int test_stop_and_gpio_failures_preserve_active_state(void)
{
    reset();
    CHECK(io_model_pwm_configure(0, 1000, 7500, true) == ESP_OK);
    fail_stop = true;
    CHECK(io_model_pwm_configure(0, 2000, 2500, false) == ESP_FAIL);
    CHECK(state(0, 1000, 7500, true) && channels[routes[0]].running);
    fail_gpio = true;
    CHECK(io_model_set_mode(0, IO_MODE_INPUT_FLOATING) == ESP_FAIL);
    CHECK(state(0, 1000, 7500, true) && mode(0) == IO_MODE_PWM);
    CHECK(routes[0] >= 0 && channels[routes[0]].running);
    CHECK(io_model_pwm_configure(0, 1000, 7500, false) == ESP_OK);
    return 0;
}
