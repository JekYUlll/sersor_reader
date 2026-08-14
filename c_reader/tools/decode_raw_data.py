#!/usr/bin/env python3
"""Verify and decode aws.raw.v1 JSONL or JSONL.GZ files offline."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from parsivel2_parser import parse_telegram  # noqa: E402


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-sha256", action="store_true")
    return parser.parse_args()


def iter_input_files(inputs: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            files.update(item.rglob("*.jsonl"))
            files.update(item.rglob("*.jsonl.gz"))
        elif item.is_file():
            files.add(item)
        else:
            raise FileNotFoundError(item)
    return sorted(files)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path, required: bool) -> str | None:
    sidecar = Path(f"{path}.sha256")
    if not sidecar.exists():
        if required:
            raise ValueError(f"missing SHA-256 sidecar: {path}")
        return None
    expected = sidecar.read_text(encoding="ascii").split()[0].lower()
    actual = sha256_file(path)
    if expected != actual:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return actual


def iter_records(path: Path) -> Iterable[dict[str, Any]]:
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if isinstance(value, dict) and value.get("schema") == "aws.raw.v1":
                yield value


def crc16_modbus(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def decode_modbus(response: bytes, record: dict[str, Any]) -> dict[str, Any]:
    if len(response) < 5:
        raise ValueError("short Modbus response")
    received_crc = response[-2] | response[-1] << 8
    expected_crc = crc16_modbus(response[:-2])
    if received_crc != expected_crc:
        raise ValueError("Modbus CRC mismatch")
    if response[0] != int(record.get("slave", 1)) or response[1] != 3:
        raise ValueError("unexpected Modbus slave or function")
    byte_count = response[2]
    register_count = int(record.get("register_count", byte_count // 2))
    if byte_count != register_count * 2 or len(response) != byte_count + 5:
        raise ValueError("unexpected Modbus byte count")
    start_register = int(record.get("start_register", 0))
    data = response[3 : 3 + byte_count]
    fields: dict[str, dict[str, Any]] = {}
    nan_fields: list[str] = []
    for name, register, unit in MODBUS_FIELDS:
        if register < start_register or register + 1 >= start_register + register_count:
            continue
        offset = (register - start_register) * 2
        raw = data[offset : offset + 4]
        value = struct.unpack(">f", raw[2:4] + raw[0:2])[0]
        if not math.isfinite(value):
            value = None
            nan_fields.append(name)
        fields[name] = {"value": value, "unit": unit}
    return {"fields": fields, "nan_fields": nan_fields}


def decode_parsivel2(response: bytes) -> dict[str, Any]:
    if not response.startswith(b"TYP OP4A") or not response.endswith(b"\x03"):
        raise ValueError("invalid Parsivel2 envelope")
    parsed = parse_telegram(response[:-1].decode("ascii"))
    if parsed is None:
        raise ValueError("Parsivel2 parser returned no data")
    return parsed


def decode_record(record: dict[str, Any], source: Path, source_sha256: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "aws.parsed.v1",
        "source_schema": record.get("schema"),
        "source_file": str(source),
        "source_sha256": source_sha256,
        "session_id": record.get("session_id"),
        "sensor": record.get("sensor"),
        "sequence": record.get("sequence"),
        "wall_time": record.get("wall_time"),
        "realtime_ns": record.get("realtime_ns"),
        "monotonic_ns": record.get("monotonic_ns"),
        "source_status": record.get("status"),
        "parse_status": "source_error",
        "data": None,
    }
    if record.get("status") != "ok":
        return result
    try:
        response = bytes.fromhex(str(record["response_hex"]))
        if len(response) != int(record["response_len"]):
            raise ValueError("response_len does not match response_hex")
        if record.get("sensor") == "parsivel2":
            result["data"] = decode_parsivel2(response)
        elif record.get("sensor") == "modbus_rtu":
            result["data"] = decode_modbus(response, record)
        else:
            raise ValueError(f"unsupported sensor: {record.get('sensor')}")
        result["parse_status"] = "ok"
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, struct.error) as exc:
        result["parse_status"] = "decode_error"
        result["parse_error"] = str(exc)
    return result


def main() -> int:
    args = parse_args()
    output_stream = args.output.open("w", encoding="utf-8") if args.output else sys.stdout
    decode_errors = 0
    records_written = 0
    try:
        for path in iter_input_files(args.inputs):
            source_sha256 = verify_sidecar(path, args.require_sha256 and path.name.endswith(".gz"))
            for record in iter_records(path):
                decoded = decode_record(record, path, source_sha256)
                if decoded["parse_status"] == "decode_error":
                    decode_errors += 1
                output_stream.write(json.dumps(decoded, ensure_ascii=False, sort_keys=True) + "\n")
                records_written += 1
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if output_stream is not sys.stdout:
            output_stream.close()
    print(f"decoded={records_written} decode_errors={decode_errors}", file=sys.stderr)
    return 0 if decode_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
