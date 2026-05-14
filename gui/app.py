"""Main application: assembles Model, Collectors, and MainWindow."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QMainWindow, QSplitter,
                                QVBoxLayout, QWidget)

from gui.collectors import Parsivel2Collector, ModbusCollector
from gui.model import DataModel
from gui.widgets.dashboard import DashboardPanel
from gui.widgets.statusbar import StatusWidget
from gui.widgets.timeseries import TimeseriesPanel

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

    def __init__(self, model: DataModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.setWindowTitle("RS485 Sensor Dashboard")
        self.resize(1280, 900)

        # Collectors (started separately)
        self._parsivel2_collector = Parsivel2Collector()
        self._modbus_collector = ModbusCollector()

        self._setup_ui()
        self._connect_signals()

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

    def _connect_signals(self):
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
        self._parsivel2_collector.stop()
        self._parsivel2_collector.wait(2000)
        self._modbus_collector.stop()
        self._modbus_collector.wait(2000)

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
        self._window = MainWindow(self._model)

    def run(self) -> int:
        self._window.show()
        # Auto-start collection
        self._window.start_collection()
        return self._app.exec()
