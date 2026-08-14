# SEU 南极传感器系统读取和展示

本项目读取两路 RS485 设备：Modbus 气象站和 Parsivel2。

## 工控机长期采集

生产 C 程序位于 [`c_reader/`](c_reader/README.md)，用于 TL3568/RK3568 Ubuntu 20.04 工控机。它保存完整原始帧、通信状态和少量健康预览，支持断电恢复、按时轮转、gzip、SHA-256、systemd watchdog 和串口自动重连。

```sh
cd c_reader
make test
make
```

默认稳定映射：

- Modbus：`usb-1a86_USB_Serial-if00-port0`，19200 8N1。
- Parsivel2：`usb-1a86_USB2.0-Serial-if00-port0`，9600 8N1。

现有 Python GUI 和分析脚本继续保留；现场无人值守采集优先使用 C 程序。
