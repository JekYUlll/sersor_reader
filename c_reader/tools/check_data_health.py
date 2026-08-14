#!/usr/bin/env python3
"""Validate recent aws-reader records and transfer-ready archives."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


SCHEMA = "aws.health-check.v1"
RAW_SCHEMA = "aws.raw.v1"
HEALTH_SCHEMA = "aws.health.v1"
EXPECTED_LENGTHS = {"parsivel2": 5189, "modbus_rtu": 89}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/data/aws"))
    parser.add_argument("--mountpoint", default="/data")
    parser.add_argument("--service", default="aws-reader.service")
    parser.add_argument("--skip-service-check", action="store_true")
    parser.add_argument("--lookback-minutes", type=int, default=20)
    parser.add_argument("--min-samples", type=int, default=30)
    parser.add_argument("--min-success-ratio", type=float, default=0.95)
    parser.add_argument("--max-age-seconds", type=float, default=30.0)
    parser.add_argument("--archive-max-age-minutes", type=int, default=180)
    parser.add_argument("--require-current-session-archive", action="store_true")
    parser.add_argument("--require-device", action="append", default=[])
    parser.add_argument("--min-free-mb", type=int, default=1024)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history-dir", type=Path)
    args = parser.parse_args()
    if args.lookback_minutes <= 0 or args.min_samples <= 0:
        parser.error("lookback and sample limits must be positive")
    if not 0.0 <= args.min_success_ratio <= 1.0:
        parser.error("min-success-ratio must be in 0..1")
    if args.max_age_seconds <= 0 or args.archive_max_age_minutes <= 0:
        parser.error("age limits must be positive")
    return args


def command_output(command: list[str]) -> tuple[int, str]:
    process = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return process.returncode, process.stdout.strip()


def read_json_lines(data: bytes, source: str) -> tuple[list[dict[str, Any]], list[str], int]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    partial_final = 0
    lines = data.splitlines(keepends=True)
    for index, raw_line in enumerate(lines):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            is_final_partial = index == len(lines) - 1 and not raw_line.endswith((b"\n", b"\r"))
            if is_final_partial:
                partial_final += 1
            else:
                errors.append(f"{source}: invalid JSON line {index + 1}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{source}: line {index + 1} is not a JSON object")
            continue
        records.append(value)
    return records, errors, partial_final


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    sidecar = Path(f"{path}.sha256")
    detail: dict[str, Any] = {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256_sidecar": sidecar.exists(),
    }
    if not sidecar.exists():
        errors.append(f"missing SHA-256 sidecar: {path}")
        return [], detail, errors, warnings
    try:
        expected = sidecar.read_text(encoding="ascii").split()[0].lower()
    except (OSError, IndexError, UnicodeDecodeError) as exc:
        errors.append(f"cannot read SHA-256 sidecar {sidecar}: {exc}")
        return [], detail, errors, warnings
    actual = sha256_file(path)
    detail["sha256"] = actual
    detail["sha256_ok"] = actual == expected
    if actual != expected:
        errors.append(f"SHA-256 mismatch: {path}")
        return [], detail, errors, warnings
    try:
        with gzip.open(path, "rb") as stream:
            payload = stream.read()
    except (OSError, EOFError) as exc:
        errors.append(f"cannot decompress {path}: {exc}")
        return [], detail, errors, warnings
    records, parse_errors, partial_final = read_json_lines(payload, str(path))
    errors.extend(parse_errors)
    if partial_final:
        warnings.append(f"{path}: preserved {partial_final} partial final line")
    detail["uncompressed_size"] = len(payload)
    detail["records"] = len(records)
    detail["partial_final_lines"] = partial_final
    return records, detail, errors, warnings


def load_active(path: Path) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return [], [f"cannot read active file {path}: {exc}"], []
    records, errors, partial_final = read_json_lines(payload, str(path))
    warnings = []
    if partial_final:
        warnings.append(f"{path}: write was in progress during inspection")
    return records, errors, warnings


def inspect_service(service: str) -> tuple[dict[str, Any], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    active_code, active = command_output(["systemctl", "is-active", service])
    enabled_code, enabled = command_output(["systemctl", "is-enabled", service])
    show_code, show = command_output(
        [
            "systemctl",
            "show",
            service,
            "-p",
            "MainPID",
            "-p",
            "NRestarts",
            "-p",
            "WatchdogTimestampMonotonic",
            "-p",
            "ActiveEnterTimestampMonotonic",
        ]
    )
    fields: dict[str, str] = {}
    if show_code == 0:
        for line in show.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                fields[key] = value
    detail: dict[str, Any] = {"active": active, "enabled": enabled, **fields}
    if active_code != 0 or active != "active":
        errors.append(f"service is not active: {service} ({active})")
    if enabled_code != 0 or enabled not in {"enabled", "static"}:
        errors.append(f"service is not enabled: {service} ({enabled})")
    if show_code != 0:
        errors.append(f"cannot inspect service properties: {show}")
    try:
        restarts = int(fields.get("NRestarts", "0"))
    except ValueError:
        restarts = 0
    if restarts > 0:
        warnings.append(f"service restart counter is {restarts}")
    return detail, errors, warnings


def inspect_storage(data_dir: Path, mountpoint: str, min_free_mb: int) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    detail: dict[str, Any] = {"data_dir": str(data_dir), "mountpoint": mountpoint}
    if not data_dir.is_dir():
        errors.append(f"data directory does not exist: {data_dir}")
        return detail, errors
    if mountpoint and not os.path.ismount(mountpoint):
        errors.append(f"required mountpoint is not mounted: {mountpoint}")
    usage = shutil.disk_usage(data_dir)
    detail.update({"free_bytes": usage.free, "total_bytes": usage.total})
    if usage.free < min_free_mb * 1024 * 1024:
        errors.append(f"free space is below {min_free_mb} MB")
    return detail, errors


def sequence_gaps(records: list[dict[str, Any]]) -> list[list[int]]:
    sequences = sorted({int(record["sequence"]) for record in records if "sequence" in record})
    return [[left, right] for left, right in zip(sequences, sequences[1:]) if right != left + 1]


def sensor_summary(
    sensor: str,
    raw_records: list[dict[str, Any]],
    health_records: list[dict[str, Any]],
    now_monotonic_ns: int,
    min_samples: int,
    min_success_ratio: float,
    max_age_seconds: float,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    statuses = collections.Counter(str(record.get("status", "missing")) for record in raw_records)
    ok_count = statuses.get("ok", 0)
    success_ratio = ok_count / len(raw_records) if raw_records else 0.0
    gaps = sequence_gaps(raw_records)
    latest_ns = max((int(record.get("monotonic_ns", 0)) for record in raw_records), default=0)
    age_seconds = (now_monotonic_ns - latest_ns) / 1_000_000_000 if latest_ns else None
    expected_length = EXPECTED_LENGTHS[sensor]
    bad_ok_lengths = sorted(
        {
            int(record.get("response_len", -1))
            for record in raw_records
            if record.get("status") == "ok" and int(record.get("response_len", -1)) != expected_length
        }
    )
    detail: dict[str, Any] = {
        "samples": len(raw_records),
        "health_records": len(health_records),
        "status_counts": dict(sorted(statuses.items())),
        "success_ratio": success_ratio,
        "expected_ok_response_len": expected_length,
        "unexpected_ok_response_lengths": bad_ok_lengths,
        "sequence_gaps": gaps,
        "latest_age_seconds": age_seconds,
    }
    if sensor == "modbus_rtu":
        detail["nan_fields_total"] = sum(int(record.get("nan_fields", 0)) for record in health_records)
        detail["nan_fields_max"] = max(
            (int(record.get("nan_fields", 0)) for record in health_records), default=0
        )
    if len(raw_records) < min_samples:
        errors.append(f"{sensor}: only {len(raw_records)} samples, require {min_samples}")
    if success_ratio < min_success_ratio:
        errors.append(
            f"{sensor}: success ratio {success_ratio:.3f} is below {min_success_ratio:.3f}"
        )
    if gaps:
        errors.append(f"{sensor}: sequence gaps detected: {gaps[:5]}")
    if age_seconds is None or age_seconds < -5.0 or age_seconds > max_age_seconds:
        errors.append(f"{sensor}: latest sample age is {age_seconds}")
    if bad_ok_lengths:
        errors.append(f"{sensor}: unexpected lengths for ok records: {bad_ok_lengths}")
    if abs(len(raw_records) - len(health_records)) > 1:
        errors.append(
            f"{sensor}: raw/health count mismatch {len(raw_records)}/{len(health_records)}"
        )
    return detail, errors


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def append_history(directory: Path, report: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    path = directory / f"{day}.jsonl"
    payload = (json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o640)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    args = parse_args()
    now_wall = time.time()
    now_monotonic_ns = time.monotonic_ns()
    errors: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "healthy": False,
        "criteria": {
            "lookback_minutes": args.lookback_minutes,
            "min_samples": args.min_samples,
            "min_success_ratio": args.min_success_ratio,
            "max_age_seconds": args.max_age_seconds,
            "archive_max_age_minutes": args.archive_max_age_minutes,
        },
    }

    storage_detail, storage_errors = inspect_storage(args.data_dir, args.mountpoint, args.min_free_mb)
    report["storage"] = storage_detail
    errors.extend(storage_errors)
    for device in args.require_device:
        if not Path(device).exists():
            errors.append(f"required device is missing: {device}")
    if not args.skip_service_check:
        service_detail, service_errors, service_warnings = inspect_service(args.service)
        report["service"] = service_detail
        errors.extend(service_errors)
        warnings.extend(service_warnings)

    archive_cutoff = now_wall - args.archive_max_age_minutes * 60
    archive_details: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    archive_sessions: set[str] = set()
    for category in ("raw", "health"):
        for path in sorted((args.data_dir / category).glob("**/*.jsonl.gz")):
            if path.stat().st_mtime < archive_cutoff:
                continue
            records, detail, archive_errors, archive_warnings = verify_archive(path)
            detail["session_ids"] = sorted(
                {str(record.get("session_id")) for record in records if record.get("session_id")}
            )
            archive_details.append(detail)
            all_records.extend(records)
            archive_sessions.update(
                str(record.get("session_id")) for record in records if record.get("session_id")
            )
            errors.extend(archive_errors)
            warnings.extend(archive_warnings)

    active_files = sorted(
        path
        for category in ("raw", "health")
        for path in (args.data_dir / category).glob("**/*.jsonl.active")
    )
    for path in active_files:
        records, active_errors, active_warnings = load_active(path)
        all_records.extend(records)
        errors.extend(active_errors)
        warnings.extend(active_warnings)

    raw_records = [record for record in all_records if record.get("schema") == RAW_SCHEMA]
    if raw_records:
        current_session = str(max(raw_records, key=lambda item: int(item.get("realtime_ns", 0)))["session_id"])
    else:
        current_session = ""
        errors.append("no raw records found")
    report["current_session_id"] = current_session
    cutoff_ns = now_monotonic_ns - args.lookback_minutes * 60 * 1_000_000_000
    current_raw = [
        record
        for record in raw_records
        if str(record.get("session_id", "")) == current_session
        and int(record.get("monotonic_ns", 0)) >= cutoff_ns
    ]
    current_health = [
        record
        for record in all_records
        if record.get("schema") == HEALTH_SCHEMA
        and str(record.get("session_id", "")) == current_session
        and int(record.get("monotonic_ns", 0)) >= cutoff_ns
    ]
    sensor_details: dict[str, Any] = {}
    for sensor in EXPECTED_LENGTHS:
        sensor_raw = [record for record in current_raw if record.get("sensor") == sensor]
        sensor_health = [record for record in current_health if record.get("sensor") == sensor]
        detail, sensor_errors = sensor_summary(
            sensor,
            sensor_raw,
            sensor_health,
            now_monotonic_ns,
            args.min_samples,
            args.min_success_ratio,
            args.max_age_seconds,
        )
        sensor_details[sensor] = detail
        errors.extend(sensor_errors)
    report["sensors"] = sensor_details
    report["archives"] = {
        "checked": len(archive_details),
        "current_session_archives": sum(
            1
            for detail in archive_details
            if current_session
            and current_session in detail.get("session_ids", [])
            and detail.get("records", 0) > 0
        ),
        "details": archive_details,
    }
    if not archive_details:
        errors.append("no recent transfer-ready archives found")
    if args.require_current_session_archive and current_session not in archive_sessions:
        errors.append("no verified archive contains the current session")

    report["warnings"] = warnings
    report["errors"] = errors
    report["healthy"] = not errors
    if args.output:
        write_report(args.output, report)
    if args.history_dir:
        append_history(args.history_dir, report)

    state = "HEALTHY" if report["healthy"] else "UNHEALTHY"
    p2 = sensor_details.get("parsivel2", {})
    modbus = sensor_details.get("modbus_rtu", {})
    print(
        f"{state} session={current_session} "
        f"p2={p2.get('status_counts', {})} "
        f"modbus={modbus.get('status_counts', {})} archives={len(archive_details)}"
    )
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
