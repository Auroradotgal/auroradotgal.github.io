#!/usr/bin/env python3
"""Test de velocidad USB contra el firmware fbdump del DSPico.

Ejemplos:
    python speedtest.py                      # IN (DSPico -> PC), 5 s
    python speedtest.py --mode out --verify  # OUT, el firmware verifica el patrón
    python speedtest.py --mode both -t 10    # ambos sentidos a la vez
"""

import argparse
import sys
import threading
import time
from array import array

import usb.core

from fbdump import protocol as p
from fbdump.device import Device

# Máximo teórico de bulk Full Speed: 19 transacciones de 64 B por frame de 1 ms.
FS_BULK_MAX = 19 * 64 * 1000

assert array("I").itemsize == 4


def pattern_bytes(start: int, words: int) -> bytes:
    return array("I", range(start, start + words)).tobytes()


class Result:
    def __init__(self):
        self.bytes = 0
        self.seconds = 0.0
        self.errors = 0
        self.error_msg = None


def run_in(dev: Device, chunk: int, seconds: float, verify: bool, res: Result):
    expected = 0
    carry = b""
    t0 = time.perf_counter()
    deadline = t0 + seconds
    try:
        while time.perf_counter() < deadline:
            data = dev.read(chunk)
            res.bytes += len(data)
            if not verify:
                continue
            buf = carry + data.tobytes()
            usable = len(buf) & ~3
            carry = buf[usable:]
            words = array("I", buf[:usable])
            ref = array("I", range(expected, expected + len(words)))
            if words != ref:
                bad = sum(1 for a, b in zip(words, ref) if a != b)
                res.errors += bad
                # Resincronizar con el último valor recibido.
                expected = (words[-1] + 1) & 0xFFFFFFFF
            else:
                expected += len(words)
    except usb.core.USBError as e:
        res.error_msg = str(e)
    res.seconds = time.perf_counter() - t0


def run_out(dev: Device, chunk: int, seconds: float, verify: bool, res: Result):
    words = chunk // 4
    value = 0
    fixed = bytes([0x5A]) * chunk
    t0 = time.perf_counter()
    deadline = t0 + seconds
    try:
        while time.perf_counter() < deadline:
            if verify:
                data = pattern_bytes(value, words)
                value += words
            else:
                data = fixed
            res.bytes += dev.write(data)
    except usb.core.USBError as e:
        res.error_msg = str(e)
    res.seconds = time.perf_counter() - t0


def fmt_rate(nbytes: int, seconds: float) -> str:
    if seconds <= 0:
        return "n/a"
    bps = nbytes / seconds
    return (
        f"{bps / 1e6:7.3f} MB/s  ({bps / 2**20:6.3f} MiB/s, {bps * 8 / 1e6:6.2f} Mbit/s, "
        f"{100 * bps / FS_BULK_MAX:5.1f}% del máx. bulk FS)"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mode", choices=["in", "out", "both"], default="in",
                    help="in = DSPico->PC, out = PC->DSPico, both = simultáneo")
    ap.add_argument("-t", "--seconds", type=float, default=5.0)
    ap.add_argument("--chunk", type=int, default=256 * 1024,
                    help="bytes por llamada a libusb (múltiplo del xfer_size del firmware)")
    ap.add_argument("--verify", action="store_true",
                    help="usar el patrón u32 incremental y comprobar integridad")
    ap.add_argument("--serial", help="número de serie si hay varios dispositivos")
    args = ap.parse_args()

    with Device(serial=args.serial) as dev:
        info = dev.get_info()
        print(f"fbdump proto v{info.proto_version}, fw {info.fw_version >> 8}.{info.fw_version & 0xFF:02x}, "
              f"xfer {info.xfer_size} B, sys_clk {info.sys_clock_hz / 1e6:.0f} MHz")

        if args.chunk % info.xfer_size:
            print(f"--chunk debe ser múltiplo de {info.xfer_size}", file=sys.stderr)
            return 2

        dev.test_stop()
        dev.drain_in()

        flags = p.TEST_PATTERN if args.verify else 0
        if args.mode in ("in", "both"):
            flags |= p.TEST_TX
        if args.mode in ("out", "both"):
            flags |= p.TEST_RX
        dev.test_start(flags)

        res_in, res_out = Result(), Result()
        threads = []
        if flags & p.TEST_TX:
            threads.append(threading.Thread(
                target=run_in, args=(dev, args.chunk, args.seconds, args.verify, res_in)))
        if flags & p.TEST_RX:
            threads.append(threading.Thread(
                target=run_out, args=(dev, args.chunk, args.seconds, args.verify, res_out)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        dev.test_stop()
        dev.drain_in()
        st = dev.get_stats()

    ok = True
    dev_s = st.elapsed_us / 1e6
    if flags & p.TEST_TX:
        print(f"\nIN  (DSPico -> PC): {res_in.bytes} B en {res_in.seconds:.2f} s")
        print(f"  host:   {fmt_rate(res_in.bytes, res_in.seconds)}")
        print(f"  device: {fmt_rate(st.tx_bytes, dev_s)}  [{st.tx_xfers} xfers]")
        if args.verify:
            print(f"  errores de patrón (host): {res_in.errors}")
            ok &= res_in.errors == 0
        if res_in.error_msg:
            print(f"  error USB: {res_in.error_msg}")
            ok = False
    if flags & p.TEST_RX:
        print(f"\nOUT (PC -> DSPico): {res_out.bytes} B en {res_out.seconds:.2f} s")
        print(f"  host:   {fmt_rate(res_out.bytes, res_out.seconds)}")
        print(f"  device: {fmt_rate(st.rx_bytes, dev_s)}  [{st.rx_xfers} xfers]")
        if st.rx_bytes != res_out.bytes:
            print(f"  aviso: el dispositivo contó {st.rx_bytes} B")
        if args.verify:
            print(f"  errores de patrón (device): {st.rx_errors}")
            ok &= st.rx_errors == 0
        if res_out.error_msg:
            print(f"  error USB: {res_out.error_msg}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
