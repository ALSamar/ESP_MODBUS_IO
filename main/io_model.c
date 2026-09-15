#include "io_model.h"

#include <stddef.h>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "sdkconfig.h"

static const char *TAG = "io_model";

/*
 * Channels 0..30 are safe on the pictured ESP32-S3-WROOM-1 board. Channels
 * 31..33 are deliberately placed last because GPIO35..37 can be occupied by
 * Octal PSRAM on N8R8/N16R8 modules.
 */
static const gpio_num_t s_digital_gpio[IO_DIGITAL_CHANNEL_COUNT] = {
    GPIO_NUM_0,  GPIO_NUM_1,  GPIO_NUM_2,  GPIO_NUM_3,  GPIO_NUM_4,
    GPIO_NUM_5,  GPIO_NUM_6,  GPIO_NUM_7,  GPIO_NUM_8,  GPIO_NUM_9,
    GPIO_NUM_10, GPIO_NUM_11, GPIO_NUM_12, GPIO_NUM_13, GPIO_NUM_14,
    GPIO_NUM_15, GPIO_NUM_16, GPIO_NUM_17, GPIO_NUM_18, GPIO_NUM_21,
    GPIO_NUM_38, GPIO_NUM_39, GPIO_NUM_40, GPIO_NUM_41, GPIO_NUM_42,
    GPIO_NUM_43, GPIO_NUM_44, GPIO_NUM_45, GPIO_NUM_46, GPIO_NUM_47,
    GPIO_NUM_48, GPIO_NUM_35, GPIO_NUM_36, GPIO_NUM_37,
};

static io_mode_t s_modes[IO_DIGITAL_CHANNEL_COUNT];
static bool s_output_state[IO_DIGITAL_CHANNEL_COUNT];

static adc_oneshot_unit_handle_t s_adc_units[2];
static adc_cali_handle_t s_adc_cali[IO_ANALOG_CHANNEL_COUNT];
static bool s_adc_cali_available[IO_ANALOG_CHANNEL_COUNT];
static bool s_any_calibration;

static void analog_channel_to_adc(uint16_t channel, adc_unit_t *unit, adc_channel_t *adc_channel)
{
    const uint16_t gpio_num = channel + 1U;
    if (gpio_num <= 10U) {
        *unit = ADC_UNIT_1;
        *adc_channel = (adc_channel_t)(gpio_num - 1U);
    } else {
        *unit = ADC_UNIT_2;
        *adc_channel = (adc_channel_t)(gpio_num - 11U);
    }
}

static esp_err_t configure_gpio_mode(uint16_t channel, io_mode_t mode)
{
    const gpio_num_t gpio_num = s_digital_gpio[channel];

    if (mode == IO_MODE_ANALOG) {
        if ((gpio_num < GPIO_NUM_1) || (gpio_num > GPIO_NUM_18)) {
            return ESP_ERR_INVALID_ARG;
        }

        adc_unit_t unit;
        adc_channel_t adc_channel;
        analog_channel_to_adc((uint16_t)(gpio_num - 1), &unit, &adc_channel);
        const adc_oneshot_chan_cfg_t adc_cfg = {
            .atten = ADC_ATTEN_DB_12,
            .bitwidth = ADC_BITWIDTH_DEFAULT,
        };
        return adc_oneshot_config_channel(s_adc_units[unit - ADC_UNIT_1], adc_channel, &adc_cfg);
    }

    gpio_config_t gpio_cfg = {
        .pin_bit_mask = (1ULL << gpio_num),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    switch (mode) {
    case IO_MODE_INPUT_FLOATING:
        break;
    case IO_MODE_INPUT_PULLUP:
        gpio_cfg.pull_up_en = GPIO_PULLUP_ENABLE;
        break;
    case IO_MODE_INPUT_PULLDOWN:
        gpio_cfg.pull_down_en = GPIO_PULLDOWN_ENABLE;
        break;
    case IO_MODE_OUTPUT:
        /* Input remains enabled so function 0x02 can read back the pin level. */
        ESP_RETURN_ON_ERROR(gpio_set_level(gpio_num, s_output_state[channel]), TAG,
                            "set GPIO%d output latch", gpio_num);
        gpio_cfg.mode = GPIO_MODE_INPUT_OUTPUT;
        break;
    default:
        return ESP_ERR_INVALID_ARG;
    }

    return gpio_config(&gpio_cfg);
}

static void init_calibration(uint16_t analog_channel)
{
    adc_unit_t unit;
    adc_channel_t channel;
    analog_channel_to_adc(analog_channel, &unit, &channel);

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    const adc_cali_curve_fitting_config_t cfg = {
        .unit_id = unit,
        .chan = channel,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_curve_fitting(&cfg, &s_adc_cali[analog_channel]) == ESP_OK) {
        s_adc_cali_available[analog_channel] = true;
        s_any_calibration = true;
    }
#endif
}

esp_err_t io_model_init(void)
{
    const adc_oneshot_unit_init_cfg_t adc1_cfg = {
        .unit_id = ADC_UNIT_1,
    };
    const adc_oneshot_unit_init_cfg_t adc2_cfg = {
        .unit_id = ADC_UNIT_2,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&adc1_cfg, &s_adc_units[0]), TAG,
                        "create ADC1 oneshot unit");
    ESP_RETURN_ON_ERROR(adc_oneshot_new_unit(&adc2_cfg, &s_adc_units[1]), TAG,
                        "create ADC2 oneshot unit");

    for (uint16_t channel = 0; channel < IO_DIGITAL_CHANNEL_COUNT; ++channel) {
        s_modes[channel] = IO_MODE_INPUT_FLOATING;
        s_output_state[channel] = false;
        if (io_model_channel_available(channel)) {
            ESP_RETURN_ON_ERROR(configure_gpio_mode(channel, IO_MODE_INPUT_FLOATING), TAG,
                                "initialize digital channel %u", channel);
        }
    }

    for (uint16_t channel = 0; channel < IO_ANALOG_CHANNEL_COUNT; ++channel) {
        init_calibration(channel);
    }

    return ESP_OK;
}

bool io_model_channel_available(uint16_t channel)
{
    if (channel >= IO_DIGITAL_CHANNEL_COUNT) {
        return false;
    }
    if (channel < IO_SAFE_DIGITAL_CHANNEL_COUNT) {
        return true;
    }
#if CONFIG_USB_MODBUS_ENABLE_GPIO35_37
    return true;
#else
    return false;
#endif
}

esp_err_t io_model_get_gpio(uint16_t channel, uint16_t *gpio_num)
{
    if ((gpio_num == NULL) || !io_model_channel_available(channel)) {
        return ESP_ERR_INVALID_ARG;
    }
    *gpio_num = (uint16_t)s_digital_gpio[channel];
    return ESP_OK;
}

esp_err_t io_model_read_output(uint16_t channel, bool *level)
{
    if ((level == NULL) || !io_model_channel_available(channel)) {
        return ESP_ERR_INVALID_ARG;
    }
    *level = s_output_state[channel];
    return ESP_OK;
}

esp_err_t io_model_write_output(uint16_t channel, bool level)
{
    if (!io_model_channel_available(channel)) {
        return ESP_ERR_INVALID_ARG;
    }

    s_output_state[channel] = level;
    if (s_modes[channel] != IO_MODE_OUTPUT) {
        ESP_RETURN_ON_ERROR(configure_gpio_mode(channel, IO_MODE_OUTPUT), TAG,
                            "set channel %u to output", channel);
        s_modes[channel] = IO_MODE_OUTPUT;
    }
    return gpio_set_level(s_digital_gpio[channel], level);
}

esp_err_t io_model_read_digital(uint16_t channel, bool *level)
{
    if ((level == NULL) || !io_model_channel_available(channel)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_modes[channel] == IO_MODE_ANALOG) {
        return ESP_ERR_INVALID_STATE;
    }
    *level = gpio_get_level(s_digital_gpio[channel]) != 0;
    return ESP_OK;
}

esp_err_t io_model_get_mode(uint16_t channel, uint16_t *mode)
{
    if ((mode == NULL) || !io_model_channel_available(channel)) {
        return ESP_ERR_INVALID_ARG;
    }
    *mode = (uint16_t)s_modes[channel];
    return ESP_OK;
}

esp_err_t io_model_validate_mode(uint16_t channel, uint16_t mode)
{
    if (!io_model_channel_available(channel) || (mode > IO_MODE_ANALOG)) {
        return ESP_ERR_INVALID_ARG;
    }
    if ((mode == IO_MODE_ANALOG) && (s_digital_gpio[channel] > GPIO_NUM_18)) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    if ((mode == IO_MODE_ANALOG) && (s_digital_gpio[channel] < GPIO_NUM_1)) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    return ESP_OK;
}

esp_err_t io_model_set_mode(uint16_t channel, uint16_t mode)
{
    ESP_RETURN_ON_ERROR(io_model_validate_mode(channel, mode), TAG,
                        "validate mode %u for channel %u", mode, channel);
    ESP_RETURN_ON_ERROR(configure_gpio_mode(channel, (io_mode_t)mode), TAG,
                        "configure mode %u for channel %u", mode, channel);
    s_modes[channel] = (io_mode_t)mode;
    return ESP_OK;
}

static esp_err_t prepare_analog_channel(uint16_t channel, adc_unit_t *unit,
                                        adc_channel_t *adc_channel)
{
    if ((channel >= IO_ANALOG_CHANNEL_COUNT) || (unit == NULL) || (adc_channel == NULL)) {
        return ESP_ERR_INVALID_ARG;
    }

    /* Analog channel N maps to GPIO N+1, which is also digital channel N+1. */
    const uint16_t digital_channel = channel + 1U;
    if (s_modes[digital_channel] == IO_MODE_OUTPUT) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_modes[digital_channel] != IO_MODE_ANALOG) {
        ESP_RETURN_ON_ERROR(io_model_set_mode(digital_channel, IO_MODE_ANALOG), TAG,
                            "prepare analog channel %u", channel);
    }

    analog_channel_to_adc(channel, unit, adc_channel);
    return ESP_OK;
}

esp_err_t io_model_read_analog_raw(uint16_t channel, uint16_t *raw)
{
    if (raw == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    adc_unit_t unit;
    adc_channel_t adc_channel;
    ESP_RETURN_ON_ERROR(prepare_analog_channel(channel, &unit, &adc_channel), TAG,
                        "prepare raw ADC read");

    int value = 0;
    ESP_RETURN_ON_ERROR(adc_oneshot_read(s_adc_units[unit - ADC_UNIT_1], adc_channel, &value),
                        TAG, "read ADC channel");
    *raw = (uint16_t)value;
    return ESP_OK;
}

esp_err_t io_model_read_analog_mv(uint16_t channel, uint16_t *millivolts)
{
    if ((millivolts == NULL) || (channel >= IO_ANALOG_CHANNEL_COUNT)) {
        return ESP_ERR_INVALID_ARG;
    }

    uint16_t raw = 0;
    ESP_RETURN_ON_ERROR(io_model_read_analog_raw(channel, &raw), TAG,
                        "read calibrated ADC input");

    if (!s_adc_cali_available[channel]) {
        *millivolts = IO_ANALOG_MV_UNAVAILABLE;
        return ESP_OK;
    }

    int voltage = 0;
    ESP_RETURN_ON_ERROR(adc_cali_raw_to_voltage(s_adc_cali[channel], raw, &voltage), TAG,
                        "convert ADC raw value");
    *millivolts = (uint16_t)voltage;
    return ESP_OK;
}

bool io_model_calibration_supported(void)
{
    return s_any_calibration;
}
