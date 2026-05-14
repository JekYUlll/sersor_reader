"""Status bar with connection indicators and error counts."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


STATUS_COLORS = {
    "ok":       "#a6e3a1",
    "degraded": "#f9e2af",
    "error":    "#f38ba8",
    "unknown":  "#6c7086",
}


class StatusWidget(QWidget):
    """Bottom status bar showing connection state for both sensors."""

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.setStyleSheet("background: #181825; border-top: 1px solid #313244;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)

        # Parsivel2 status
        self._p2_dot = QLabel("●")
        self._p2_label = QLabel("Parsivel2: 未连接")
        self._p2_label.setStyleSheet("color: #6c7086; font-size: 12px;")

        # Modbus status
        self._mb_dot = QLabel("●")
        self._mb_label = QLabel("Modbus: 未连接")
        self._mb_label.setStyleSheet("color: #6c7086; font-size: 12px;")

        # Last update time
        self._time_label = QLabel("—")
        self._time_label.setStyleSheet("color: #6c7086; font-size: 12px;")

        # Error counts
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #6c7086; font-size: 12px;")

        layout.addWidget(self._p2_dot)
        layout.addWidget(self._p2_label)
        layout.addSpacing(20)
        layout.addWidget(self._mb_dot)
        layout.addWidget(self._mb_label)
        layout.addStretch()
        layout.addWidget(self._error_label)
        layout.addSpacing(20)
        layout.addWidget(self._time_label)

        # Connect model signals
        model.status_changed.connect(self._on_status)
        model.parsivel2_type1.connect(self._on_p2_data)
        model.modbus_updated.connect(self._on_mb_data)

    def _set_dot(self, dot_label: QLabel, text_label: QLabel, name: str, status: str):
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
        dot_label.setText("●")
        dot_label.setStyleSheet(f"color: {color}; font-size: 14px;")
        status_cn = {"ok": "正常", "degraded": "降级", "error": "故障"}.get(status, status)
        text_label.setText(f"{name}: {status_cn}")
        text_label.setStyleSheet(f"color: #cdd6f4; font-size: 12px;")

    def _on_status(self, sensor_id: str, status: str):
        if sensor_id == "parsivel2":
            self._set_dot(self._p2_dot, self._p2_label, "Parsivel2", status)
        elif sensor_id == "modbus":
            self._set_dot(self._mb_dot, self._mb_label, "Modbus", status)
        self._update_errors()

    def _update_errors(self):
        p2_errs = self.model.parsivel2_error_count
        mb_errs = self.model.modbus_error_count
        parts = []
        if p2_errs:
            parts.append(f"Parsivel2 错误: {p2_errs}")
        if mb_errs:
            parts.append(f"Modbus 错误: {mb_errs}")
        self._error_label.setText(" | ".join(parts))

    def _on_p2_data(self, data: dict):
        from datetime import datetime
        self._time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._update_errors()

    def _on_mb_data(self, data: dict):
        from datetime import datetime
        self._time_label.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self._update_errors()
