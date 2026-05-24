"""Main application: assembles Model, Collectors, and MainWindow."""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter,
                                QVBoxLayout, QWidget)

from gui.collectors import Parsivel2Collector, ModbusCollector
from gui.model import DataModel
from gui.widgets.dashboard import DashboardPanel
from gui.widgets.port_binding_dialog import PortBindingDialog
from gui.widgets.statusbar import StatusWidget
from gui.widgets.timeseries import TimeseriesPanel
from port_config import default_config

DARK_STYLESHEET = """
    QMainWindow, QWidget {
        background-color: #11111b;
        color: #cdd6f4;
    }
    QSplitter::handle {
        background-color: #313244;
        width: 2px;
    }
    QTabWidget::pane {
        border: 1px solid #45475a;
        background: #1e1e2e;
    }
    QTabBar::tab {
        background: #181825;
        color: #6c7086;
        padding: 6px 16px;
        border: 1px solid #313244;
        border-bottom: none;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }
    QTabBar::tab:selected {
        background: #1e1e2e;
        color: #cdd6f4;
        border-bottom: 2px solid #cba6f7;
    }
"""


class MainWindow(QMainWindow):
    """Top-level window with Dashboard, Timeseries, and StatusBar."""

    def __init__(self, model: DataModel, port_config: dict | None = None, log_dir=None, parent=None):
        super().__init__(parent)
        self.model = model
        self._port_config = port_config or default_config()
        self._log_dir = log_dir
        self.setWindowTitle("RS485 Sensor Dashboard")
        self.resize(1280, 900)

        self._parsivel2_collector = None
        self._modbus_collector = None

        self._setup_ui()
        self._create_collectors()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)

        # Splitter: top = dashboard, bottom = timeseries
        splitter = QSplitter(Qt.Vertical)

        self._dashboard = DashboardPanel(self.model)
        splitter.addWidget(self._dashboard)

        self._timeseries = TimeseriesPanel(self.model)
        splitter.addWidget(self._timeseries)

        splitter.setStretchFactor(0, 1)  # dashboard: smaller
        splitter.setStretchFactor(1, 2)  # timeseries: larger
        layout.addWidget(splitter, 1)

        # Status bar
        self._status = StatusWidget(self.model)
        layout.addWidget(self._status)

        # ── Menu ──
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件")
        a_exit = QAction("退出", self)
        a_exit.triggered.connect(self.close)
        file_menu.addAction(a_exit)

        control_menu = menubar.addMenu("采集")
        a_start = QAction("启动采集", self)
        a_start.triggered.connect(self.start_collection)
        control_menu.addAction(a_start)

        a_stop = QAction("停止采集", self)
        a_stop.triggered.connect(self.stop_collection)
        control_menu.addAction(a_stop)

        a_port_settings = QAction("串口设置", self)
        a_port_settings.triggered.connect(self.configure_ports)
        control_menu.addAction(a_port_settings)

    def _create_collectors(self):
        """Create collectors from the current port configuration."""
        self._parsivel2_collector = Parsivel2Collector(
            port=self._port_config.get("parsivel2_port", ""),
            baud=self._port_config.get("parsivel2_baud", 9600),
            log_dir=self._log_dir,
        )
        self._modbus_collector = ModbusCollector(
            port=self._port_config.get("modbus_port", ""),
            baud=self._port_config.get("modbus_baud", 19200),
            slave=self._port_config.get("modbus_slave", 1),
            log_dir=self._log_dir,
        )
        self._connect_collector_signals()

    def _connect_collector_signals(self):
        """Wire collectors → model."""
        self._parsivel2_collector.new_data.connect(self._on_parsivel2_data)
        self._parsivel2_collector.error_occurred.connect(self._on_parsivel2_error)
        self._modbus_collector.new_data.connect(self._on_modbus_data)
        self._modbus_collector.error_occurred.connect(self._on_modbus_error)

    # ── Slots ──────────────────────────────────────────────────────

    def _on_parsivel2_data(self, record: dict):
        self.model.push_parsivel2(record)

    def _on_parsivel2_error(self, msg: str):
        self.model.push_parsivel2_error(msg)

    def _on_modbus_data(self, record: dict):
        self.model.push_modbus(record)

    def _on_modbus_error(self, msg: str):
        self.model.push_modbus_error(msg)

    # ── Control ────────────────────────────────────────────────────

    def start_collection(self):
        if not self._parsivel2_collector.isRunning():
            self._parsivel2_collector.start()
        if not self._modbus_collector.isRunning():
            self._modbus_collector.start()

    def stop_collection(self):
        if self._parsivel2_collector is not None:
            self._parsivel2_collector.stop()
            self._parsivel2_collector.wait(2000)
        if self._modbus_collector is not None:
            self._modbus_collector.stop()
            self._modbus_collector.wait(2000)

    def configure_ports(self):
        dialog = PortBindingDialog(self._port_config, self)
        if dialog.exec() != PortBindingDialog.Accepted:
            return

        was_running = (
            self._parsivel2_collector.isRunning()
            or self._modbus_collector.isRunning()
        )
        self.stop_collection()
        self._port_config = dialog.get_config()
        self._create_collectors()
        if was_running:
            self.start_collection()

    def closeEvent(self, event):
        self.stop_collection()
        event.accept()


class App:
    """Application wrapper."""

    def __init__(self, argv):
        self._app = QApplication(argv)
        self._app.setApplicationName("RS485 Sensor Dashboard")
        self._app.setStyleSheet(DARK_STYLESHEET)

        self._model = DataModel()
        port_config = default_config()
        dialog = PortBindingDialog(port_config)
        if dialog.exec() != PortBindingDialog.Accepted:
            print("串口配置取消，程序退出")
            sys.exit(0)
        port_config = dialog.get_config()
        self._window = MainWindow(self._model, port_config=port_config)

    def run(self) -> int:
        self._window.show()
        # Auto-start collection
        self._window.start_collection()
        return self._app.exec()
