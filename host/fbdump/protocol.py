"""Réplica en Python de common/include/fbdump_usb.h. Mantener sincronizado."""

import struct
from dataclasses import dataclass

USB_VID = 0x1209
USB_PID = 0x0001

PROTO_VERSION = 1

EP_OUT = 0x01
EP_IN = 0x81
EP_SIZE = 64

REQ_GET_INFO = 0x01
REQ_TEST_START = 0x02
REQ_TEST_STOP = 0x03
REQ_GET_STATS = 0x04

TEST_TX = 1 << 0
TEST_RX = 1 << 1
TEST_PATTERN = 1 << 2

INFO_MAGIC = 0x50444246  # "FBDP"


@dataclass
class Info:
    magic: int
    proto_version: int
    fw_version: int
    xfer_size: int
    sys_clock_hz: int

    FORMAT = struct.Struct("<IHHII")

    @classmethod
    def unpack(cls, data: bytes) -> "Info":
        return cls(*cls.FORMAT.unpack(bytes(data)))


@dataclass
class Stats:
    flags: int
    elapsed_us: int
    tx_bytes: int
    rx_bytes: int
    rx_errors: int
    tx_xfers: int
    rx_xfers: int

    FORMAT = struct.Struct("<IIQQIII")

    @classmethod
    def unpack(cls, data: bytes) -> "Stats":
        return cls(*cls.FORMAT.unpack(bytes(data)))
