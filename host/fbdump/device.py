"""Acceso al DSPico con firmware fbdump mediante pyusb/libusb."""

import usb.core
import usb.util

from . import protocol as p

# bmRequestType: vendor | device
_REQ_OUT = 0x40
_REQ_IN = 0xC0


def _find_backend():
    # En Windows (y macOS sin libusb del sistema) libusb-package trae la DLL.
    try:
        import libusb_package

        return libusb_package.get_libusb1_backend()
    except ImportError:
        return None


class Device:
    def __init__(self, serial: str | None = None, timeout_ms: int = 1000):
        kwargs = {"idVendor": p.USB_VID, "idProduct": p.USB_PID}
        backend = _find_backend()
        if backend is not None:
            kwargs["backend"] = backend
        if serial:
            kwargs["custom_match"] = lambda d: d.serial_number == serial

        dev = usb.core.find(**kwargs)
        if dev is None:
            raise RuntimeError(
                f"No se encuentra ningún dispositivo {p.USB_VID:04x}:{p.USB_PID:04x}"
            )
        self.dev = dev
        self.timeout_ms = timeout_ms
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # ya configurado (p. ej. por WinUSB)
        usb.util.claim_interface(dev, 0)

    def close(self):
        usb.util.release_interface(self.dev, 0)
        usb.util.dispose_resources(self.dev)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- control ---

    def get_info(self) -> p.Info:
        data = self.dev.ctrl_transfer(
            _REQ_IN, p.REQ_GET_INFO, 0, 0, p.Info.FORMAT.size, self.timeout_ms
        )
        info = p.Info.unpack(data)
        if info.magic != p.INFO_MAGIC:
            raise RuntimeError(f"Magic inesperado: {info.magic:#010x}")
        return info

    def test_start(self, flags: int):
        self.dev.ctrl_transfer(_REQ_OUT, p.REQ_TEST_START, flags, 0, None, self.timeout_ms)

    def test_stop(self):
        self.dev.ctrl_transfer(_REQ_OUT, p.REQ_TEST_STOP, 0, 0, None, self.timeout_ms)

    def get_stats(self) -> p.Stats:
        data = self.dev.ctrl_transfer(
            _REQ_IN, p.REQ_GET_STATS, 0, 0, p.Stats.FORMAT.size, self.timeout_ms
        )
        return p.Stats.unpack(data)

    # --- bulk ---

    def read(self, size: int, timeout_ms: int | None = None):
        return self.dev.read(p.EP_IN, size, timeout_ms or self.timeout_ms)

    def write(self, data, timeout_ms: int | None = None) -> int:
        return self.dev.write(p.EP_OUT, data, timeout_ms or self.timeout_ms)

    def drain_in(self, chunk: int = 64 * 1024) -> int:
        """Lee y descarta lo pendiente en EP IN hasta que no llegue nada."""
        total = 0
        while True:
            try:
                total += len(self.read(chunk, timeout_ms=100))
            except usb.core.USBTimeoutError:
                return total
