"""Port Binding Dialog — lets the user select serial ports for both sensors.

Dark-themed QDialog matching the existing stylesheet.
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from port_config import scan_ports, default_config
from parsivel2_parser import parse_telegram

import minimalmodbus
import serial
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

BAUD_RATES = ["2400", "4800", "9600", "19200", "38400", "57600", "115200"]

DIALOG_STYLESHEET = """
    QDialog {
        background-color: #11111b;
        color: #cdd6f4;
    }
    QGroupBox {
        color: #cdd6f4;
        font-weight: bold;
        border: 1px solid #45475a;
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 18px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
    QLabel {
        color: #a6adc8;
    }
    QComboBox, QLineEdit {
        background-color: #1e1e2e;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 4px 8px;
        min-height: 24px;
    }
    QComboBox:hover, QLineEdit:hover {
        border: 1px solid #cba6f7;
    }
    QComboBox::drop-down {
        border: none;
        padding-right: 6px;
    }
    QComboBox QAbstractItemView {
        background-color: #1e1e2e;
        color: #cdd6f4;
        selection-background-color: #313244;
        border: 1px solid #45475a;
    }
    QPushButton {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 6px 16px;
        min-height: 24px;
    }
    QPushButton:hover {
        background-color: #45475a;
        border: 1px solid #cba6f7;
    }
    QPushButton:pressed {
        background-color: #585b70;
    }
    QDialogButtonBox QPushButton {
        min-width: 72px;
    }
"""


class ConnectionTestWorker(QThread):
    """Run a serial health check without blocking the dialog."""

    result_ready = Signal(bool, str)

    def __init__(
        self,
        sensor_type: str,
        port: str,
        baud: int,
        slave_address: int = 1,
        parent=None,
    ):
        super().__init__(parent)
        self.sensor_type = sensor_type
        self.port = port
        self.baud = baud
        self.slave_address = slave_address

    def run(self):
        try:
            if not self.port:
                raise ValueError("未选择串口")

            if self.sensor_type == "parsivel2":
                self._test_parsivel2()
            elif self.sensor_type == "modbus":
                self._test_modbus()
            else:
                raise ValueError(f"未知传感器类型: {self.sensor_type}")
        except Exception as exc:
            self.result_ready.emit(False, str(exc))
            return

        self.result_ready.emit(True, "")

    def _test_parsivel2(self):
        with serial.Serial(
            self.port,
            baudrate=self.baud,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=1,
        ) as ser:
            ser.reset_input_buffer()
            ser.write(b"CS/PA\r")
            time.sleep(0.6)
            raw = ser.read(ser.in_waiting or 4096)

        data = raw.decode("ascii", errors="replace").strip()
        if not data:
            raise RuntimeError("无响应")
        if parse_telegram(data) is None:
            raise RuntimeError("响应无法解析")

    def _test_modbus(self):
        inst = minimalmodbus.Instrument(self.port, self.slave_address)
        inst.serial.baudrate = self.baud
        inst.serial.bytesize = 8
        inst.serial.parity = minimalmodbus.serial.PARITY_NONE
        inst.serial.stopbits = 1
        inst.serial.timeout = 1
        inst.mode = minimalmodbus.MODE_RTU
        inst.read_register(0, number_of_decimals=0, functioncode=3)


class PortRow(QWidget):
    """A single row: label + port combo (with manual-entry fallback) + baud combo."""

    MANUAL_ENTRY_TEXT = "✏️ 手动输入…"

    def __init__(
        self,
        label: str,
        sensor_type: str,
        port: str = "",
        baud: int = 9600,
        slave_address_getter=None,
        parent=None,
    ):
        super().__init__(parent)
        self._sensor_type = sensor_type
        self._slave_address_getter = slave_address_getter
        self._manual_line = None  # shown when manual entry selected
        self._test_worker = None
        self._test_token = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Label
        label_w = QLabel(label)
        label_w.setMinimumWidth(120)
        layout.addWidget(label_w)

        # Port combo
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(240)
        self._port_combo.currentIndexChanged.connect(self._on_port_changed)
        layout.addWidget(self._port_combo, 1)

        # Manual entry line (hidden by default)
        self._manual_line = QLineEdit()
        self._manual_line.setPlaceholderText("/dev/ttyXXX")
        self._manual_line.setMinimumWidth(240)
        self._manual_line.hide()
        layout.addWidget(self._manual_line, 1)

        # Baud combo
        self._baud_combo = QComboBox()
        self._baud_combo.setMinimumWidth(96)
        for rate in BAUD_RATES:
            self._baud_combo.addItem(rate)
        layout.addWidget(self._baud_combo)

        # Test connection button
        self._test_button = QPushButton("测试")
        self._test_button.setMinimumWidth(78)
        self._test_button.clicked.connect(self.test_connection)
        layout.addWidget(self._test_button)

        # Populate port list
        self.refresh_ports()

        # Set initial values
        if port:
            idx = self._port_combo.findText(port)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                # Custom port — switch to manual entry
                self._port_combo.setCurrentIndex(
                    self._port_combo.count() - 1
                )  # last = manual
                self._manual_line.setText(port)

        baud_str = str(baud)
        bidx = self._baud_combo.findText(baud_str)
        if bidx >= 0:
            self._baud_combo.setCurrentIndex(bidx)

    def _on_port_changed(self, idx):
        """Toggle manual entry visibility."""
        is_manual = self._port_combo.currentText() == self.MANUAL_ENTRY_TEXT
        self._port_combo.setVisible(not is_manual)
        self._manual_line.setVisible(is_manual)
        if is_manual:
            self._manual_line.setFocus()

    def refresh_ports(self):
        """Re-populate the port combo with current available ports."""
        current_text = self._port_combo.currentText()
        if self._manual_line and self._manual_line.isVisible():
            current_text = self._manual_line.text()

        self._port_combo.blockSignals(True)
        self._port_combo.clear()
        ports = scan_ports()
        if ports:
            for dev, desc in ports:
                display = f"{dev} — {desc}" if desc and desc != dev else dev
                self._port_combo.addItem(dev, (dev, desc))
                # Also set the display text via setItemText — but we need data
                # Actually let's just use device as display text, description as tooltip
                idx = self._port_combo.count() - 1
                self._port_combo.setItemText(idx, dev)
                self._port_combo.setItemData(idx, desc, Qt.ToolTipRole)
        else:
            self._port_combo.addItem("(无可用串口)")

        self._port_combo.addItem(self.MANUAL_ENTRY_TEXT)
        self._port_combo.blockSignals(False)

        # Restore selection
        if current_text:
            idx = self._port_combo.findText(current_text)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)
            else:
                self._port_combo.setCurrentIndex(
                    self._port_combo.count() - 1
                )  # manual
                if self._manual_line:
                    self._manual_line.setText(current_text)

    def get_port(self) -> str:
        """Get the selected port path."""
        if self._manual_line and self._manual_line.isVisible():
            return self._manual_line.text().strip()
        text = self._port_combo.currentText()
        if text == self.MANUAL_ENTRY_TEXT or text == "(无可用串口)":
            return ""
        return text

    def get_baud(self) -> int:
        """Get the selected baud rate."""
        try:
            return int(self._baud_combo.currentText())
        except (ValueError, TypeError):
            return 9600

    def test_connection(self):
        """Probe the selected serial device in a worker thread."""
        if self._test_worker is not None and self._test_worker.isRunning():
            return

        self._test_token += 1
        slave_address = 1
        if self._slave_address_getter is not None:
            try:
                slave_address = int(self._slave_address_getter())
            except (TypeError, ValueError):
                self._show_test_result(False, "从站地址无效")
                return

        self._test_button.setText("测试中…")
        self._test_button.setEnabled(False)
        self._test_button.setStyleSheet("")

        worker = ConnectionTestWorker(
            sensor_type=self._sensor_type,
            port=self.get_port(),
            baud=self.get_baud(),
            slave_address=slave_address,
            parent=self,
        )
        worker.result_ready.connect(self._on_test_finished)
        worker.finished.connect(worker.deleteLater)
        self._test_worker = worker
        worker.start()

    def _on_test_finished(self, success: bool, message: str):
        self._test_worker = None
        self._show_test_result(success, message)

    def _show_test_result(self, success: bool, message: str = ""):
        self._test_button.setEnabled(True)
        if success:
            self._test_button.setText("✓ 成功")
            self._test_button.setStyleSheet("color: #a6e3a1;")
        else:
            self._test_button.setText("✗ 失败")
            self._test_button.setStyleSheet("color: #f38ba8;")
            if message:
                self._test_button.setToolTip(message)

        token = self._test_token
        QTimer.singleShot(3000, lambda: self._reset_test_button(token))

    def _reset_test_button(self, token: int):
        if token != self._test_token:
            return
        if self._test_worker is not None and self._test_worker.isRunning():
            return
        self._test_button.setText("测试")
        self._test_button.setEnabled(True)
        self._test_button.setStyleSheet("")
        self._test_button.setToolTip("")


class PortBindingDialog(QDialog):
    """Dialog to configure serial port bindings for both sensors."""

    def __init__(self, config: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("串口配置 — RS485 Sensor Dashboard")
        self.setMinimumWidth(580)
        self.setModal(True)
        self.setStyleSheet(DIALOG_STYLESHEET)

        cfg = config or default_config()

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ── Parsivel2 row ──
        p2_group = QGroupBox("雨滴谱仪 (Parsivel2)")
        p2_inner = QVBoxLayout(p2_group)
        self._parsivel2_row = PortRow(
            label="串口:",
            sensor_type="parsivel2",
            port=cfg.get("parsivel2_port", ""),
            baud=cfg.get("parsivel2_baud", 9600),
        )
        p2_inner.addWidget(self._parsivel2_row)
        main_layout.addWidget(p2_group)

        # ── Modbus row ──
        modbus_group = QGroupBox("气象站 (Modbus)")
        mb_inner = QVBoxLayout(modbus_group)

        self._modbus_row = PortRow(
            label="串口:",
            sensor_type="modbus",
            port=cfg.get("modbus_port", ""),
            baud=cfg.get("modbus_baud", 19200),
            slave_address_getter=lambda: self._slave_edit.text().strip(),
        )
        mb_inner.addWidget(self._modbus_row)

        # Slave address
        slave_layout = QHBoxLayout()
        slave_layout.setContentsMargins(0, 0, 0, 0)
        slave_label = QLabel("从站地址 (Slave):")
        slave_label.setMinimumWidth(120)
        self._slave_edit = QLineEdit(str(cfg.get("modbus_slave", 1)))
        self._slave_edit.setMaximumWidth(80)
        slave_layout.addWidget(slave_label)
        slave_layout.addWidget(self._slave_edit)
        slave_layout.addStretch(1)
        mb_inner.addLayout(slave_layout)

        main_layout.addWidget(modbus_group)

        # ── Rescan button ──
        btn_layout = QHBoxLayout()
        btn_rescan = QPushButton("🔄 重新扫描")
        btn_rescan.clicked.connect(self._on_rescan)
        btn_layout.addWidget(btn_rescan)
        btn_layout.addStretch(1)
        main_layout.addLayout(btn_layout)

        # ── Button box ──
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        main_layout.addWidget(self._button_box)

    def _on_rescan(self):
        """Re-scan serial ports and update both rows."""
        self._parsivel2_row.refresh_ports()
        self._modbus_row.refresh_ports()

    def _on_accept(self):
        """Validate and accept."""
        p2_port = self._parsivel2_row.get_port()
        mb_port = self._modbus_row.get_port()

        # Validate same port
        if p2_port and mb_port and p2_port == mb_port:
            QMessageBox.warning(
                self,
                "配置错误",
                "雨滴谱仪 (Parsivel2) 和 气象站 (Modbus) 不能使用同一个串口！\n"
                "请为两个设备选择不同的串口。",
            )
            return

        # Validate slave address
        try:
            slave = int(self._slave_edit.text().strip())
            if slave < 1 or slave > 247:
                raise ValueError
        except ValueError:
            QMessageBox.warning(
                self,
                "配置错误",
                "Modbus 从站地址必须在 1–247 范围内。",
            )
            return

        self.accept()

    def get_config(self) -> dict:
        """Return the configured port/bindings."""
        return {
            "parsivel2_port": self._parsivel2_row.get_port(),
            "parsivel2_baud": self._parsivel2_row.get_baud(),
            "modbus_port": self._modbus_row.get_port(),
            "modbus_baud": self._modbus_row.get_baud(),
            "modbus_slave": int(self._slave_edit.text().strip()),
        }
