#!/usr/bin/env python3
"""Lista los dispositivos USB que ve libusb (diagnóstico)."""

import usb.core
import usb.util

from fbdump.device import _find_backend

kwargs = {"find_all": True}
backend = _find_backend()
if backend is not None:
    kwargs["backend"] = backend

for d in usb.core.find(**kwargs):
    try:
        name = usb.util.get_string(d, d.iProduct) if d.iProduct else ""
    except (usb.core.USBError, ValueError, NotImplementedError):
        name = "(sin acceso a strings)"
    print(f"{d.idVendor:04x}:{d.idProduct:04x}  bus {d.bus} addr {d.address}  {name}")
