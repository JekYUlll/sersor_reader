#!/usr/bin/env python3
"""End-to-end test using pseudo terminals for both real sensor protocols."""

from __future__ import annotations

import gzip
import json
import math
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for value in payload:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def cdab(value: float) -> bytes:
    raw = struct.pack(">f", value)
    return raw[2:4] + raw[0:2]


def modbus_response() -> bytes:
    values = [
        12.6875,
        24.25,
        125.0,
        0.05,
        -46.8,
        45.0,
        998.6,
        float("nan"),
    ] + [float(index) for index in range(8, 21)]
    payload = bytes([1, 3, 84]) + b"".join(cdab(value) for value in values)
    checksum = crc16(payload)
    return payload + bytes([checksum & 0xFF, checksum >> 8])


def serve_once(master_fd: int, expected: bytes, response: bytes, errors: list[str]) -> None:
    received = bytearray()
    while len(received) < len(expected):
        ready, _, _ = select.select([master_fd], [], [], 5.0)
        if not ready:
            errors.append(f"timeout waiting for {expected.hex()}")
            return
        received.extend(os.read(master_fd, 1024))
    if bytes(received[: len(expected)]) != expected:
        errors.append(f"request mismatch: {bytes(received).hex()} != {expected.hex()}")
        return
    os.write(master_fd, response)


def write_config(path: Path, data_dir: Path, p2_device: str, modbus_device: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"data_dir={data_dir}",
                "required_mountpoint=",
                "rotate_minutes=60",
                "sync_interval_sec=1",
                "min_free_mb=0",
                "compression_enabled=true",
                "reconnect_delay_ms=100",
                "p2_enabled=true",
                f"p2_device={p2_device}",
                "p2_baud=9600",
                "p2_interval_ms=500",
                "p2_timeout_ms=2000",
                "p2_quiet_ms=100",
                "p2_max_response_bytes=8192",
                "p2_direction_gpio=-1",
                "modbus_enabled=true",
                f"modbus_device={modbus_device}",
                "modbus_baud=19200",
                "modbus_interval_ms=500",
                "modbus_timeout_ms=1000",
                "modbus_slave=1",
                "modbus_start_register=0",
                "modbus_register_count=42",
                "modbus_direction_gpio=-1",
                "",
            ]
        ),
        encoding="ascii",
    )


def read_archives(data_dir: Path) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    invalid_lines: list[str] = []
    archives = sorted(data_dir.rglob("*.jsonl.gz"))
    if len(archives) != 4:
        raise AssertionError(f"expected 4 archives including recovery, got {archives}")
    for archive in archives:
        sidecar = Path(str(archive) + ".sha256")
        if not sidecar.exists():
            raise AssertionError(f"missing sidecar for {archive}")
        subprocess.run(
            ["sha256sum", "-c", "--status", sidecar.name],
            cwd=archive.parent,
            check=True,
        )
        with gzip.open(archive, "rt", encoding="utf-8") as stream:
            for line in stream:
                if line.strip().startswith("{"):
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        invalid_lines.append(line)
    return records, invalid_lines


def test_partial_p2(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="aws-reader-partial-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        master, slave = pty.openpty()
        config = root / "reader.conf"
        config.write_text(
            "\n".join(
                [
                    f"data_dir={data_dir}",
                    "required_mountpoint=",
                    "min_free_mb=0",
                    "compression_enabled=false",
                    "p2_enabled=true",
                    f"p2_device={os.ttyname(slave)}",
                    "p2_interval_ms=500",
                    "p2_timeout_ms=500",
                    "p2_quiet_ms=100",
                    "p2_max_response_bytes=8192",
                    "modbus_enabled=false",
                    "",
                ]
            ),
            encoding="ascii",
        )
        errors: list[str] = []
        server = threading.Thread(
            target=serve_once,
            args=(master, b"CS/PA\r", b"TYP OP4A\r\npartial without ETX", errors),
        )
        server.start()
        process = subprocess.run(
            [str(binary), "--config", str(config), "--once"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        server.join(timeout=1)
        os.close(master)
        os.close(slave)
        if errors:
            raise AssertionError(errors)
        if process.returncode == 0:
            raise AssertionError("partial p2 frame unexpectedly returned success")
        raw_path = next(data_dir.rglob("*parsivel2*.jsonl"))
        record = json.loads(raw_path.read_text(encoding="utf-8").strip())
        assert record["status"] == "short_frame"
        assert bytes.fromhex(record["response_hex"]).endswith(b"without ETX")


def test_serial_reconnect(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="aws-reader-reconnect-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        device_link = root / "p2-device"
        master1, slave1 = pty.openpty()
        device_link.symlink_to(os.ttyname(slave1))
        config = root / "reader.conf"
        config.write_text(
            "\n".join(
                [
                    f"data_dir={data_dir}",
                    "required_mountpoint=",
                    "min_free_mb=0",
                    "compression_enabled=false",
                    "reconnect_delay_ms=100",
                    "p2_enabled=true",
                    f"p2_device={device_link}",
                    "p2_interval_ms=500",
                    "p2_timeout_ms=500",
                    "p2_quiet_ms=100",
                    "p2_max_response_bytes=8192",
                    "modbus_enabled=false",
                    "",
                ]
            ),
            encoding="ascii",
        )
        errors: list[str] = []
        first_server = threading.Thread(
            target=serve_once,
            args=(master1, b"CS/PA\r", b"TYP OP4A\r\nfirst\r\n\x03", errors),
        )
        first_server.start()
        process = subprocess.Popen(
            [str(binary), "--config", str(config)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first_server.join(timeout=5)
        if first_server.is_alive() or errors:
            process.terminate()
            process.communicate(timeout=5)
            raise AssertionError(errors or ["first reconnect fixture timed out"])

        os.close(master1)
        os.close(slave1)
        master2, slave2 = pty.openpty()
        replacement = root / "p2-device.new"
        replacement.symlink_to(os.ttyname(slave2))
        os.replace(replacement, device_link)
        second_server = threading.Thread(
            target=serve_once,
            args=(master2, b"CS/PA\r", b"TYP OP4A\r\nreconnected\r\n\x03", errors),
        )
        second_server.start()
        second_server.join(timeout=5)
        if second_server.is_alive() or errors:
            process.terminate()
            process.communicate(timeout=5)
            raise AssertionError(errors or ["reconnect fixture timed out"])
        time.sleep(0.2)
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        os.close(master2)
        os.close(slave2)
        if process.returncode != 0:
            raise AssertionError(f"reconnect reader failed\nstdout={stdout}\nstderr={stderr}")

        records = []
        for path in data_dir.rglob("*parsivel2*.jsonl"):
            records.extend(json.loads(line) for line in path.read_text().splitlines() if line)
        statuses = [record["status"] for record in sorted(records, key=lambda item: item["sequence"])]
        if statuses[0] != "ok" or statuses[-1] != "ok" or "io_error" not in statuses:
            raise AssertionError(f"unexpected reconnect statuses: {statuses}\nstderr={stderr}")


def test_low_space_guard(binary: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="aws-reader-low-space-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        master, slave = pty.openpty()
        config = root / "reader.conf"
        config.write_text(
            "\n".join(
                [
                    f"data_dir={data_dir}",
                    "required_mountpoint=",
                    "min_free_mb=1000000000",
                    "compression_enabled=false",
                    "p2_enabled=true",
                    f"p2_device={os.ttyname(slave)}",
                    "p2_interval_ms=500",
                    "p2_timeout_ms=500",
                    "p2_quiet_ms=100",
                    "p2_max_response_bytes=8192",
                    "modbus_enabled=false",
                    "",
                ]
            ),
            encoding="ascii",
        )
        errors: list[str] = []
        server = threading.Thread(
            target=serve_once,
            args=(master, b"CS/PA\r", b"TYP OP4A\r\nlow space\r\n\x03", errors),
        )
        server.start()
        process = subprocess.run(
            [str(binary), "--config", str(config), "--once"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        server.join(timeout=1)
        os.close(master)
        os.close(slave)
        if errors or process.returncode != 0:
            raise AssertionError(errors or [process.stderr])
        assert "writes paused" in process.stderr
        assert not list(data_dir.rglob("*.jsonl"))
        assert not list(data_dir.rglob("*.active"))


def main() -> int:
    binary = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="aws-reader-integration-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        interrupted_dir = data_dir / "raw" / "20000101"
        interrupted_dir.mkdir(parents=True)
        interrupted = interrupted_dir / "interrupted.jsonl.active"
        interrupted.write_bytes(b'{"partial":true')

        p2_master, p2_slave = pty.openpty()
        modbus_master, modbus_slave = pty.openpty()
        config = root / "reader.conf"
        write_config(config, data_dir, os.ttyname(p2_slave), os.ttyname(modbus_slave))

        p2_request = b"CS/PA\r"
        modbus_payload = bytes([1, 3, 0, 0, 0, 42])
        checksum = crc16(modbus_payload)
        modbus_request = modbus_payload + bytes([checksum & 0xFF, checksum >> 8])
        errors: list[str] = []
        servers = [
            threading.Thread(
                target=serve_once,
                args=(p2_master, p2_request, b"TYP OP4A\r\nmock parsivel frame\r\n\x03", errors),
            ),
            threading.Thread(
                target=serve_once,
                args=(modbus_master, modbus_request, modbus_response(), errors),
            ),
        ]
        for server in servers:
            server.start()
        process = subprocess.run(
            [str(binary), "--config", str(config), "--once"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        for server in servers:
            server.join(timeout=1)
        os.close(p2_master)
        os.close(p2_slave)
        os.close(modbus_master)
        os.close(modbus_slave)

        if errors:
            raise AssertionError(errors)
        if process.returncode != 0:
            raise AssertionError(
                f"reader failed ({process.returncode})\nstdout={process.stdout}\nstderr={process.stderr}"
            )
        records, invalid_lines = read_archives(data_dir)
        raw = [record for record in records if record.get("schema") == "aws.raw.v1"]
        health = [record for record in records if record.get("schema") == "aws.health.v1"]
        if len(raw) != 2 or len(health) != 2:
            raise AssertionError(f"unexpected records: {records}")
        p2_raw = next(record for record in raw if record["sensor"] == "parsivel2")
        modbus_raw = next(record for record in raw if record["sensor"] == "modbus_rtu")
        modbus_health = next(record for record in health if record["sensor"] == "modbus_rtu")
        assert p2_raw["status"] == "ok"
        assert bytes.fromhex(p2_raw["response_hex"]).startswith(b"TYP OP4A")
        assert modbus_raw["status"] == "ok"
        assert modbus_raw["response_len"] == 89
        assert modbus_health["nan_fields"] == 1
        assert math.isclose(modbus_health["preview"]["Airtemp_Avg"], -46.8, abs_tol=1e-3)
        assert modbus_health["preview"]["Dew_temp_Avg"] is None
        assert not interrupted.exists()
        assert invalid_lines == ['{"partial":true']

    test_partial_p2(binary)
    test_serial_reconnect(binary)
    test_low_space_guard(binary)
    print("integration_mock: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
