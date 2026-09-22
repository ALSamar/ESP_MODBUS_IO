#pragma once
#define ESP_RETURN_ON_ERROR(expr, tag, ...) do { \
    (void)(tag); \
    esp_err_t test_err = (expr); \
    if (test_err != ESP_OK) return test_err; \
} while (0)
