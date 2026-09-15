#include "usb_modbus.h"

#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "modbus_server.h"
#include "sdkconfig.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tinyusb_default_config.h"

#define USB_RX_CHUNK_SIZE 256U
#define USB_RX_QUEUE_DEPTH 8U
#define USB_STREAM_BUFFER_SIZE 512U

typedef struct {
    size_t length;
    uint8_t data[USB_RX_CHUNK_SIZE];
} usb_rx_message_t;

static const char *TAG = "usb_modbus";
static QueueHandle_t s_rx_queue;

static void cdc_rx_callback(int interface, cdcacm_event_t *event)
{
    (void)event;
    usb_rx_message_t message = {0};
    const esp_err_t error = tinyusb_cdcacm_read(interface, message.data,
                                                 sizeof(message.data), &message.length);
    if ((error == ESP_OK) && (message.length > 0U)) {
        if (xQueueSend(s_rx_queue, &message, 0) != pdTRUE) {
            ESP_LOGW(TAG, "RX queue full; dropped %u bytes", (unsigned)message.length);
        }
    }
}

static void discard_stream_prefix(uint8_t *stream, size_t *length, size_t count)
{
    if (count >= *length) {
        *length = 0U;
        return;
    }
    memmove(stream, &stream[count], *length - count);
    *length -= count;
}

static void process_stream(uint8_t *stream, size_t *stream_length)
{
    uint8_t response[MODBUS_RTU_MAX_ADU_SIZE];

    while (*stream_length >= 2U) {
        const size_t expected = modbus_server_expected_request_length(stream, *stream_length);
        if (expected == SIZE_MAX) {
            discard_stream_prefix(stream, stream_length, 1U);
            continue;
        }
        if ((expected == 0U) || (*stream_length < expected)) {
            return;
        }
        if (!modbus_rtu_crc_valid(stream, expected)) {
            /* Search byte-by-byte so a bad frame cannot consume a following request. */
            discard_stream_prefix(stream, stream_length, 1U);
            continue;
        }

        size_t response_length = 0U;
        const esp_err_t error = modbus_server_process(stream, expected, response,
                                                       sizeof(response), &response_length);
        if ((error == ESP_OK) && (response_length > 0U)) {
            tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, response, response_length);
            const esp_err_t flush_error = tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
            if (flush_error != ESP_OK) {
                ESP_LOGW(TAG, "CDC write flush failed: %s", esp_err_to_name(flush_error));
            }
        }
        discard_stream_prefix(stream, stream_length, expected);
    }
}

static void usb_modbus_task(void *argument)
{
    (void)argument;
    uint8_t stream[USB_STREAM_BUFFER_SIZE];
    size_t stream_length = 0U;
    int64_t last_byte_time_us = 0;
    usb_rx_message_t message;

    while (true) {
        if (xQueueReceive(s_rx_queue, &message, pdMS_TO_TICKS(5)) == pdTRUE) {
            last_byte_time_us = esp_timer_get_time();
            if ((stream_length + message.length) > sizeof(stream)) {
                ESP_LOGW(TAG, "RX stream overflow; discarding incomplete data");
                stream_length = 0U;
            }
            if (message.length <= (sizeof(stream) - stream_length)) {
                memcpy(&stream[stream_length], message.data, message.length);
                stream_length += message.length;
                process_stream(stream, &stream_length);
            }
        } else if ((stream_length > 0U) &&
                   ((esp_timer_get_time() - last_byte_time_us) >=
                    ((int64_t)CONFIG_USB_MODBUS_INTERFRAME_TIMEOUT_MS * 1000LL))) {
            /* USB has no meaningful baud-derived 3.5 character interval. */
            ESP_LOGW(TAG, "Incomplete RTU frame timed out; discarding %u bytes",
                     (unsigned)stream_length);
            stream_length = 0U;
        }
    }
}

esp_err_t usb_modbus_start(void)
{
    s_rx_queue = xQueueCreate(USB_RX_QUEUE_DEPTH, sizeof(usb_rx_message_t));
    if (s_rx_queue == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const tinyusb_config_t usb_config = TINYUSB_DEFAULT_CONFIG();
    ESP_RETURN_ON_ERROR(tinyusb_driver_install(&usb_config), TAG, "install TinyUSB driver");

    const tinyusb_config_cdcacm_t cdc_config = {
        .cdc_port = TINYUSB_CDC_ACM_0,
        .callback_rx = cdc_rx_callback,
        .callback_rx_wanted_char = NULL,
        .callback_line_state_changed = NULL,
        .callback_line_coding_changed = NULL,
    };
    ESP_RETURN_ON_ERROR(tinyusb_cdcacm_init(&cdc_config), TAG, "initialize CDC ACM");

    if (xTaskCreate(usb_modbus_task, "usb_modbus", 6144, NULL, 10, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
