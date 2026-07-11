#!/usr/bin/env python3
"""Capture raw serial frames for RS485/AWS sensor debugging.

This intentionally records raw request/response bytes first. Parsed values are
only included as a preview so offline parsers can be changed later.
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import math
import os
import select
import struct
import subprocess
import termios
import time
from pathlib import Path


BAUDS = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}

MODBUS_FIELDS = [
    ("Batt_volt_Min", 0, "V"),
    ("PTemp", 2, "C"),
    ("WD", 4, "deg"),
    ("WS_Avg", 6, "m/s"),
    ("Airtemp_Avg", 8, "C"),
    ("RH_Avg", 10, "%"),
    ("BP_Avg", 12, "hPa"),
    ("Dew_temp_Avg", 14, "C"),
    ("LPS_GHI_Avg", 16, "W/m2"),
    ("LPS_GHI_Max", 18, "W/m2"),
    ("Flux_min", 20, "g/m2/s"),
    ("Flux_avg", 22, "g/m2/s"),
    ("Flux_max", 24, "g/m2/s"),
    ("Flux_std", 26, "g/m2/s"),
    ("Flux_cum", 28, "g/m2/s"),
    ("wind_min", 30, "km/h"),
    ("wind_avg", 32, "km/h"),
    ("wind_max", 34, "km/h"),
    ("TargetmV_Avg", 36, "mV"),
    ("DetectorTC_Avg", 38, "C"),
    ("TargetTC_Avg", 40, "C"),
]


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
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


def read_for(fd: int, seconds: float, max_bytes: int = 65536) -> bytes:
    deadline = time.monotonic() + seconds
    chunks: list[bytes] = []
    total = 0
    while time.monotonic() < deadline and total < max_bytes:
        timeout = max(0.0, min(0.2, deadline - time.monotonic()))
        readable, _, _ = select.select([fd], [], [], timeout)
        if not readable:
            continue
        try:
            data = os.read(fd, min(1024, max_bytes - total))
        except BlockingIOError:
            continue
        if not data:
            continue
        chunks.append(data)
        total += len(data)
    return b"".join(chunks)


def open_port(port: str) -> int:
    return os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)


def serial_meta(port: str) -> dict:
    name = os.path.basename(port)
    meta = {"port": port}
    for kind in ("by-id", "by-path"):
        base = Path("/dev/serial") / kind
        matches = []
        if base.exists():
            for item in base.iterdir():
                try:
                    if os.path.realpath(item) == os.path.realpath(port):
                        matches.append(str(item))
                except OSError:
                    pass
        meta[kind.replace("-", "_")] = matches
    sys_dev = Path("/sys/class/tty") / name / "device"
    try:
        meta["sysfs_device"] = str(sys_dev.resolve())
    except OSError:
        meta["sysfs_device"] = str(sys_dev)
    return meta


def encode_bytes(data: bytes) -> dict:
    return {
        "len": len(data),
        "hex": data.hex(" "),
        "base64": base64.b64encode(data).decode("ascii"),
        "ascii_preview": data[:240].decode("ascii", errors="replace").replace("\r", "\\r").replace("\n", "\\n"),
    }


def modbus_request(slave: int, function: int, start: int, qty: int) -> bytes:
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


def classify_modbus(req: bytes, resp: bytes) -> str:
    if not resp:
        return "empty"
    if len(resp) < 5:
        return "short"
    got = resp[-2] | (resp[-1] << 8)
    want = crc16_modbus(resp[:-2])
    if got != want:
        return f"bad_crc got=0x{got:04x} want=0x{want:04x}"
    if resp[0] != req[0]:
        return f"other_slave slave={resp[0]}"
    if resp[1] == (req[1] | 0x80):
        return f"exception code={resp[2] if len(resp) > 2 else 'missing'}"
    if resp[1] != req[1]:
        return f"other_function function={resp[1]}"
    return "ok"


def decode_float_cdab(data: bytes, offset: int) -> float | None:
    raw = data[offset : offset + 4]
    if len(raw) != 4:
        return None
    value = struct.unpack(">f", raw[2:4] + raw[0:2])[0]
    if not math.isfinite(value):
        return None
    return round(value, 4)


def parse_modbus_preview(resp: bytes, start: int, qty: int) -> dict | None:
    if len(resp) < 5 or resp[1] != 3:
        return None
    byte_count = resp[2]
    data = resp[3 : 3 + byte_count]
    if byte_count != qty * 2 or len(data) != byte_count:
        return None
    preview = {}
    for name, reg, unit in MODBUS_FIELDS:
        if reg < start or reg + 1 >= start + qty:
            continue
        offset = (reg - start) * 2
        value = decode_float_cdab(data, offset)
        preview[name] = {"value": value, "unit": unit}
    return preview


def transaction(port: str, baud: int, request: bytes, read_seconds: float) -> tuple[bytes, str | None]:
    fd = open_port(port)
    try:
        configure(fd, baud)
        time.sleep(0.15)
        termios.tcflush(fd, termios.TCIOFLUSH)
        if request:
            os.write(fd, request)
        response = read_for(fd, read_seconds)
        return response, None
    except OSError as exc:
        return b"", str(exc)
    finally:
        os.close(fd)


def write_record(fp, record: dict) -> None:
    fp.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    fp.flush()


def probe_parsivel(fp, port: str, cycle: int, read_seconds: float) -> dict:
    request = b"CS/PA\r"
    response, error = transaction(port, 9600, request, read_seconds)
    status = "ok" if response else "empty"
    if error:
        status = "io_error"
    record = {
        "timestamp": now_iso(),
        "cycle": cycle,
        "sensor_probe": "parsivel2",
        "port": port,
        "baud": 9600,
        "request": encode_bytes(request),
        "response": encode_bytes(response),
        "status": status,
        "error": error,
    }
    write_record(fp, record)
    return record


def probe_modbus(fp, port: str, cycle: int, baud: int, slave: int, start: int, qty: int) -> dict:
    request = modbus_request(slave, 3, start, qty)
    response, error = transaction(port, baud, request, 1.2)
    status = "io_error" if error else classify_modbus(request, response)
    record = {
        "timestamp": now_iso(),
        "cycle": cycle,
        "sensor_probe": "modbus_rtu",
        "port": port,
        "baud": baud,
        "slave": slave,
        "function": 3,
        "start_register": start,
        "quantity_registers": qty,
        "request": encode_bytes(request),
        "response": encode_bytes(response),
        "status": status,
        "error": error,
    }
    if status == "ok":
        record["parsed_preview_cdab_float"] = parse_modbus_preview(response, start, qty)
    write_record(fp, record)
    return record


def command_output(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
    except Exception as exc:
        return f"ERR: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ports", nargs="*", default=sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--parsivel-read-seconds", type=float, default=6.0)
    parser.add_argument("--slaves", default="1,2,3")
    parser.add_argument("--modbus-bauds", default="19200,9600")
    parser.add_argument("--probes", default="parsivel2,modbus", help="Comma-separated: parsivel2,modbus")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"raw_captures/{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "serial_raw_capture.jsonl"
    summary_path = out_dir / "summary.json"

    slaves = [int(x) for x in args.slaves.split(",") if x.strip()]
    modbus_bauds = [int(x) for x in args.modbus_bauds.split(",") if x.strip()]
    probes = {x.strip().lower() for x in args.probes.split(",") if x.strip()}
    summary = {
        "started_at": now_iso(),
        "ports": [serial_meta(p) for p in args.ports],
        "probes": sorted(probes),
        "modbus_bauds": modbus_bauds,
        "jsonl": str(jsonl_path),
        "system": {
            "uname": command_output(["uname", "-a"]),
            "lsusb": command_output(["lsusb"]),
        },
        "events": [],
    }

    with jsonl_path.open("w", encoding="utf-8") as fp:
        write_record(fp, {"timestamp": now_iso(), "event": "capture_start", "summary": summary})
        for cycle in range(1, args.cycles + 1):
            for port in args.ports:
                if "parsivel2" in probes or "parsivel" in probes:
                    rec = probe_parsivel(fp, port, cycle, args.parsivel_read_seconds)
                    summary["events"].append({
                        "probe": "parsivel2",
                        "port": port,
                        "cycle": cycle,
                        "status": rec["status"],
                        "bytes": rec["response"]["len"],
                    })
                if "modbus" in probes or "modbus_rtu" in probes:
                    for baud in modbus_bauds:
                        for slave in slaves:
                            rec = probe_modbus(fp, port, cycle, baud, slave, 0, 2)
                            summary["events"].append({
                                "probe": "modbus_rtu",
                                "port": port,
                                "cycle": cycle,
                                "baud": baud,
                                "slave": slave,
                                "qty": 2,
                                "status": rec["status"],
                                "bytes": rec["response"]["len"],
                            })
                            if rec["status"] == "ok" and baud == 19200 and slave == 1:
                                full = probe_modbus(fp, port, cycle, baud, slave, 0, 42)
                                summary["events"].append({
                                    "probe": "modbus_rtu_full",
                                    "port": port,
                                    "cycle": cycle,
                                    "baud": baud,
                                    "slave": slave,
                                    "qty": 42,
                                    "status": full["status"],
                                    "bytes": full["response"]["len"],
                                    "parsed_preview_cdab_float": full.get("parsed_preview_cdab_float"),
                                })
                time.sleep(args.interval)
        write_record(fp, {"timestamp": now_iso(), "event": "capture_end"})

    summary["finished_at"] = now_iso()
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"raw_jsonl={jsonl_path}")
    print(f"summary={summary_path}")
    for event in summary["events"]:
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
