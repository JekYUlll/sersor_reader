import argparse
import json
import minimalmodbus
import serial.tools.list_ports
import time

BAUDRATE = 19200
SLAVE_ADDRESS = 1

# (field_name, register_address_0based, unit)
FIELDS = [
    ("Batt_volt_Min",  0,  "V"),
    ("PTemp",          2,  "°C"),
    ("WD",             4,  "°"),
    ("WS_Avg",         6,  "m/s"),
    ("Airtemp_Avg",    8,  "°C"),
    ("RH_Avg",         10, "%"),
    ("BP_Avg",         12, "hPa"),
    ("Dew_temp_Avg",   14, "°C"),
    ("LPS_GHI_Avg",    16, "W/m²"),
    ("LPS_GHI_Max",    18, "W/m²"),
    ("Flux_min",       20, "g/m²/s"),
    ("Flux_avg",       22, "g/m²/s"),
    ("Flux_max",       24, "g/m²/s"),
    ("Flux_std",       26, "g/m²/s"),
    ("Flux_cum",       28, "g/m²/s"),
    ("wind_min",       30, "km/h"),
    ("wind_avg",       32, "km/h"),
    ("wind_max",       34, "km/h"),
    ("TargetmV_Avg",   36, "mV"),
    ("DetectorTC_Avg", 38, "°C"),
    ("TargetTC_Avg",   40, "°C"),
]

def list_ports():
    for p in serial.tools.list_ports.comports():
        print(f"{p.device} — {p.description}")

def make_instrument(port: str, baud: int = BAUDRATE, slave: int = SLAVE_ADDRESS) -> minimalmodbus.Instrument:
    inst = minimalmodbus.Instrument(port, slave)
    inst.serial.baudrate = baud
    inst.serial.bytesize = 8
    inst.serial.parity = minimalmodbus.serial.PARITY_NONE
    inst.serial.stopbits = 1
    inst.serial.timeout = 1
    inst.mode = minimalmodbus.MODE_RTU
    return inst

def read_once(inst: minimalmodbus.Instrument) -> dict:
    result = {}
    for name, reg, unit in FIELDS:
        try:
            val = inst.read_float(reg, functioncode=3,
                                  byteorder=minimalmodbus.BYTEORDER_LITTLE_SWAP)
            result[name] = round(val, 4)
        except Exception as e:
            result[name] = f"ERR: {e}"
    return result

def poll_loop(inst: minimalmodbus.Instrument, interval: int = 10):
    log_path = time.strftime("modbus_%Y%m%d_%H%M%S.jsonl")
    with open(log_path, "w") as f:
        print(f"Polling every {interval}s — logging to {log_path} — Ctrl+C to stop\n")
        while True:
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            data = read_once(inst)
            record = {"timestamp": timestamp, "sensor": "modbus", "data": data}
            line = json.dumps(record, ensure_ascii=False)
            f.write(line + "\n")
            f.flush()
            print(line)
            time.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Read Modbus station data over serial.")
    parser.add_argument("--port", help="Serial port, for example COM4 or /dev/ttyUSB1")
    parser.add_argument("--baud", type=int, default=BAUDRATE, help=f"Baud rate (default: {BAUDRATE})")
    parser.add_argument("--slave", type=int, default=SLAVE_ADDRESS, help=f"Slave address (default: {SLAVE_ADDRESS})")
    args = parser.parse_args()

    if not args.port:
        print("Available serial ports:")
        list_ports()
        parser.error("please specify a serial port with --port")

    inst = make_instrument(args.port, baud=args.baud, slave=args.slave)
    print(f"Opened {args.port} @ {args.baud} baud, slave={args.slave}\n")
    poll_loop(inst, interval=10)
