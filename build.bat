@echo off
REM =============================================
REM  RS485 Sensor Dashboard — Windows 打包脚本
REM  使用方法: 双击运行 或 在终端执行 build.bat
REM  输出: dist/RS485_Sensor_Dashboard.exe
REM =============================================

cd /d "%~dp0"

echo [1/4] 检查 Python 环境…
python --version >nul 2>&1 || (
    echo 错误: 未找到 Python，请先安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

echo [2/4] 安装依赖…
pip install -e .
pip install pyinstaller

echo [3/4] 打包中…
pyinstaller --onefile --windowed ^
    --name "RS485_Sensor_Dashboard" ^
    --icon NONE ^
    --add-data "gui;gui" ^
    --add-data "port_config.py;." ^
    --add-data "parsivel2_parser.py;." ^
    --hidden-import minimalmodbus ^
    --hidden-import serial ^
    --hidden-import PySide6 ^
    --hidden-import pyqtgraph ^
    --collect-all PySide6 ^
    gui/main.py

echo [4/4] 完成!
echo 可执行文件: dist\RS485_Sensor_Dashboard.exe
echo.
echo 注意: 首次运行可能被杀毒软件拦截，请添加信任。
echo.

pause
