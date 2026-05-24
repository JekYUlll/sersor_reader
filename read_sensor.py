import argparse
import json
import serial
import serial.tools.list_ports
import time

from parsivel2_parser import parse_telegram

BAUDRATE = 9600

def list_ports():
    for p in serial.tools.list_ports.comports():
        print(f"{p.device} — {p.description}")

def query(ser, cmd: str) -> str:
    try:
        ser.reset_input_buffer()
        ser.write((cmd + "\r").encode("ascii"))
        time.sleep(0.6)
        raw = ser.read(ser.in_waiting or 4096)
        return raw.decode("ascii", errors="replace").strip()
    except Exception as e:
        return f"ERR: {e}"

def poll_loop(ser, interval: int = 5):
    log_path = time.strftime("parsivel2_%Y%m%d_%H%M%S.jsonl")
    with open(log_path, "w") as f:
        print(f"Polling every {interval}s — logging to {log_path} — Ctrl+C to stop\n")
        while True:
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            raw = query(ser, "CS/PA")
            result = parse_telegram(raw)

            if result is None:
                record = {"timestamp": timestamp, "sensor": "parsivel2", "error": "parse_failed", "raw": raw[:200]}
            else:
                record = {"timestamp": timestamp, "sensor": "parsivel2", "type": result.pop("type"), "data": result}

            line = json.dumps(record, ensure_ascii=False)
            f.write(line + "\n")
            f.flush()
            print(line)
            time.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read Parsivel2 data over serial.")
    parser.add_argument("--port", help="Serial port, for example COM3 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=BAUDRATE, help=f"Baud rate (default: {BAUDRATE})")
    args = parser.parse_args()

    if not args.port:
        print("Available serial ports:")
        list_ports()
        parser.error("please specify a serial port with --port")

    with serial.Serial(args.port, baudrate=args.baud, bytesize=8,
                       parity="N", stopbits=1, timeout=1) as ser:
        print(f"Opened {args.port} @ {args.baud} baud\n")
        poll_loop(ser, interval=5)
