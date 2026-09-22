#pragma once
#include <stdbool.h>
void esp_rom_gpio_connect_out_signal(unsigned gpio, unsigned signal, bool invert, bool output_invert);
