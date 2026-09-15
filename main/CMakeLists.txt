idf_component_register(
    SRCS
        "app_main.c"
        "io_model.c"
        "modbus_server.c"
        "usb_modbus.c"
    INCLUDE_DIRS "."
    REQUIRES
        driver
        esp_adc
        esp_timer
        esp_tinyusb
)
