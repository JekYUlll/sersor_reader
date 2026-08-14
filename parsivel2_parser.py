"""Parsivel2 CS/PA telegram parser based on BA_Parsivel2_EN_70210002BE.pdf"""

from __future__ import annotations

import re

# ── Field definitions: (field_number, name, scale_divisor, unit) ──
# Scale divisor: raw_value / divisor = real value
# None = keep as string, 1 = integer as-is

SENTINEL_RAW = "-9.999"

TELEGRAM_TYPE1_FIELDS = [
    (0,  "telegram_id",       None,  ""),       # e.g. "TYP OP4A"
    (1,  "rain_intensity",    1000,  "mm/h"),    # 01:0000.000
    (2,  "rain_amount",       100,   "mm"),      # 02:0000.00
    (3,  "weather_syn_present", 1,   "code"),    # 03:00 (int)
    (4,  "weather_syn_past",  1,     "code"),    # 04:00 (int)
    (5,  "weather_metar",     None,  ""),        # 05:   NP (2-char string)
    (6,  "weather_nws",       None,  ""),        # 06:   C  (1-char string)
    (7,  "radar_reflectivity", 1000, "dBz"),     # 07:-9.999
    (8,  "mor_visibility",    1,     "m"),       # 08:20000
    (9,  "laser_band_amplitude", 1,  ""),        # 09:00027
    (10, "sensor_temp",       1000,  "°C"),      # 10:25439 -> 25.439°C
    (11, "particle_count",    1,     ""),        # 11:00000
    (12, "heating_current",   1,     ""),        # 12:025
    (13, "serial_number",     1,     ""),        # 13:452920
    (14, "firmware_version",  None,  ""),        # 14:2.11.11
    (15, "dsp_version",       None,  ""),        # 15:2.11.1
    (16, "sensor_head_fw",    None,  ""),        # 16:0.49
    (17, "supply_voltage",    1,     "V"),       # 17:12.0 -> 12.0V (already has decimal)
    (18, "heating_state",     1,     ""),        # 18:0 (0=off, 1=low, 2=high)
    (19, "time_str",          None,  ""),        # 19:11:35:00
    (20, "date_str",          None,  ""),        # 20:00:42:48 (dd:mm:yy)
    (21, "calib_date",        None,  ""),        # 21:15.05.2032
    (22, "sensor_head_calib_date", None, ""),    # 22:
    (23, "sensor_head_sn",    None,  ""),        # 23:
    (24, "rain_intensity_hires", 100, "mm/h"),   # 24:0000.00
    (25, "sensor_status",     1,     ""),        # 25:000
    (26, "laser_status",      1,     ""),        # 26:036
    (27, "optics_status",     1,     ""),        # 27:024
    (28, "temp_status",       1,     ""),        # 28:024
    (29, "precip_intensity",  1000,  "mm/h"),    # 29:000.073
    (30, "precip_amount",     100,   "mm"),      # 30:00.000
    (31, "precip_type",       None,  ""),        # 31:0000.0
    (32, "precip_type_metar", None,  ""),        # 32:0000.00
    (34, "precip_intensity_2", 100,  "mm/h"),    # 34:0000.00
    (35, "precip_amount_2",   100,   "mm"),      # 35:0000.00
    (40, "mor_visibility_rain", 1,  "m"),        # 40:20000
    (41, "mor_visibility_snow", 1,  "m"),        # 41:20000
    (50, "status_word_hex",   None,  ""),        # 50:00000000
    (51, "error_code",        1,     ""),        # 51:000087
    (90, "snow_intensity",    1000,  "mm/h"),    # 90:-9.999;-9.999;-9.9
]

TELEGRAM_TYPE2_FIELDS = [
    (94, "psd_size_classes",   None,  ""),   # 32 size bins, semicolon-separated counts
    (95, "psd_velocity",       None,  "m/s"), # velocity per class
    (96, "psd_particles",      None,  ""),   # particle count per velocity class
    (97, "reserved_97",        None,  ""),
    (98, "reserved_98",        None,  ""),
    (99, "reserved_99",        None,  ""),
]

def parse_field(raw_value: str, scale) -> int | float | str | None:
    """Parse a raw field value, applying scale if numeric. Returns None for sentinel."""
    raw = raw_value.strip()
    if raw == SENTINEL_RAW:
        return None
    if scale is None:
        return raw
    try:
        val = float(raw) / scale
        if scale == 1 and "." not in raw:
            return int(val)
        return val
    except ValueError:
        return raw

def parse_telegram(data: str) -> dict | None:
    """
    Parse a CS/PA telegram response.

    Returns dict with 'type' key ('type1' or 'type2') and parsed fields,
    or None if the data cannot be parsed.
    """
    if not data or not data.strip():
        return None

    # Detect telegram type: Type 1 starts with "TYP" (telegram ID), Type 2 has fields 94-99
    lines = data.split("\n")

    # Check for Type 1: starts with a line like "00:TYP OP4A" or contains "TYP"
    has_typ = any("TYP" in line for line in lines)
    has_94 = any(re.match(r"^\s*94:", line) for line in lines)

    # A complete OP4A response can contain both metadata and fields 94-99.
    if has_typ and has_94:
        type1 = _parse_type1(lines) or {}
        type2 = _parse_type2(lines) or {}
        result = {**type1, **type2, "type": "combined"}
        return result if len(result) > 1 else None
    # Telegram type 1: structured metadata
    if has_typ:
        return _parse_type1(lines)
    # Telegram type 2: particle size distribution
    elif has_94:
        return _parse_type2(lines)
    else:
        return None

def _parse_type1(lines: list[str]) -> dict | None:
    """Parse Type 1 telegram (fields 00-90)."""
    result = {"type": "type1"}

    # Build a regex pattern to match field numbers
    field_map = {f[0]: f for f in TELEGRAM_TYPE1_FIELDS}

    for line in lines:
        # Field 00 (telegram_id) may appear as "TYP ..." without "00:" prefix
        typ_match = re.match(r"^\s*(TYP\s+\S+)", line)
        if typ_match and 0 in field_map:
            _, name, scale, unit = field_map[0]
            result[name] = typ_match.group(1).strip()
            continue

        match = re.match(r"^\s*(\d{1,2}):\s*(.*)", line)
        if not match:
            continue
        field_num = int(match.group(1))
        raw_value = match.group(2)

        if field_num in field_map:
            _, name, scale, unit = field_map[field_num]
            # Handle multi-value fields (semicolon-separated)
            if ";" in raw_value:
                parts = [p.strip() for p in raw_value.rstrip(";").split(";") if p.strip()]
                parsed = [parse_field(p, scale) for p in parts]
            else:
                parsed = parse_field(raw_value, scale)
            result[name] = parsed

    return result if len(result) > 1 else None

def _parse_type2(lines: list[str]) -> dict | None:
    """Parse Type 2 telegram (fields 94-99, particle distribution)."""
    result = {"type": "type2"}

    field_map = {f[0]: f for f in TELEGRAM_TYPE2_FIELDS}

    for line in lines:
        match = re.match(r"^\s*(\d{2}):\s*(.*)", line)
        if not match:
            continue
        field_num = int(match.group(1))
        raw_value = match.group(2).strip()

        if field_num in field_map:
            _, name, scale, unit = field_map[field_num]
            if field_num in (94, 95, 96):
                # Semicolon-separated lists
                parts = [p.strip() for p in raw_value.rstrip(";").split(";")]
                parsed = []
                for p in parts:
                    if not p:
                        parsed.append(0)
                    elif field_num == 94:
                        # 4-digit hex counts
                        parsed.append(int(p, 16))
                    elif field_num == 95:
                        # velocity floats
                        parsed.append(float(p) if "." in p else int(p))
                    else:
                        # particle counts (decimal)
                        parsed.append(int(p))
            else:
                parsed = parse_field(raw_value, scale)
            result[name] = parsed

    return result if len(result) > 1 else None
