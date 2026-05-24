"""Serial port detection and configuration helpers."""

import serial.tools.list_ports


def scan_ports() -> list[tuple[str, str]]:
    """Return list of (device_path, description) for available serial ports."""
    return [(p.device, p.description) for p in serial.tools.list_ports.comports()]


def default_config() -> dict:
    return {
        "parsivel2_port": "",
        "parsivel2_baud": 9600,
        "modbus_port": "",
        "modbus_baud": 19200,
        "modbus_slave": 1,
    }
