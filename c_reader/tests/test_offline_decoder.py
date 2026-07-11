#!/usr/bin/env python3
"""Exercise checksum verification and both offline protocol decoders."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
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
    values = [12.5, 25.0, 180.0, 1.25, -46.8, 50.0, 998.6, float("nan")]
    values.extend(float(index) for index in range(8, 21))
    payload = bytes([1, 3, 84]) + b"".join(cdab(value) for value in values)
    checksum = crc16(payload)
    return payload + bytes([checksum & 0xFF, checksum >> 8])


def raw_record(sensor: str, response: bytes, sequence: int) -> dict:
    record = {
        "schema": "aws.raw.v1",
        "session_id": "offline-test",
        "sensor": sensor,
        "sequence": sequence,
        "wall_time": "2026-07-11T00:00:00.000000000Z",
        "realtime_ns": 1,
        "monotonic_ns": sequence,
        "status": "ok",
        "response_len": len(response),
        "response_hex": response.hex(),
    }
    if sensor == "modbus_rtu":
        record.update({"slave": 1, "start_register": 0, "register_count": 42})
    return record


def main() -> int:
    decoder = Path(sys.argv[1]).resolve()
    p2 = (
        b"TYP OP4A\r\n01:0001.000\r\n10:25439\r\n17:12.0\r\n25:000\r\n"
        b"94:0001;0002;\r\n95:0.00;1.00;\r\n96:0000001;0000002;\r\n"
        b"97:;\r\n98:;\r\n99:;\r\n\x03"
    )
    rows = [raw_record("parsivel2", p2, 1), raw_record("modbus_rtu", modbus_response(), 1)]
    with tempfile.TemporaryDirectory(prefix="aws-offline-decode-") as temporary:
        root = Path(temporary)
        archive = root / "raw.jsonl.gz"
        with gzip.open(archive, "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
        output = root / "parsed.jsonl"
        process = subprocess.run(
            [sys.executable, str(decoder), str(archive), "--require-sha256", "--output", str(output)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if process.returncode != 0:
            raise AssertionError(f"decoder failed\n{process.stdout}\n{process.stderr}")
        decoded = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        p2_result = next(row for row in decoded if row["sensor"] == "parsivel2")
        modbus_result = next(row for row in decoded if row["sensor"] == "modbus_rtu")
        assert p2_result["parse_status"] == "ok"
        assert p2_result["data"]["type"] == "combined"
        assert p2_result["data"]["rain_intensity"] == 0.001
        assert p2_result["data"]["psd_size_classes"] == [1, 2]
        assert modbus_result["parse_status"] == "ok"
        fields = modbus_result["data"]["fields"]
        assert math.isclose(fields["Batt_volt_Min"]["value"], 12.5)
        assert math.isclose(fields["Airtemp_Avg"]["value"], -46.8, abs_tol=1e-3)
        assert fields["Dew_temp_Avg"]["value"] is None
        assert modbus_result["data"]["nan_fields"] == ["Dew_temp_Avg"]
        assert modbus_result["source_sha256"] == digest

    print("test_offline_decoder: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
