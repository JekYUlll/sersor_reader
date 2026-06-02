#!/usr/bin/env bash
# RS485 Sensor Dashboard — Linux 打包脚本
# 输出: dist/RS485_Sensor_Dashboard (单文件可执行)
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/3] 安装依赖…"
.venv/bin/pip install -q pyinstaller

echo "[2/3] 打包中…"
.venv/bin/pyinstaller --onefile --windowed \
    --name "RS485_Sensor_Dashboard" \
    --add-data "gui:gui" \
    --add-data "port_config.py:." \
    --add-data "parsivel2_parser.py:." \
    --hidden-import minimalmodbus \
    --hidden-import serial \
    --hidden-import PySide6 \
    --hidden-import pyqtgraph \
    --collect-all PySide6 \
    gui/main.py

echo "[3/3] 完成!"
echo "可执行文件: dist/RS485_Sensor_Dashboard"
