#include "modbus_server.h"

#include <stdbool.h>
#include <string.h>

#include "bus_debugger.h"
#include "io_model.h"
#include "sdkconfig.h"

enum {
    FC_READ_COILS = 0x01,
    FC_READ_DISCRETE_INPUTS = 0x02,
    FC_READ_HOLDING_REGISTERS = 0x03,
    FC_READ_INPUT_REGISTERS = 0x04,
    FC_WRITE_SINGLE_COIL = 0x05,
    FC_WRITE_SINGLE_REGISTER = 0x06,
    FC_WRITE_MULTIPLE_COILS = 0x0F,
    FC_WRITE_MULTIPLE_REGISTERS = 0x10,
    FC_DEBUGGER = 0x41,
};

enum {
    EX_ILLEGAL_FUNCTION = 0x01,
    EX_ILLEGAL_DATA_ADDRESS = 0x02,
    EX_ILLEGAL_DATA_VALUE = 0x03,
    EX_SERVER_DEVICE_FAILURE = 0x04,
    EX_SERVER_DEVICE_BUSY = 0x06,
};

enum {
    HR_MODE_BASE = 0x0000,
    HR_GPIO_MAP_BASE = 0x0100,
    HR_INFO_BASE = 0x0200,
    HR_INFO_COUNT = 7,
    HR_PWM_BASE = 0x0300,
    HR_PWM_STRIDE = 4,
    IR_RAW_BASE = 0x0000,
    IR_MV_BASE = 0x0100,
    PROTOCOL_VERSION = 0x0102,
    FIRMWARE_VERSION = 0x0102,
};

static uint16_t read_be16(const uint8_t *data)
{
    return (uint16_t)(((uint16_t)data[0] << 8) | data[1]);
}

static void write_be16(uint8_t *data, uint16_t value)
{
    data[0] = (uint8_t)(value >> 8);
    data[1] = (uint8_t)value;
}

uint16_t modbus_rtu_crc16(const uint8_t *data, size_t length)
{
    uint16_t crc = 0xFFFFU;
    for (size_t index = 0; index < length; ++index) {
        crc ^= data[index];
        for (uint8_t bit = 0; bit < 8U; ++bit) {
            if ((crc & 1U) != 0U) {
                crc = (uint16_t)((crc >> 1) ^ 0xA001U);
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

bool modbus_rtu_crc_valid(const uint8_t *frame, size_t length)
{
    if ((frame == NULL) || (length < 4U)) {
        return false;
    }
    const uint16_t received = (uint16_t)(frame[length - 2U] |
                                         ((uint16_t)frame[length - 1U] << 8));
    return modbus_rtu_crc16(frame, length - 2U) == received;
}

size_t modbus_server_expected_request_length(const uint8_t *data, size_t length)
{
    if ((data == NULL) || (length < 2U)) {
        return 0U;
    }

    switch (data[1]) {
    case FC_WRITE_MULTIPLE_COILS:
    case FC_WRITE_MULTIPLE_REGISTERS:
        if (length < 7U) {
            return 0U;
        }
        if (data[6] > (MODBUS_RTU_MAX_ADU_SIZE - 9U)) {
            return SIZE_MAX;
        }
        return 9U + data[6];
    case FC_DEBUGGER:
        if (length < 4U) {
            return 0U;
        }
        return data[3] <= (MODBUS_RTU_MAX_ADU_SIZE - 6U)
                   ? (size_t)data[3] + 6U : SIZE_MAX;
    default:
        /* All supported fixed-length requests and unsupported-function probes. */
        return 8U;
    }
}

static void append_crc(uint8_t *frame, size_t payload_length)
{
    const uint16_t crc = modbus_rtu_crc16(frame, payload_length);
    frame[payload_length] = (uint8_t)crc;
    frame[payload_length + 1U] = (uint8_t)(crc >> 8);
}

static esp_err_t make_exception(uint8_t slave, uint8_t function, uint8_t exception,
                                uint8_t *response, size_t capacity, size_t *length)
{
    if (capacity < 5U) {
        return ESP_ERR_NO_MEM;
    }
    response[0] = slave;
    response[1] = function | 0x80U;
    response[2] = exception;
    append_crc(response, 3U);
    *length = 5U;
    return ESP_OK;
}

static uint8_t exception_for_error(esp_err_t error)
{
    if (error == ESP_ERR_NOT_FOUND) {
        return EX_SERVER_DEVICE_BUSY;
    }
    if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_NOT_SUPPORTED) {
        return EX_ILLEGAL_DATA_ADDRESS;
    }
    if (error == ESP_ERR_INVALID_STATE) {
        return EX_SERVER_DEVICE_FAILURE;
    }
    return EX_SERVER_DEVICE_FAILURE;
}

static bool valid_read_quantity(uint16_t quantity, uint16_t maximum)
{
    return (quantity > 0U) && (quantity <= maximum);
}

static esp_err_t read_bits(uint8_t function, uint16_t start, uint16_t quantity,
                           uint8_t slave, uint8_t *response, size_t capacity,
                           size_t *response_length)
{
    if (!valid_read_quantity(quantity, 2000U)) {
        return make_exception(slave, function, EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }
    if (((uint32_t)start + quantity) > IO_DIGITAL_CHANNEL_COUNT) {
        return make_exception(slave, function, EX_ILLEGAL_DATA_ADDRESS,
                              response, capacity, response_length);
    }

    const uint8_t byte_count = (uint8_t)((quantity + 7U) / 8U);
    if (capacity < (size_t)byte_count + 5U) {
        return ESP_ERR_NO_MEM;
    }

    response[0] = slave;
    response[1] = function;
    response[2] = byte_count;
    memset(&response[3], 0, byte_count);

    for (uint16_t offset = 0; offset < quantity; ++offset) {
        bool value = false;
        esp_err_t error = (function == FC_READ_COILS)
                              ? io_model_read_output(start + offset, &value)
                              : io_model_read_digital(start + offset, &value);
        if (error != ESP_OK) {
            return make_exception(slave, function, exception_for_error(error),
                                  response, capacity, response_length);
        }
        if (value) {
            response[3U + (offset / 8U)] |= (uint8_t)(1U << (offset % 8U));
        }
    }

    const size_t payload_length = 3U + byte_count;
    append_crc(response, payload_length);
    *response_length = payload_length + 2U;
    return ESP_OK;
}

static esp_err_t read_holding_register(uint16_t address, uint16_t *value)
{
    if (address < (HR_MODE_BASE + IO_DIGITAL_CHANNEL_COUNT)) {
        return io_model_get_mode(address - HR_MODE_BASE, value);
    }
    if ((address >= HR_GPIO_MAP_BASE) &&
        (address < (HR_GPIO_MAP_BASE + IO_DIGITAL_CHANNEL_COUNT))) {
        const uint16_t channel = address - HR_GPIO_MAP_BASE;
        if (!io_model_channel_available(channel)) {
            return ESP_ERR_INVALID_ARG;
        }
        return io_model_get_gpio(channel, value);
    }
    if ((address >= HR_INFO_BASE) && (address < (HR_INFO_BASE + HR_INFO_COUNT))) {
        switch (address - HR_INFO_BASE) {
        case 0:
            *value = PROTOCOL_VERSION;
            break;
        case 1:
            *value = FIRMWARE_VERSION;
            break;
        case 2:
            *value = IO_DIGITAL_CHANNEL_COUNT;
            break;
        case 3:
            *value = IO_ANALOG_CHANNEL_COUNT;
            break;
        case 4:
            *value = (uint16_t)(4U | 8U | 16U | 32U | (io_model_calibration_supported() ? 1U : 0U) |
#if CONFIG_USB_MODBUS_ENABLE_GPIO35_37
                                2U
#else
                                0U
#endif
            );
            break;
        case 5:
            *value = IO_PWM_MAX_CHANNELS;
            break;
        case 6:
            *value = IO_PWM_MAX_TIMERS;
            break;
        default:
            return ESP_ERR_INVALID_ARG;
        }
        return ESP_OK;
    }
    if ((address >= HR_PWM_BASE) &&
        (address < HR_PWM_BASE + HR_PWM_STRIDE * IO_DIGITAL_CHANNEL_COUNT)) {
        const uint16_t channel = (address - HR_PWM_BASE) / HR_PWM_STRIDE;
        const uint16_t field = (address - HR_PWM_BASE) % HR_PWM_STRIDE;
        uint32_t frequency = 0;
        uint16_t duty = 0;
        bool enabled = false;
        const esp_err_t error = io_model_pwm_get(channel, &frequency, &duty, &enabled);
        if (error != ESP_OK) {
            return error;
        }
        switch (field) {
        case 0: *value = (uint16_t)(frequency >> 16); break;
        case 1: *value = (uint16_t)frequency; break;
        case 2: *value = duty; break;
        case 3: *value = enabled ? 1U : 0U; break;
        }
        return ESP_OK;
    }
    return ESP_ERR_INVALID_ARG;
}

static esp_err_t read_registers(uint8_t function, uint16_t start, uint16_t quantity,
                                uint8_t slave, uint8_t *response, size_t capacity,
                                size_t *response_length)
{
    if (!valid_read_quantity(quantity, 125U)) {
        return make_exception(slave, function, EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }

    if ((uint32_t)start + quantity > 0x10000UL) {
        return make_exception(slave, function, EX_ILLEGAL_DATA_ADDRESS,
                              response, capacity, response_length);
    }
    const size_t byte_count = (size_t)quantity * 2U;
    if (capacity < byte_count + 5U) {
        return ESP_ERR_NO_MEM;
    }
    response[0] = slave;
    response[1] = function;
    response[2] = (uint8_t)byte_count;

    for (uint16_t offset = 0; offset < quantity; ++offset) {
        uint16_t value = 0;
        esp_err_t error = ESP_ERR_INVALID_ARG;
        const uint16_t address = start + offset;

        if (function == FC_READ_HOLDING_REGISTERS) {
            error = read_holding_register(address, &value);
        } else if (address < (IR_RAW_BASE + IO_ANALOG_CHANNEL_COUNT)) {
            error = io_model_read_analog_raw(address - IR_RAW_BASE, &value);
        } else if ((address >= IR_MV_BASE) &&
                   (address < (IR_MV_BASE + IO_ANALOG_CHANNEL_COUNT))) {
            error = io_model_read_analog_mv(address - IR_MV_BASE, &value);
        }

        if (error != ESP_OK) {
            return make_exception(slave, function, exception_for_error(error),
                                  response, capacity, response_length);
        }
        write_be16(&response[3U + ((size_t)offset * 2U)], value);
    }

    const size_t payload_length = 3U + byte_count;
    append_crc(response, payload_length);
    *response_length = payload_length + 2U;
    return ESP_OK;
}

static esp_err_t write_single_coil(const uint8_t *request, bool broadcast,
                                   uint8_t *response, size_t capacity,
                                   size_t *response_length)
{
    const uint16_t channel = read_be16(&request[2]);
    const uint16_t encoded_value = read_be16(&request[4]);
    if ((encoded_value != 0xFF00U) && (encoded_value != 0x0000U)) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }

    const esp_err_t error = io_model_write_output(channel, encoded_value == 0xFF00U);
    if (error != ESP_OK) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], exception_for_error(error),
                              response, capacity, response_length);
    }

    if (!broadcast) {
        if (capacity < 8U) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(response, request, 8U);
        *response_length = 8U;
    }
    return ESP_OK;
}

static esp_err_t write_single_register(const uint8_t *request, bool broadcast,
                                       uint8_t *response, size_t capacity,
                                       size_t *response_length)
{
    const uint16_t address = read_be16(&request[2]);
    const uint16_t value = read_be16(&request[4]);
    if ((address >= HR_PWM_BASE) &&
        (address < HR_PWM_BASE + HR_PWM_STRIDE * IO_DIGITAL_CHANNEL_COUNT)) {
        if (broadcast) {
            return ESP_OK;
        }
        /* A frequency, duty and enable flag must be sent as one record. */
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }
    esp_err_t error = ESP_ERR_INVALID_ARG;
    if (address < IO_DIGITAL_CHANNEL_COUNT) {
        error = io_model_validate_mode(address, value);
        if (error == ESP_OK) {
            error = io_model_set_mode(address, value);
        }
    }

    if (error != ESP_OK) {
        if (broadcast) {
            return ESP_OK;
        }
        const uint8_t exception = (value > IO_MODE_PWM)
                                      ? EX_ILLEGAL_DATA_VALUE
                                      : exception_for_error(error);
        return make_exception(request[0], request[1], exception,
                              response, capacity, response_length);
    }

    if (!broadcast) {
        if (capacity < 8U) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(response, request, 8U);
        *response_length = 8U;
    }
    return ESP_OK;
}

static esp_err_t write_multiple_coils(const uint8_t *request, size_t request_length,
                                      bool broadcast, uint8_t *response, size_t capacity,
                                      size_t *response_length)
{
    const uint16_t start = read_be16(&request[2]);
    const uint16_t quantity = read_be16(&request[4]);
    const uint8_t byte_count = request[6];

    if (!valid_read_quantity(quantity, 1968U) ||
        (byte_count != ((quantity + 7U) / 8U)) ||
        (request_length != (size_t)byte_count + 9U)) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }
    if (((uint32_t)start + quantity) > IO_DIGITAL_CHANNEL_COUNT) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_ADDRESS,
                              response, capacity, response_length);
    }
    for (uint16_t offset = 0; offset < quantity; ++offset) {
        if (!io_model_channel_available(start + offset)) {
            if (broadcast) {
                return ESP_OK;
            }
            return make_exception(request[0], request[1], EX_ILLEGAL_DATA_ADDRESS,
                                  response, capacity, response_length);
        }
        uint16_t mode;
        if (io_model_get_mode(start + offset, &mode) != ESP_OK || mode >= IO_MODE_I2C) {
            if (broadcast) {
                return ESP_OK;
            }
            return make_exception(request[0], request[1], EX_SERVER_DEVICE_FAILURE,
                                  response, capacity, response_length);
        }
    }
    for (uint16_t offset = 0; offset < quantity; ++offset) {
        const bool value = ((request[7U + (offset / 8U)] >> (offset % 8U)) & 1U) != 0U;
        const esp_err_t error = io_model_write_output(start + offset, value);
        if (error != ESP_OK) {
            if (broadcast) {
                return ESP_OK;
            }
            return make_exception(request[0], request[1], exception_for_error(error),
                                  response, capacity, response_length);
        }
    }

    if (!broadcast) {
        if (capacity < 8U) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(response, request, 6U);
        append_crc(response, 6U);
        *response_length = 8U;
    }
    return ESP_OK;
}

static esp_err_t write_multiple_registers(const uint8_t *request, size_t request_length,
                                          bool broadcast, uint8_t *response, size_t capacity,
                                          size_t *response_length)
{
    const uint16_t start = read_be16(&request[2]);
    const uint16_t quantity = read_be16(&request[4]);
    const uint8_t byte_count = request[6];

    if (!valid_read_quantity(quantity, 123U) || (byte_count != (quantity * 2U)) ||
        (request_length != (size_t)byte_count + 9U)) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_VALUE,
                              response, capacity, response_length);
    }
    if ((start >= HR_PWM_BASE) &&
        (start < HR_PWM_BASE + HR_PWM_STRIDE * IO_DIGITAL_CHANNEL_COUNT)) {
        uint8_t exception = 0U;
        const uint16_t channel = (start - HR_PWM_BASE) / HR_PWM_STRIDE;
        if (((start - HR_PWM_BASE) % HR_PWM_STRIDE != 0U) ||
            (quantity != HR_PWM_STRIDE)) {
            exception = EX_ILLEGAL_DATA_VALUE;
        } else if (!io_model_channel_available(channel)) {
            exception = EX_ILLEGAL_DATA_ADDRESS;
        } else {
            const uint32_t frequency = ((uint32_t)read_be16(&request[7]) << 16) |
                                       read_be16(&request[9]);
            const uint16_t duty = read_be16(&request[11]);
            const uint16_t enabled = read_be16(&request[13]);
            if ((frequency < IO_PWM_MIN_FREQUENCY_HZ) ||
                (frequency > IO_PWM_MAX_FREQUENCY_HZ) ||
                (duty > IO_PWM_DUTY_MAX) || (enabled > 1U)) {
                exception = EX_ILLEGAL_DATA_VALUE;
            } else {
                const esp_err_t error = io_model_pwm_configure(channel, frequency,
                                                               duty, enabled != 0U);
                if (error != ESP_OK) {
                    exception = exception_for_error(error);
                }
            }
        }
        if (broadcast) {
            return ESP_OK;
        }
        if (exception != 0U) {
            return make_exception(request[0], request[1], exception,
                                  response, capacity, response_length);
        }
        if (capacity < 8U) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(response, request, 6U);
        append_crc(response, 6U);
        *response_length = 8U;
        return ESP_OK;
    }
    if (((uint32_t)start + quantity) > IO_DIGITAL_CHANNEL_COUNT) {
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_DATA_ADDRESS,
                              response, capacity, response_length);
    }

    for (uint16_t offset = 0; offset < quantity; ++offset) {
        const uint16_t value = read_be16(&request[7U + ((size_t)offset * 2U)]);
        /* Enable PWM one channel at a time: shared resources may be exhausted. */
        if ((value == IO_MODE_PWM) && (quantity != 1U)) {
            if (broadcast) {
                return ESP_OK;
            }
            return make_exception(request[0], request[1], EX_ILLEGAL_DATA_VALUE,
                                  response, capacity, response_length);
        }
        const esp_err_t error = io_model_validate_mode(start + offset, value);
        if (error != ESP_OK) {
            if (broadcast) {
                return ESP_OK;
            }
            const uint8_t exception = (value > IO_MODE_PWM)
                                          ? EX_ILLEGAL_DATA_VALUE
                                          : exception_for_error(error);
            return make_exception(request[0], request[1], exception,
                                  response, capacity, response_length);
        }
    }

    for (uint16_t offset = 0; offset < quantity; ++offset) {
        const uint16_t value = read_be16(&request[7U + ((size_t)offset * 2U)]);
        const esp_err_t error = io_model_set_mode(start + offset, value);
        if (error != ESP_OK) {
            if (broadcast) {
                return ESP_OK;
            }
            return make_exception(request[0], request[1], exception_for_error(error),
                                  response, capacity, response_length);
        }
    }

    if (!broadcast) {
        if (capacity < 8U) {
            return ESP_ERR_NO_MEM;
        }
        memcpy(response, request, 6U);
        append_crc(response, 6U);
        *response_length = 8U;
    }
    return ESP_OK;
}

static esp_err_t debugger_request(const uint8_t *request, uint8_t *response,
                                  size_t capacity, size_t *response_length)
{
    if (capacity < 6U) {
        return ESP_ERR_NO_MEM;
    }
    size_t result_length = 0;
    esp_err_t error = bus_debugger_command(request[2], &request[4], request[3],
                                           &response[4], capacity - 6U, &result_length);
    if (error != ESP_OK) {
        const uint8_t exception = error == ESP_ERR_NOT_SUPPORTED ? EX_ILLEGAL_FUNCTION
            : error == ESP_ERR_INVALID_ARG ? EX_ILLEGAL_DATA_VALUE
            : error == ESP_ERR_NOT_FOUND ? EX_SERVER_DEVICE_BUSY
            : EX_SERVER_DEVICE_FAILURE;
        return make_exception(request[0], request[1], exception, response,
                              capacity, response_length);
    }
    if (result_length > 250U || result_length > capacity - 6U) {
        return ESP_ERR_INVALID_SIZE;
    }
    response[0] = request[0];
    response[1] = FC_DEBUGGER;
    response[2] = request[2];
    response[3] = (uint8_t)result_length;
    append_crc(response, result_length + 4U);
    *response_length = result_length + 6U;
    return ESP_OK;
}

esp_err_t modbus_server_process(const uint8_t *request, size_t request_length,
                                uint8_t *response, size_t response_capacity,
                                size_t *response_length)
{
    if ((request == NULL) || (response == NULL) || (response_length == NULL) ||
        (request_length < 4U)) {
        return ESP_ERR_INVALID_ARG;
    }
    *response_length = 0U;
    if (!modbus_rtu_crc_valid(request, request_length)) {
        return ESP_ERR_INVALID_CRC;
    }
    /* Validate length before handlers read function-specific fields. */
    const size_t expected = modbus_server_expected_request_length(request, request_length);
    if ((request_length > MODBUS_RTU_MAX_ADU_SIZE) ||
        (expected == 0U) || (expected == SIZE_MAX) || (expected != request_length)) {
        return ESP_ERR_INVALID_SIZE;
    }

    const bool broadcast = request[0] == 0U;
    if (!broadcast && (request[0] != CONFIG_USB_MODBUS_SLAVE_ADDRESS)) {
        return ESP_OK;
    }

    const uint8_t function = request[1];
    const bool is_write = (function == FC_WRITE_SINGLE_COIL) ||
                          (function == FC_WRITE_SINGLE_REGISTER) ||
                          (function == FC_WRITE_MULTIPLE_COILS) ||
                          (function == FC_WRITE_MULTIPLE_REGISTERS);
    if (broadcast && !is_write) {
        return ESP_OK;
    }

    switch (function) {
    case FC_READ_COILS:
    case FC_READ_DISCRETE_INPUTS:
        return read_bits(function, read_be16(&request[2]), read_be16(&request[4]),
                         request[0], response, response_capacity, response_length);
    case FC_READ_HOLDING_REGISTERS:
    case FC_READ_INPUT_REGISTERS:
        return read_registers(function, read_be16(&request[2]), read_be16(&request[4]),
                              request[0], response, response_capacity, response_length);
    case FC_WRITE_SINGLE_COIL:
        return write_single_coil(request, broadcast, response, response_capacity,
                                 response_length);
    case FC_WRITE_SINGLE_REGISTER:
        return write_single_register(request, broadcast, response, response_capacity,
                                     response_length);
    case FC_WRITE_MULTIPLE_COILS:
        return write_multiple_coils(request, request_length, broadcast, response,
                                    response_capacity, response_length);
    case FC_WRITE_MULTIPLE_REGISTERS:
        return write_multiple_registers(request, request_length, broadcast, response,
                                        response_capacity, response_length);
    case FC_DEBUGGER:
        return debugger_request(request, response, response_capacity, response_length);
    default:
        if (broadcast) {
            return ESP_OK;
        }
        return make_exception(request[0], request[1], EX_ILLEGAL_FUNCTION,
                              response, response_capacity, response_length);
    }
}
