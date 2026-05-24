"""Serial collectors running in QThreads, reusing existing parsing logic."""

import json
import os
import sys
import time

import minimalmodbus
import serial
from PySide6.QtCore import QThread, Signal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from parsivel2_parser import parse_telegram

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

MODBUS_FIELDS = [
    ("Batt_volt_Min",  0),
    ("PTemp",          2),
    ("WD",             4),
    ("WS_Avg",         6),
    ("Airtemp_Avg",    8),
    ("RH_Avg",         10),
    ("BP_Avg",         12),
    ("Dew_temp_Avg",   14),
    ("LPS_GHI_Avg",    16),
    ("LPS_GHI_Max",    18),
    ("Flux_min",       20),
    ("Flux_avg",       22),
    ("Flux_max",       24),
    ("Flux_std",       26),
    ("Flux_cum",       28),
    ("wind_min",       30),
    ("wind_avg",       32),
    ("wind_max",       34),
    ("TargetmV_Avg",   36),
    ("DetectorTC_Avg", 38),
    ("TargetTC_Avg",   40),
]


def _read_parsivel2(ser) -> dict | None:
    ser.reset_input_buffer()
    ser.write(b"CS/PA\r")
    time.sleep(0.6)
    raw = ser.read(ser.in_waiting or 4096)
    data = raw.decode("ascii", errors="replace").strip()
    if not data:
        return None
    result = parse_telegram(data)
    if result is None:
        return None
    result["raw"] = data[:200]
    return result


def _read_modbus(inst) -> dict:
    data = {}
    for name, reg in MODBUS_FIELDS:
        try:
            val = inst.read_float(reg, functioncode=3,
                                  byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP)
            data[name] = round(val, 4)
        except Exception:
            data[name] = None
    return data


def _default_log_dir() -> str:
    return os.path.join(PROJECT_ROOT, "logs")


def _open_log_file(log_dir: str, prefix: str):
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, time.strftime(f"{prefix}_%Y%m%d_%H%M%S.jsonl"))
    return open(log_path, "w", encoding="utf-8")


def _sleep_until_stopped(collector, seconds: float):
    deadline = time.monotonic() + seconds
    while collector._running and time.monotonic() < deadline:
        time.sleep(min(0.1, deadline - time.monotonic()))


class Parsivel2Collector(QThread):
    """Polls Parsivel2 every 5s, emits parsed telegrams."""

    new_data = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, port=None, baud=9600, log_dir=None, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self.log_dir = log_dir or _default_log_dir()
        self._running = False
        self._log_file = None

    def run(self):
        self._running = True
        if not self.port or not str(self.port).strip():
            msg = "Parsivel2 port is not configured"
            print(f"[Parsivel2] {msg}", flush=True)
            self.error_occurred.emit(msg)
            self._running = False
            return

        ser = None
        try:
            ser = serial.Serial(self.port, baudrate=self.baud, bytesize=8,
                                parity="N", stopbits=1, timeout=1)
            print(f"[Parsivel2] Opened {self.port} @ {self.baud}")
            self._log_file = _open_log_file(self.log_dir, "parsivel2")
        except Exception as e:
            msg = f"Parsivel2 open failed: {e}"
            print(f"[Parsivel2] {msg}", flush=True)
            self.error_occurred.emit(msg)
            if ser is not None:
                ser.close()
            self._running = False
            return

        try:
            while self._running:
                try:
                    result = _read_parsivel2(ser)
                    if result:
                        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
                        t = result.pop("type")
                        record = {"timestamp": ts, "sensor": "parsivel2", "type": t, "data": result}
                        line = json.dumps(record, ensure_ascii=False)
                        self._log_file.write(line + "\n")
                        self._log_file.flush()
                        self.new_data.emit(record)
                        print(f"[Parsivel2] {ts} type={t}", flush=True)
                    else:
                        print(f"[Parsivel2] empty/unparseable response", flush=True)
                        self.error_occurred.emit("Parsivel2: empty or unparseable response")
                except Exception as e:
                    msg = f"Parsivel2: {e}"
                    print(f"[Parsivel2] {msg}", flush=True)
                    self.error_occurred.emit(msg)
                    _sleep_until_stopped(self, 2)
                    continue
                _sleep_until_stopped(self, 5)
        finally:
            if ser is not None:
                ser.close()
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            print("[Parsivel2] stopped", flush=True)

    def stop(self):
        self._running = False


class ModbusCollector(QThread):
    """Polls Modbus station every 10s, emits data dicts."""

    new_data = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, port=None, baud=19200, slave=1, log_dir=None, parent=None):
        super().__init__(parent)
        self.port = port
        self.baud = baud
        self.slave = slave
        self.log_dir = log_dir or _default_log_dir()
        self._running = False
        self._log_file = None

    def run(self):
        self._running = True
        if not self.port or not str(self.port).strip():
            msg = "Modbus port is not configured"
            print(f"[Modbus] {msg}", flush=True)
            self.error_occurred.emit(msg)
            self._running = False
            return

        inst = None
        try:
            inst = minimalmodbus.Instrument(self.port, self.slave)
            inst.serial.baudrate = self.baud
            inst.serial.bytesize = 8
            inst.serial.parity = minimalmodbus.serial.PARITY_NONE
            inst.serial.stopbits = 1
            inst.serial.timeout = 1
            inst.mode = minimalmodbus.MODE_RTU
            print(f"[Modbus] Opened {self.port} @ {self.baud}, slave={self.slave}")
            self._log_file = _open_log_file(self.log_dir, "modbus")
        except Exception as e:
            msg = f"Modbus open failed: {e}"
            print(f"[Modbus] {msg}", flush=True)
            self.error_occurred.emit(msg)
            if inst is not None:
                inst.serial.close()
            self._running = False
            return

        try:
            while self._running:
                try:
                    data = _read_modbus(inst)
                    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
                    record = {"timestamp": ts, "sensor": "modbus", "data": data}
                    line = json.dumps(record, ensure_ascii=False)
                    self._log_file.write(line + "\n")
                    self._log_file.flush()
                    self.new_data.emit(record)
                    temp = data.get("Airtemp_Avg", "?")
                    print(f"[Modbus] {ts} AirTemp={temp}°C", flush=True)
                except Exception as e:
                    msg = f"Modbus: {e}"
                    print(f"[Modbus] {msg}", flush=True)
                    self.error_occurred.emit(msg)
                    _sleep_until_stopped(self, 2)
                    continue
                _sleep_until_stopped(self, 10)
        finally:
            if inst is not None:
                inst.serial.close()
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
            print("[Modbus] stopped", flush=True)

    def stop(self):
        self._running = False
