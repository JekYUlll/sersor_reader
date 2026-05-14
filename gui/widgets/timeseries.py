"""Timeseries panel with pyqtgraph plots in tabs."""

import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

RING_SIZE = 300

# Dark theme colors
COLORS = {
    "parsivel2_temp":  "#f38ba8",  # pink
    "modbus_airtemp":  "#a6e3a1",  # green
    "dew_temp":        "#89b4fa",  # blue
    "rain":            "#74c7ec",  # cyan
    "ghi":             "#f9e2af",  # yellow
    "wind":            "#cba6f7",  # purple
    "wd":              "#fab387",  # orange
    "pressure":        "#94e2d5",  # teal
    "humidity":        "#b4befe",  # lavender
}


def _mk_pen(color: str, width: float = 1.5) -> pg.mkPen:
    return pg.mkPen(color=color, width=width)


def _extract_ts(data_list: list, field: str, default=0.0):
    """Extract a field from list of (timestamp, data_dict) tuples."""
    xs, ys = [], []
    for i, (ts, d) in enumerate(data_list):
        v = d.get(field)
        if v is None:
            continue
        try:
            ys.append(float(v))
            xs.append(i)  # index-based x axis (sample count)
        except (ValueError, TypeError):
            continue
    return np.array(xs), np.array(ys)


class TimeseriesPanel(QWidget):
    """Tabbed pyqtgraph plots for historical data."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Create tabs
        self._temp_plot = self._create_plot("Temperature (°C)")
        self._tabs.addTab(self._temp_plot, "温度")

        self._precip_plot = self._create_plot("Precipitation / Radiation")
        self._tabs.addTab(self._precip_plot, "降水 & 辐射")

        self._wind_plot = self._create_plot("Wind")
        self._tabs.addTab(self._wind_plot, "风")

        self._modbus_plot = self._create_plot("Modbus Overview")
        self._tabs.addTab(self._modbus_plot, "气象综合")

        # Store curves
        self._curves = {}

        # Refresh timer — poll the ring buffers
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)  # refresh every 2s

    def _create_plot(self, title: str) -> pg.PlotWidget:
        pw = pg.PlotWidget()
        pw.setBackground("#1e1e2e")
        pw.setLabel("left", title)
        pw.setLabel("bottom", "samples")
        pw.showGrid(x=True, y=True, alpha=0.3)
        pw.addLegend(offset=(-10, 10))
        return pw

    def _get_or_create_curve(self, plot: pg.PlotWidget, name: str, color: str) -> pg.PlotDataItem:
        key = (plot, name)
        if key not in self._curves:
            curve = plot.plot(pen=_mk_pen(color), name=name)
            self._curves[key] = curve
        return self._curves[key]

    def _refresh(self):
        """Pull latest ring-buffer data and update curves."""
        # ── Temperature tab ──
        p2_data = self.model.ts_parsivel2
        mb_data = self.model.ts_modbus

        if p2_data:
            x, y = _extract_ts(p2_data, "sensor_temp")
            if len(x):
                self._get_or_create_curve(self._temp_plot, "Parsivel2 T", COLORS["parsivel2_temp"]).setData(x, y)

        if mb_data:
            x, y = _extract_ts(mb_data, "Airtemp_Avg")
            if len(x):
                self._get_or_create_curve(self._temp_plot, "Air Temp", COLORS["modbus_airtemp"]).setData(x, y)
            x, y = _extract_ts(mb_data, "Dew_temp_Avg")
            if len(x):
                self._get_or_create_curve(self._temp_plot, "Dew Temp", COLORS["dew_temp"]).setData(x, y)

        # ── Precip / Radiation tab ──
        if p2_data:
            x, y = _extract_ts(p2_data, "rain_intensity")
            if len(x):
                self._get_or_create_curve(self._precip_plot, "Rain (mm/h)", COLORS["rain"]).setData(x, y)

        if mb_data:
            x, y = _extract_ts(mb_data, "LPS_GHI_Avg")
            if len(x):
                self._get_or_create_curve(self._precip_plot, "GHI (W/m²)", COLORS["ghi"]).setData(x, y)

        # ── Wind tab ──
        if mb_data:
            x, y = _extract_ts(mb_data, "WS_Avg")
            if len(x):
                self._get_or_create_curve(self._wind_plot, "Wind Speed (m/s)", COLORS["wind"]).setData(x, y)
            x2, y2 = _extract_ts(mb_data, "WD")
            if len(x2):
                self._get_or_create_curve(self._wind_plot, "Wind Dir (°)", COLORS["wd"]).setData(x2, y2)

        # ── Modbus overview tab ──
        if mb_data:
            x, y = _extract_ts(mb_data, "BP_Avg")
            if len(x):
                self._get_or_create_curve(self._modbus_plot, "Pressure (hPa)", COLORS["pressure"]).setData(x, y)
            x, y = _extract_ts(mb_data, "RH_Avg")
            if len(x):
                self._get_or_create_curve(self._modbus_plot, "Humidity (%)", COLORS["humidity"]).setData(x, y)
