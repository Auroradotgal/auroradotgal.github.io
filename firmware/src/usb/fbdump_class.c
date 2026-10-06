#include "usb/fbdump_class.h"

#include "tusb.h"
#include "device/usbd_pvt.h"

static const fbdump_usb_handler_t *s_handler;
static uint8_t s_ep_in;
static uint8_t s_ep_out;
static uint8_t *s_rx_buf;

void fbdump_usb_set_handler(const fbdump_usb_handler_t *handler)
{
    s_handler = handler;
}

bool fbdump_usb_ready(void)
{
    return tud_mounted() && s_ep_in && s_ep_out;
}

bool fbdump_usb_tx_busy(void)
{
    return s_ep_in && usbd_edpt_busy(0, s_ep_in);
}

bool fbdump_usb_rx_busy(void)
{
    return s_ep_out && usbd_edpt_busy(0, s_ep_out);
}

bool fbdump_usb_send(const uint8_t *buf, uint32_t len)
{
    if (!fbdump_usb_ready() || len > FBDUMP_MAX_XFER)
        return false;
    if (!usbd_edpt_claim(0, s_ep_in))
        return false;
    // TinyUSB no modifica el buffer en IN; el cast solo sortea la firma.
    bool ok = usbd_edpt_xfer(0, s_ep_in, (uint8_t *)buf, (uint16_t)len);
    if (!ok)
        usbd_edpt_release(0, s_ep_in);
    return ok;
}

bool fbdump_usb_receive(uint8_t *buf, uint32_t len)
{
    if (!fbdump_usb_ready() || len > FBDUMP_MAX_XFER)
        return false;
    if (!usbd_edpt_claim(0, s_ep_out))
        return false;
    s_rx_buf = buf;
    bool ok = usbd_edpt_xfer(0, s_ep_out, buf, (uint16_t)len);
    if (!ok)
        usbd_edpt_release(0, s_ep_out);
    return ok;
}

//--------------------------------------------------------------------+
// Driver de clase
//--------------------------------------------------------------------+

static void fbdump_drv_init(void)
{
    s_ep_in = s_ep_out = 0;
}

static bool fbdump_drv_deinit(void)
{
    return true;
}

static void fbdump_drv_reset(uint8_t rhport)
{
    (void)rhport;
    s_ep_in = s_ep_out = 0;
}

static uint16_t fbdump_drv_open(uint8_t rhport, const tusb_desc_interface_t *itf,
                                uint16_t max_len)
{
    TU_VERIFY(itf->bInterfaceClass == TUSB_CLASS_VENDOR_SPECIFIC, 0);
    TU_VERIFY(itf->bNumEndpoints == 2, 0);

    uint16_t drv_len = sizeof(tusb_desc_interface_t) + 2 * sizeof(tusb_desc_endpoint_t);
    TU_VERIFY(max_len >= drv_len, 0);

    const uint8_t *p_desc = tu_desc_next(itf);
    TU_ASSERT(usbd_open_edpt_pair(rhport, p_desc, 2, TUSB_XFER_BULK, &s_ep_out, &s_ep_in), 0);

    return drv_len;
}

static bool fbdump_drv_control_xfer_cb(uint8_t rhport, uint8_t stage,
                                       const tusb_control_request_t *request)
{
    // Las peticiones vendor llegan por tud_vendor_control_xfer_cb().
    (void)rhport;
    (void)stage;
    (void)request;
    return false;
}

static bool fbdump_drv_xfer_cb(uint8_t rhport, uint8_t ep_addr, xfer_result_t result,
                               uint32_t xferred_bytes)
{
    (void)rhport;
    if (result != XFER_RESULT_SUCCESS)
        xferred_bytes = 0;

    if (ep_addr == s_ep_in) {
        if (s_handler && s_handler->on_tx_done)
            s_handler->on_tx_done(xferred_bytes);
    } else if (ep_addr == s_ep_out) {
        if (s_handler && s_handler->on_rx_done)
            s_handler->on_rx_done(s_rx_buf, xferred_bytes);
    }
    return true;
}

static const usbd_class_driver_t s_driver = {
#if CFG_TUSB_DEBUG >= 2
    .name = "FBDUMP",
#endif
    .init = fbdump_drv_init,
    .deinit = fbdump_drv_deinit,
    .reset = fbdump_drv_reset,
    .open = fbdump_drv_open,
    .control_xfer_cb = fbdump_drv_control_xfer_cb,
    .xfer_cb = fbdump_drv_xfer_cb,
    .sof = NULL,
};

const usbd_class_driver_t *usbd_app_driver_get_cb(uint8_t *driver_count)
{
    *driver_count = 1;
    return &s_driver;
}
