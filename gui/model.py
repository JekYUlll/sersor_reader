"""DataModel: aggregates sensor data with Qt signals for UI notification."""

from collections import deque

from PySide6.QtCore import QObject, Signal


RING_SIZE = 300  # ~50 min of Parsivel2 Type1, ~50 min of Modbus data


class DataModel(QObject):
    """Singleton aggregator for both sensor data streams.

    Emits ``data_updated`` when any sensor produces new data.
    Widgets connect to this signal for thread-safe UI updates.
    """

    parsivel2_type1 = Signal(dict)  # latest parsed Type1 record
    parsivel2_type2 = Signal(dict)  # latest parsed Type2 record
    modbus_updated = Signal(dict)   # latest Modbus record
    status_changed = Signal(str, str)  # (sensor_id, status) e.g. "parsivel2", "error"

    def __init__(self, parent=None):
        super().__init__(parent)

        # Latest values
        self._latest_p1 = None
        self._latest_p2 = None
        self._latest_modbus = None

        # Ring buffers for time-series (timestamp, field_dict)
        self._ts_p1 = deque(maxlen=RING_SIZE)    # Parsivel2 Type1
        self._ts_modbus = deque(maxlen=RING_SIZE)

        # Status tracking
        self._parsivel2_errors = 0
        self._modbus_errors = 0
        self._parsivel2_connected = False
        self._modbus_connected = False

    # ── Parsivel2 ──────────────────────────────────────────────────

    def push_parsivel2(self, record: dict):
        """Record: {'type': 'type1'|'type2', 'data': {...}, ...}."""
        t = record.get("type")
        if t == "type1":
            self._latest_p1 = record["data"]
            self._ts_p1.append((record["timestamp"], record["data"]))
            self.parsivel2_type1.emit(record["data"])
        elif t == "type2":
            self._latest_p2 = record["data"]
            self.parsivel2_type2.emit(record["data"])
        self._parsivel2_connected = True
        self._parsivel2_errors = 0
        self.status_changed.emit("parsivel2", "ok")

    def push_parsivel2_error(self, raw_msg: str = ""):
        self._parsivel2_errors += 1
        status = "error" if self._parsivel2_errors > 3 else "degraded"
        self.status_changed.emit("parsivel2", status)

    # ── Modbus ─────────────────────────────────────────────────────

    def push_modbus(self, record: dict):
        """Record: {'timestamp': ..., 'data': {...}}."""
        self._latest_modbus = record["data"]
        self._ts_modbus.append((record["timestamp"], record["data"]))
        self.modbus_updated.emit(record["data"])
        self._modbus_connected = True
        self._modbus_errors = 0
        self.status_changed.emit("modbus", "ok")

    def push_modbus_error(self, raw_msg: str = ""):
        self._modbus_errors += 1
        status = "error" if self._modbus_errors > 3 else "degraded"
        self.status_changed.emit("modbus", status)

    # ── Accessors ──────────────────────────────────────────────────

    @property
    def latest_parsivel2_type1(self) -> dict | None:
        return self._latest_p1

    @property
    def latest_parsivel2_type2(self) -> dict | None:
        return self._latest_p2

    @property
    def latest_modbus(self) -> dict | None:
        return self._latest_modbus

    @property
    def ts_parsivel2(self) -> list:
        return list(self._ts_p1)

    @property
    def ts_modbus(self) -> list:
        return list(self._ts_modbus)

    @property
    def parsivel2_connected(self) -> bool:
        return self._parsivel2_connected

    @property
    def modbus_connected(self) -> bool:
        return self._modbus_connected

    @property
    def parsivel2_error_count(self) -> int:
        return self._parsivel2_errors

    @property
    def modbus_error_count(self) -> int:
        return self._modbus_errors
