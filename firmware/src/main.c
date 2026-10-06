#include "pico/stdlib.h"
#include "tusb.h"

#include "speedtest/speedtest.h"

int main(void)
{
    // Sin board_init() de TinyUSB: en la placa "pico" configura el LED
    // (GPIO25) y la UART (GPIO0/1), que en el DSPico pueden ser pines del bus.
    speedtest_init();
    tud_init(0);

    // Bucle principal en core 0. Core 1 queda libre para el bus NTR (PIO)
    // cuando se implemente; ver src/ntr/README.md.
    while (true)
        tud_task();
}
