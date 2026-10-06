#include "speedtest/speedtest.h"

#include <string.h>

#include "pico/time.h"

#include "usb/fbdump_class.h"

#define WORDS_PER_XFER (SPEEDTEST_XFER_SIZE / 4)

// Doble buffer por dirección: mientras uno está en vuelo se prepara
// (TX) o se verifica (RX) el otro.
static uint32_t s_tx_buf[2][WORDS_PER_XFER];
static uint32_t s_rx_buf[2][WORDS_PER_XFER];

static uint32_t s_flags;
static uint64_t s_start_us;
static uint64_t s_stop_us;

static uint8_t s_tx_next;     // buffer listo para enviar a continuación
static uint32_t s_tx_pattern; // siguiente palabra del patrón a generar
static uint8_t s_rx_next;     // buffer que se armará en la próxima recepción
static uint32_t s_rx_pattern; // siguiente palabra esperada

static uint64_t s_tx_bytes;
static uint64_t s_rx_bytes;
static uint32_t s_rx_errors;
static uint32_t s_tx_xfers;
static uint32_t s_rx_xfers;

static void fill_pattern(uint32_t *buf)
{
    uint32_t v = s_tx_pattern;
    for (uint32_t i = 0; i < WORDS_PER_XFER; i++)
        buf[i] = v++;
    s_tx_pattern = v;
}

static void verify_pattern(const uint32_t *buf, uint32_t bytes)
{
    uint32_t words = bytes / 4;
    uint32_t v = s_rx_pattern;
    uint32_t errors = 0;
    for (uint32_t i = 0; i < words; i++, v++)
        errors += (buf[i] != v);
    s_rx_pattern = v;
    s_rx_errors += errors;
}

static void tx_send_next(void)
{
    if (!fbdump_usb_send((const uint8_t *)s_tx_buf[s_tx_next], SPEEDTEST_XFER_SIZE))
        return;
    s_tx_next ^= 1;
    // El buffer que acaba de terminar pasa a ser el siguiente; sin patrón
    // ambos son constantes y no hace falta rellenar.
    if (s_flags & FBDUMP_TEST_PATTERN)
        fill_pattern(s_tx_buf[s_tx_next]);
}

static void rx_arm_next(void)
{
    if (fbdump_usb_receive((uint8_t *)s_rx_buf[s_rx_next], SPEEDTEST_XFER_SIZE))
        s_rx_next ^= 1;
}

static void on_tx_done(uint32_t bytes)
{
    if (!(s_flags & FBDUMP_TEST_TX))
        return;
    s_tx_bytes += bytes;
    s_tx_xfers++;
    tx_send_next();
}

static void on_rx_done(uint8_t *buf, uint32_t bytes)
{
    if (!(s_flags & FBDUMP_TEST_RX))
        return; // parado: se descarta y no se vuelve a armar
    // Rearmar antes de verificar para que el host no espere.
    rx_arm_next();
    s_rx_bytes += bytes;
    s_rx_xfers++;
    if (s_flags & FBDUMP_TEST_PATTERN)
        verify_pattern((const uint32_t *)buf, bytes);
}

static const fbdump_usb_handler_t s_handler = {
    .on_tx_done = on_tx_done,
    .on_rx_done = on_rx_done,
};

void speedtest_init(void)
{
    s_flags = 0;
    fbdump_usb_set_handler(&s_handler);
}

bool speedtest_start(uint32_t flags)
{
    if (!fbdump_usb_ready())
        return false;
    // Una transferencia IN pendiente de un test anterior entregaría datos
    // viejos: el host debe drenar el EP IN tras TEST_STOP.
    if ((flags & FBDUMP_TEST_TX) && fbdump_usb_tx_busy())
        return false;

    s_flags = flags;
    s_tx_bytes = s_rx_bytes = 0;
    s_rx_errors = 0;
    s_tx_xfers = s_rx_xfers = 0;
    s_tx_pattern = s_rx_pattern = 0;
    s_start_us = time_us_64();
    s_stop_us = 0;

    if (flags & FBDUMP_TEST_RX) {
        // Si sigue armada una recepción del test anterior, se reutiliza.
        if (!fbdump_usb_rx_busy())
            rx_arm_next();
    }

    if (flags & FBDUMP_TEST_TX) {
        s_tx_next = 0;
        if (flags & FBDUMP_TEST_PATTERN) {
            fill_pattern(s_tx_buf[0]);
            fill_pattern(s_tx_buf[1]);
        } else {
            memset(s_tx_buf, 0xA5, sizeof(s_tx_buf));
        }
        // Ambos buffers ya están listos: tx_send_next() rellenaría el
        // segundo por encima, así que el primero se envía directamente.
        if (fbdump_usb_send((const uint8_t *)s_tx_buf[0], SPEEDTEST_XFER_SIZE))
            s_tx_next = 1;
    }
    return true;
}

void speedtest_stop(void)
{
    if (s_flags)
        s_stop_us = time_us_64();
    s_flags = 0;
}

void speedtest_get_stats(struct fbdump_stats *out)
{
    uint64_t end = s_flags ? time_us_64() : s_stop_us;
    out->flags = s_flags;
    out->elapsed_us = end > s_start_us ? (uint32_t)(end - s_start_us) : 0;
    out->tx_bytes = s_tx_bytes;
    out->rx_bytes = s_rx_bytes;
    out->rx_errors = s_rx_errors;
    out->tx_xfers = s_tx_xfers;
    out->rx_xfers = s_rx_xfers;
}
