// Protocolo USB entre el firmware del DSPico y el host (PC).
//
// Este header es la fuente de verdad: host/fbdump/protocol.py replica estos
// valores y debe mantenerse sincronizado a mano.
//
// Todos los campos multibyte son little-endian.

#ifndef FBDUMP_USB_H
#define FBDUMP_USB_H

#include <stdint.h>

// pid.codes "Test PID": válido solo para desarrollo privado.
#define FBDUMP_USB_VID 0x1209
#define FBDUMP_USB_PID 0x0001

#define FBDUMP_PROTO_VERSION 1

// Endpoints bulk de la interfaz vendor (64 bytes, Full Speed).
#define FBDUMP_EP_OUT 0x01
#define FBDUMP_EP_IN  0x81
#define FBDUMP_EP_SIZE 64

// Peticiones de control: bmRequestType = vendor | device.
enum fbdump_request {
    FBDUMP_REQ_GET_INFO   = 0x01, // IN: struct fbdump_info
    FBDUMP_REQ_TEST_START = 0x02, // wValue = flags FBDUMP_TEST_*; resetea contadores
    FBDUMP_REQ_TEST_STOP  = 0x03, // detiene el test (el host debe drenar EP IN)
    FBDUMP_REQ_GET_STATS  = 0x04, // IN: struct fbdump_stats

    FBDUMP_REQ_MS_OS_20   = 0xEE, // reservado: descriptor MS OS 2.0 (WinUSB)
};

// Flags de FBDUMP_REQ_TEST_START.
#define FBDUMP_TEST_TX      (1u << 0) // dispositivo -> host por EP IN
#define FBDUMP_TEST_RX      (1u << 1) // host -> dispositivo por EP OUT
#define FBDUMP_TEST_PATTERN (1u << 2) // datos = contador u32 incremental desde 0
                                      // (TX: el firmware lo genera; RX: lo verifica)

#define FBDUMP_INFO_MAGIC 0x50444246u // "FBDP"

struct fbdump_info {
    uint32_t magic;          // FBDUMP_INFO_MAGIC
    uint16_t proto_version;  // FBDUMP_PROTO_VERSION
    uint16_t fw_version;     // BCD, 0x0100 = 1.00
    uint32_t xfer_size;      // bytes por transferencia bulk que arma el firmware
    uint32_t sys_clock_hz;
} __attribute__((packed));

struct fbdump_stats {
    uint32_t flags;          // flags del test activo (0 = parado)
    uint32_t elapsed_us;     // desde TEST_START (o hasta TEST_STOP)
    uint64_t tx_bytes;       // bytes completados por EP IN
    uint64_t rx_bytes;       // bytes recibidos por EP OUT
    uint32_t rx_errors;      // palabras u32 que no coinciden con el patrón
    uint32_t tx_xfers;
    uint32_t rx_xfers;
} __attribute__((packed));

#endif
