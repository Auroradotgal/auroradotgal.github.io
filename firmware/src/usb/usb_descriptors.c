#include <string.h>

#include "pico/unique_id.h"
#include "tusb.h"

#include "fbdump_usb.h"

#define FW_VERSION_BCD 0x0100

enum {
    ITF_NUM_FBDUMP = 0,
    ITF_NUM_TOTAL,
};

enum {
    STRID_LANGID = 0,
    STRID_MANUFACTURER,
    STRID_PRODUCT,
    STRID_SERIAL,
    STRID_INTERFACE,
};

//--------------------------------------------------------------------+
// Dispositivo
//--------------------------------------------------------------------+

static const tusb_desc_device_t s_desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0210, // 2.1: necesario para que Windows pida el BOS
    .bDeviceClass = 0x00,
    .bDeviceSubClass = 0x00,
    .bDeviceProtocol = 0x00,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = FBDUMP_USB_VID,
    .idProduct = FBDUMP_USB_PID,
    .bcdDevice = FW_VERSION_BCD,
    .iManufacturer = STRID_MANUFACTURER,
    .iProduct = STRID_PRODUCT,
    .iSerialNumber = STRID_SERIAL,
    .bNumConfigurations = 1,
};

const uint8_t *tud_descriptor_device_cb(void)
{
    return (const uint8_t *)&s_desc_device;
}

//--------------------------------------------------------------------+
// Configuración
//--------------------------------------------------------------------+

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_VENDOR_DESC_LEN)

static const uint8_t s_desc_config[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_VENDOR_DESCRIPTOR(ITF_NUM_FBDUMP, STRID_INTERFACE, FBDUMP_EP_OUT, FBDUMP_EP_IN,
                          FBDUMP_EP_SIZE),
};

const uint8_t *tud_descriptor_configuration_cb(uint8_t index)
{
    (void)index;
    return s_desc_config;
}

//--------------------------------------------------------------------+
// BOS + MS OS 2.0: Windows asigna WinUSB sin instalar drivers (ni Zadig)
//--------------------------------------------------------------------+

#define BOS_TOTAL_LEN (TUD_BOS_DESC_LEN + TUD_BOS_MICROSOFT_OS_DESC_LEN)
#define MS_OS_20_DESC_LEN 0xB2

static const uint8_t s_desc_bos[] = {
    TUD_BOS_DESCRIPTOR(BOS_TOTAL_LEN, 1),
    TUD_BOS_MS_OS_20_DESCRIPTOR(MS_OS_20_DESC_LEN, FBDUMP_REQ_MS_OS_20),
};

const uint8_t *tud_descriptor_bos_cb(void)
{
    return s_desc_bos;
}

const uint8_t desc_ms_os_20[] = {
    // Set header: length, type, windows version, total length
    U16_TO_U8S_LE(0x000A), U16_TO_U8S_LE(MS_OS_20_SET_HEADER_DESCRIPTOR),
    U32_TO_U8S_LE(0x06030000), U16_TO_U8S_LE(MS_OS_20_DESC_LEN),

    // Configuration subset header
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_CONFIGURATION), 0, 0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A),

    // Function subset header
    U16_TO_U8S_LE(0x0008), U16_TO_U8S_LE(MS_OS_20_SUBSET_HEADER_FUNCTION), ITF_NUM_FBDUMP, 0,
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08),

    // Compatible ID: WINUSB
    U16_TO_U8S_LE(0x0014), U16_TO_U8S_LE(MS_OS_20_FEATURE_COMPATBLE_ID),
    'W', 'I', 'N', 'U', 'S', 'B', 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,

    // Registry property: DeviceInterfaceGUIDs
    U16_TO_U8S_LE(MS_OS_20_DESC_LEN - 0x0A - 0x08 - 0x08 - 0x14),
    U16_TO_U8S_LE(MS_OS_20_FEATURE_REG_PROPERTY),
    U16_TO_U8S_LE(0x0007), U16_TO_U8S_LE(0x002A),
    'D', 0, 'e', 0, 'v', 0, 'i', 0, 'c', 0, 'e', 0, 'I', 0, 'n', 0, 't', 0, 'e', 0,
    'r', 0, 'f', 0, 'a', 0, 'c', 0, 'e', 0, 'G', 0, 'U', 0, 'I', 0, 'D', 0, 's', 0, 0, 0,
    U16_TO_U8S_LE(0x0050),
    // "{6E4B3C2A-5F1D-4A8B-9C7E-2D0F1B3A4C5D}" (arbitrario, propio de fbdump)
    '{', 0, '6', 0, 'E', 0, '4', 0, 'B', 0, '3', 0, 'C', 0, '2', 0, 'A', 0, '-', 0,
    '5', 0, 'F', 0, '1', 0, 'D', 0, '-', 0, '4', 0, 'A', 0, '8', 0, 'B', 0, '-', 0,
    '9', 0, 'C', 0, '7', 0, 'E', 0, '-', 0, '2', 0, 'D', 0, '0', 0, 'F', 0, '1', 0,
    'B', 0, '3', 0, 'A', 0, '4', 0, 'C', 0, '5', 0, 'D', 0, '}', 0, 0, 0, 0, 0,
};

TU_VERIFY_STATIC(sizeof(desc_ms_os_20) == MS_OS_20_DESC_LEN, "Tamaño MS OS 2.0 incorrecto");

//--------------------------------------------------------------------+
// Strings
//--------------------------------------------------------------------+

static const char *const s_strings[] = {
    [STRID_MANUFACTURER] = "fbdump",
    [STRID_PRODUCT] = "DSPico fbdump",
    [STRID_SERIAL] = NULL, // se rellena con el ID único de la flash
    [STRID_INTERFACE] = "fbdump bulk",
};

static uint16_t s_desc_str[33];

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
    (void)langid;
    size_t count;

    if (index == STRID_LANGID) {
        s_desc_str[1] = 0x0409;
        count = 1;
    } else if (index == STRID_SERIAL) {
        char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
        pico_get_unique_board_id_string(serial, sizeof(serial));
        count = strlen(serial);
        for (size_t i = 0; i < count; i++)
            s_desc_str[1 + i] = serial[i];
    } else {
        if (index >= TU_ARRAY_SIZE(s_strings) || !s_strings[index])
            return NULL;
        const char *str = s_strings[index];
        count = strlen(str);
        if (count > 32)
            count = 32;
        for (size_t i = 0; i < count; i++)
            s_desc_str[1 + i] = str[i];
    }

    s_desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * count + 2));
    return s_desc_str;
}
