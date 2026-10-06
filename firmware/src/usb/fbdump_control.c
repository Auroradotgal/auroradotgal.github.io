// Peticiones de control vendor (EP0). Ver common/include/fbdump_usb.h.

#include <string.h>

#include "hardware/clocks.h"
#include "tusb.h"

#include "fbdump_usb.h"
#include "speedtest/speedtest.h"

extern const uint8_t desc_ms_os_20[];

static struct fbdump_info s_info;
static struct fbdump_stats s_stats;

bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage,
                                const tusb_control_request_t *request)
{
    if (stage != CONTROL_STAGE_SETUP)
        return true;

    switch (request->bRequest) {
    case FBDUMP_REQ_MS_OS_20: {
        if (request->wIndex != 7)
            return false;
        uint16_t total_len;
        memcpy(&total_len, desc_ms_os_20 + 8, 2);
        return tud_control_xfer(rhport, request, (void *)(uintptr_t)desc_ms_os_20, total_len);
    }

    case FBDUMP_REQ_GET_INFO:
        s_info.magic = FBDUMP_INFO_MAGIC;
        s_info.proto_version = FBDUMP_PROTO_VERSION;
        s_info.fw_version = 0x0100;
        s_info.xfer_size = SPEEDTEST_XFER_SIZE;
        s_info.sys_clock_hz = clock_get_hz(clk_sys);
        return tud_control_xfer(rhport, request, &s_info, sizeof(s_info));

    case FBDUMP_REQ_TEST_START:
        if (!speedtest_start(request->wValue))
            return false; // STALL: p. ej. EP IN sin drenar
        return tud_control_status(rhport, request);

    case FBDUMP_REQ_TEST_STOP:
        speedtest_stop();
        return tud_control_status(rhport, request);

    case FBDUMP_REQ_GET_STATS:
        speedtest_get_stats(&s_stats);
        return tud_control_xfer(rhport, request, &s_stats, sizeof(s_stats));

    default:
        return false;
    }
}
