// Driver de clase TinyUSB para la interfaz vendor de fbdump.
//
// No usa la clase vendor de TinyUSB: esa clase arma transferencias de un
// solo paquete (64 bytes) a través de un FIFO, y reponerlas en cada vuelta
// de tud_task() limita el throughput. Aquí se arman transferencias grandes
// directamente sobre el endpoint, y el DCD del RP2040 encadena los
// paquetes desde la ISR (con doble buffer en IN).

#ifndef FW_FBDUMP_CLASS_H
#define FW_FBDUMP_CLASS_H

#include <stdbool.h>
#include <stdint.h>

#include "fbdump_usb.h"

// Versión del firmware (BCD). Va en bcdDevice: Windows cachea por
// VID/PID/bcdDevice si el dispositivo tiene descriptor MS OS, así que hay
// que subirla al cambiar ese descriptor.
#define FBDUMP_FW_VERSION_BCD 0x0101

// Tamaño máximo de una transferencia (usbd_edpt_xfer usa uint16_t).
#define FBDUMP_MAX_XFER 65535u

// Callbacks del consumidor actual (speedtest y, más adelante, el streamer
// de framebuffer). Se ejecutan en el contexto de tud_task(), no en la ISR.
typedef struct {
    void (*on_tx_done)(uint32_t bytes);
    void (*on_rx_done)(uint8_t *buf, uint32_t bytes);
} fbdump_usb_handler_t;

void fbdump_usb_set_handler(const fbdump_usb_handler_t *handler);

bool fbdump_usb_ready(void);
bool fbdump_usb_tx_busy(void);
bool fbdump_usb_rx_busy(void);

// Arman una transferencia. El buffer debe seguir vivo hasta el callback.
bool fbdump_usb_send(const uint8_t *buf, uint32_t len);
bool fbdump_usb_receive(uint8_t *buf, uint32_t len);

#endif
