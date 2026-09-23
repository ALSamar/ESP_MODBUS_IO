#include "esp_err.h"
#include "io_model.h"
#include "bus_debugger.h"
#include "usb_modbus.h"

void app_main(void)
{
    ESP_ERROR_CHECK(io_model_init());
    ESP_ERROR_CHECK(bus_debugger_init());
    ESP_ERROR_CHECK(usb_modbus_start());
}
