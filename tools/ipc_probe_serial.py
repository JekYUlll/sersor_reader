#!/usr/bin/env python3
"""Probe USB serial ports on the IPC without external Python packages."""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time


BAUDS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def configure(fd: int, baud: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
    attrs[3] = 0
    attrs[4] = BAUDS[baud]
    attrs[5] = BAUDS[baud]
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def read_for(fd: int, seconds: float, max_bytes: int = 4096) -> bytes:
    deadline = time.monotonic() + seconds
    chunks: list[bytes] = []
    total = 0
    while time.monotonic() < deadline and total < max_bytes:
        timeout = max(0.0, min(0.2, deadline - time.monotonic()))
        readable, _, _ = select.select([fd], [], [], timeout)
        if not readable:
            continue
        try:
            data = os.read(fd, min(512, max_bytes - total))
        except BlockingIOError:
            continue
        if not data:
            continue
        chunks.append(data)
        total += len(data)
    return b"".join(chunks)


def preview(data: bytes, limit: int = 160) -> str:
    text = data[:limit].decode("ascii", errors="replace")
    return text.replace("\r", "\\r").replace("\n", "\\n")


def hex_preview(data: bytes, limit: int = 80) -> str:
    return data[:limit].hex(" ")


def open_port(port: str) -> int:
    return os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)


def probe_parsivel(port: str) -> None:
    try:
        fd = open_port(port)
    except OSError as exc:
        print(f"PARSIVEL {port} open_error {exc}")
        return

    try:
        configure(fd, 9600)
        time.sleep(0.2)
        os.write(fd, b"CS/PA\r")
        data = read_for(fd, 2.5)
        print(f"PARSIVEL {port} bytes={len(data)} ascii='{preview(data)}' hex='{hex_preview(data)}'")
    except OSError as exc:
        print(f"PARSIVEL {port} io_error {exc}")
    finally:
        os.close(fd)


def build_modbus_request(slave: int, function: int, start: int, qty: int) -> bytes:
    frame = bytes([
        slave & 0xFF,
        function & 0xFF,
        (start >> 8) & 0xFF,
        start & 0xFF,
        (qty >> 8) & 0xFF,
        qty & 0xFF,
    ])
    crc = crc16_modbus(frame)
    return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def classify_modbus_response(request: bytes, response: bytes) -> str:
    if not response:
        return "empty"
    if len(response) < 5:
        return "short"
    got_crc = response[-2] | (response[-1] << 8)
    want_crc = crc16_modbus(response[:-2])
    if got_crc != want_crc:
        return f"bad_crc got=0x{got_crc:04x} want=0x{want_crc:04x}"
    if response[0] != request[0]:
        return f"other_slave slave={response[0]}"
    if response[1] == (request[1] | 0x80):
        return f"exception code={response[2] if len(response) > 2 else 'missing'}"
    if response[1] != request[1]:
        return f"other_function function={response[1]}"
    return "ok"


def probe_modbus(port: str, baud: int, slaves: list[int]) -> None:
    try:
        fd = open_port(port)
    except OSError as exc:
        print(f"MODBUS {port} baud={baud} open_error {exc}")
        return

    try:
        configure(fd, baud)
        time.sleep(0.2)
        for slave in slaves:
            request = build_modbus_request(slave, 3, 0, 2)
            termios.tcflush(fd, termios.TCIOFLUSH)
            os.write(fd, request)
            data = read_for(fd, 1.0, max_bytes=256)
            status = classify_modbus_response(request, data)
            print(
                f"MODBUS {port} baud={baud} slave={slave} fc=03 "
                f"bytes={len(data)} status={status} hex='{hex_preview(data)}'"
            )
            time.sleep(0.15)
    except OSError as exc:
        print(f"MODBUS {port} baud={baud} io_error {exc}")
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="*", default=["/dev/ttyUSB0", "/dev/ttyUSB1"])
    parser.add_argument("--slaves", default="1,2,3")
    args = parser.parse_args()

    slaves = [int(x) for x in args.slaves.split(",") if x.strip()]
    print(f"probe_start ports={','.join(args.ports)} slaves={','.join(map(str, slaves))}")
    for port in args.ports:
        print(f"=== PORT {port} ===")
        probe_parsivel(port)
        for baud in (19200, 9600):
            probe_modbus(port, baud, slaves)
    print("probe_done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
