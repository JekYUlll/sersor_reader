#!/usr/bin/env python3
"""Integration test for the production data health checker."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def record(schema: str, sensor: str, session: str, sequence: int, monotonic_ns: int) -> dict:
    value = {
        "schema": schema,
        "sensor": sensor,
        "session_id": session,
        "sequence": sequence,
        "monotonic_ns": monotonic_ns,
        "realtime_ns": time.time_ns(),
        "status": "ok",
        "response_len": 5189 if sensor == "parsivel2" else 89,
    }
    if schema == "aws.health.v1" and sensor == "modbus_rtu":
        value["nan_fields"] = 0
    return value


def write_archive(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(
        (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8") for row in rows
    )
    with gzip.open(path, "wb") as stream:
        stream.write(payload)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(f"{path}.sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")


def write_active(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def run_checker(checker: Path, data_dir: Path, output: Path, ratio: str = "0.95") -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(checker),
            "--data-dir",
            str(data_dir),
            "--mountpoint",
            "",
            "--skip-service-check",
            "--lookback-minutes",
            "10",
            "--min-samples",
            "30",
            "--min-success-ratio",
            ratio,
            "--max-age-seconds",
            "30",
            "--archive-max-age-minutes",
            "60",
            "--require-current-session-archive",
            "--min-free-mb",
            "0",
            "--output",
            str(output),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def main() -> int:
    checker = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="aws-health-check-") as temporary:
        root = Path(temporary)
        data_dir = root / "data"
        session = "test-session"
        now = time.monotonic_ns()
        archive_raw: dict[str, list[dict]] = {"parsivel2": [], "modbus_rtu": []}
        archive_health: list[dict] = []
        active_raw: dict[str, list[dict]] = {"parsivel2": [], "modbus_rtu": []}
        active_health: list[dict] = []
        for sequence in range(1, 41):
            monotonic_ns = now - (40 - sequence) * 10_000_000_000
            target_raw = archive_raw if sequence <= 10 else active_raw
            for sensor in ("parsivel2", "modbus_rtu"):
                target_raw[sensor].append(
                    record("aws.raw.v1", sensor, session, sequence, monotonic_ns)
                )
                health_row = record("aws.health.v1", sensor, session, sequence, monotonic_ns)
                (archive_health if sequence <= 10 else active_health).append(health_row)
        for sensor, rows in archive_raw.items():
            write_archive(data_dir / "raw" / "20260711" / f"{sensor}.jsonl.gz", rows)
            write_active(data_dir / "raw" / "20260711" / f"{sensor}.jsonl.active", active_raw[sensor])
        write_archive(data_dir / "health" / "20260711" / "health.jsonl.gz", archive_health)
        write_active(data_dir / "health" / "20260711" / "health.jsonl.active", active_health)
        ignored_monitor = data_dir / "monitor" / "history" / "broken.jsonl.gz"
        ignored_monitor.parent.mkdir(parents=True)
        ignored_monitor.write_bytes(b"not a gzip archive")

        output = root / "latest.json"
        healthy = run_checker(checker, data_dir, output)
        if healthy.returncode != 0:
            raise AssertionError(f"healthy fixture failed\n{healthy.stdout}\n{healthy.stderr}")
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["healthy"] is True
        assert report["sensors"]["parsivel2"]["samples"] == 40
        assert report["sensors"]["modbus_rtu"]["samples"] == 40
        assert report["archives"]["checked"] == 3

        p2_active = data_dir / "raw" / "20260711" / "parsivel2.jsonl.active"
        rows = [json.loads(line) for line in p2_active.read_text().splitlines()]
        rows[-1]["status"] = "short_frame"
        rows[-1]["response_len"] = 4000
        write_active(p2_active, rows)
        unhealthy = run_checker(checker, data_dir, output, ratio="1.0")
        if unhealthy.returncode == 0:
            raise AssertionError("unhealthy fixture unexpectedly passed")
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["healthy"] is False
        assert any("success ratio" in error for error in report["errors"])

    print("test_health_checker: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
