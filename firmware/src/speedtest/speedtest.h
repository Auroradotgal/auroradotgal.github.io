// Test de velocidad USB: TX (dispositivo -> host), RX (host -> dispositivo)
// o ambos a la vez, con verificación opcional de un patrón u32 incremental.

#ifndef FW_SPEEDTEST_H
#define FW_SPEEDTEST_H

#include <stdbool.h>
#include <stdint.h>

#include "fbdump_usb.h"

// Bytes por transferencia bulk. Múltiplo de 64 y de 4.
#define SPEEDTEST_XFER_SIZE (16u * 1024u)

void speedtest_init(void);
bool speedtest_start(uint32_t flags);
void speedtest_stop(void);
void speedtest_get_stats(struct fbdump_stats *out);

#endif
