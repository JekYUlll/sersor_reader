"""Dashboard panel showing latest sensor values in a card grid."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QLabel,
                                QVBoxLayout, QWidget)


STYLE_CARD = """
    QFrame#card {
        background: #1e1e2e;
        border: 1px solid #313244;
        border-radius: 6px;
        padding: 8px;
    }
"""

STYLE_LABEL = "color: #a6adc8; font-size: 11px;"
STYLE_VALUE = "color: #cdd6f4; font-size: 18px; font-weight: bold;"
STYLE_UNIT  = "color: #6c7086; font-size: 11px;"


class ValueCard(QFrame):
    """Single metric card: label, value, unit."""

    def __init__(self, title: str, unit: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setStyleSheet(STYLE_CARD)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self._label = QLabel(title)
        self._label.setStyleSheet(STYLE_LABEL)
        layout.addWidget(self._label)

        self._value = QLabel("—")
        self._value.setStyleSheet(STYLE_VALUE)
        layout.addWidget(self._value)

        self._unit_label = QLabel(unit)
        self._unit_label.setStyleSheet(STYLE_UNIT)
        layout.addWidget(self._unit_label)

    def set_value(self, text: str, color: str = None):
        self._value.setText(text)
        if color:
            self._value.setStyleSheet(f"{STYLE_VALUE} color: {color};")


class DashboardPanel(QWidget):
    """Grid of ValueCards for both sensors."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

        self._parsivel2_cards = {}
        self._modbus_cards = {}

        self._setup_ui()

        # Connect signals
        model.parsivel2_type1.connect(self._on_parsivel2_type1)
        model.modbus_updated.connect(self._on_modbus)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # ── Parsivel2 Group ──
        p2_group = QGroupBox("Parsivel2 — 激光雨滴谱仪")
        p2_group.setStyleSheet("QGroupBox { color: #cdd6f4; font-weight: bold; border: 1px solid #45475a; border-radius: 6px; margin-top: 8px; padding-top: 16px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }")
        p2_grid = QGridLayout(p2_group)
        p2_grid.setSpacing(6)

        p2_fields = [
            ("温度",        "°C",    "sensor_temp"),
            ("供电电压",    "V",     "supply_voltage"),
            ("雨强",        "mm/h",  "rain_intensity"),
            ("天气",        "",      "weather_metar"),
            ("激光振幅",    "",      "laser_band_amplitude"),
            ("粒子数",      "",      "particle_count"),
            ("雷达反射率",  "dBz",   "radar_reflectivity"),
            ("能见度",      "m",     "mor_visibility"),
            ("雪强",        "mm/h",  "snow_intensity_0"),
            ("传感器状态",  "",      "sensor_status"),
            ("错误码",      "",      "error_code"),
            ("加热",        "",      "heating_state"),
        ]
        for i, (label, unit, key) in enumerate(p2_fields):
            card = ValueCard(label, unit)
            p2_grid.addWidget(card, i // 3, i % 3)
            self._parsivel2_cards[key] = card

        main_layout.addWidget(p2_group)

        # ── Modbus Group ──
        mb_group = QGroupBox("Modbus — 气象站")
        mb_group.setStyleSheet("QGroupBox { color: #cdd6f4; font-weight: bold; border: 1px solid #45475a; border-radius: 6px; margin-top: 8px; padding-top: 16px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }")
        mb_grid = QGridLayout(mb_group)
        mb_grid.setSpacing(6)

        mb_fields = [
            ("气温",        "°C",     "Airtemp_Avg"),
            ("湿度",        "%",      "RH_Avg"),
            ("气压",        "hPa",    "BP_Avg"),
            ("露点",        "°C",     "Dew_temp_Avg"),
            ("风速",        "m/s",    "WS_Avg"),
            ("风向",        "°",      "WD"),
            ("太阳辐射",    "W/m²",   "LPS_GHI_Avg"),
            ("电池电压",    "V",      "Batt_volt_Min"),
            ("面板温度",    "°C",     "PTemp"),
            ("阵风",        "km/h",   "wind_max"),
        ]
        for i, (label, unit, key) in enumerate(mb_fields):
            card = ValueCard(label, unit)
            mb_grid.addWidget(card, i // 5, i % 5)
            self._modbus_cards[key] = card

        main_layout.addWidget(mb_group)

    # ── Slots ──────────────────────────────────────────────────────

    def _on_parsivel2_type1(self, data: dict):
        """Update Parsivel2 cards from Type1 data."""
        updates = {
            "sensor_temp":       self._fmt(data.get("sensor_temp"), ".1f"),
            "supply_voltage":    self._fmt(data.get("supply_voltage"), ".1f"),
            "rain_intensity":    self._fmt(data.get("rain_intensity"), ".3f"),
            "weather_metar":     str(data.get("weather_metar", "?")).strip(),
            "laser_band_amplitude": str(data.get("laser_band_amplitude", "?")),
            "particle_count":    str(data.get("particle_count", "?")),
            "radar_reflectivity": self._fmt(data.get("radar_reflectivity"), ".1f"),
            "mor_visibility":    str(data.get("mor_visibility", "?")),
            "sensor_status":     str(data.get("sensor_status", "?")),
            "error_code":        str(data.get("error_code", "?")),
            "heating_state":     self._heat_label(data.get("heating_state")),
        }
        # Snow intensity (first element)
        snow = data.get("snow_intensity")
        if isinstance(snow, list) and len(snow) > 0:
            updates["snow_intensity_0"] = self._fmt(snow[0], ".3f")

        for key, text in updates.items():
            if key in self._parsivel2_cards:
                self._parsivel2_cards[key].set_value(text)

    def _on_modbus(self, data: dict):
        """Update Modbus cards."""
        updates = {
            "Airtemp_Avg":  self._fmt(data.get("Airtemp_Avg"), ".1f"),
            "RH_Avg":       self._fmt(data.get("RH_Avg"), ".1f"),
            "BP_Avg":       self._fmt(data.get("BP_Avg"), ".1f"),
            "Dew_temp_Avg": self._fmt(data.get("Dew_temp_Avg"), ".1f"),
            "WS_Avg":       self._fmt(data.get("WS_Avg"), ".2f"),
            "WD":           self._fmt(data.get("WD"), ".0f"),
            "LPS_GHI_Avg":  self._fmt(data.get("LPS_GHI_Avg"), ".2f"),
            "Batt_volt_Min": self._fmt(data.get("Batt_volt_Min"), ".2f"),
            "PTemp":        self._fmt(data.get("PTemp"), ".1f"),
            "wind_max":     self._fmt(data.get("wind_max"), ".1f"),
        }
        for key, text in updates.items():
            if key in self._modbus_cards:
                self._modbus_cards[key].set_value(text)

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _fmt(val, fmt_spec: str) -> str:
        if val is None:
            return "N/A"
        if isinstance(val, str):
            return val
        try:
            return f"{val:{fmt_spec}}"
        except (ValueError, TypeError):
            return str(val)

    @staticmethod
    def _heat_label(val) -> str:
        if val is None:
            return "?"
        try:
            v = int(val)
            return ["关", "低", "高"][v] if 0 <= v <= 2 else str(v)
        except (ValueError, TypeError):
            return str(val)
