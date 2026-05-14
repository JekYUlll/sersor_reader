# RS485 传感器数据规格说明

> 本文档描述当前系统连接的两台 RS485 传感器：OTT Parsivel2 激光雨滴谱仪和 Modbus RTU 气象站。包含传感器参数、数据形状、JSON 输出格式和字段释义。

---

## 1. 系统概览

```
┌─────────────────────────────────────────────────────┐
│  Linux Host                                          │
│  ├─ /dev/ttyUSB0 ─── Parsivel2 (CH341, 9600 8N1)    │
│  └─ /dev/ttyUSB1 ─── Modbus Weather Station          │
│                        (CH341, 19200 8N1, Slave=1)   │
│                                                       │
│  采集脚本:                                             │
│  ├─ read_sensor.py   → sensor_*.jsonl                │
│  └─ modbus_reader.py → modbus_*.jsonl                │
└─────────────────────────────────────────────────────┘
```

| 属性 | Parsivel2 | Modbus 气象站 |
|------|-----------|---------------|
| 接口 | RS485 (半双工) | RS485 (半双工) |
| USB 适配器 | CH341 (QinHeng) | CH341 (QinHeng) |
| 波特率 | 9600 | 19200 |
| 数据位/校验/停止位 | 8N1 | 8N1 |
| 协议 | ASCII 文本 (CS/PA 命令) | Modbus RTU |
| 轮询间隔 | 5 s | 10 s |
| 输出格式 | JSON Lines | JSON Lines |
| 传感器类型 | 激光雨滴谱仪 (Disdrometer) | 多参数气象站 |

---

## 2. OTT Parsivel2 — 激光雨滴谱仪

### 2.1 传感器简介

Parsivel2 是一款基于激光消光原理的光学雨滴谱仪 (Optical Disdrometer)。传感器发射一束激光带 (激光波段 650 nm, 输出功率 0.5 mW)，当降水粒子穿过激光带时会引起消光，通过测量消光信号的幅度和持续时间，传感器可以同时测定每个粒子的**粒径**和**下落速度**。

**核心参数：**

| 参数 | 值 |
|------|-----|
| 测量面积 | 54 cm² (180 mm × 30 mm) |
| 粒径范围 | 0.2 – 25 mm (32 个非等间距等级) |
| 速度范围 | 0.2 – 20 m/s (32 个非等间距等级) |
| 降水类型分类 | SYN/METAR/NWS 三种天气码 |
| 雷达反射率 | –9.999 – 99.999 dBz |
| 能见度 (MOR) | 0 – 20000 m |
| 供电 | 7 – 30 VDC (典型 12V, 加热时 24V) |
| 固件 | 2.11.11 / DSP 2.11.1 |

### 2.2 数据形状

每发送一次 `CS/PA`（Current Sample / Particles）命令，传感器返回两种数据页中的一种，两次命令交替出现：

#### Type 1 — 传感器状态与测量值

一条完整的 Type 1 记录包含 32 个字段。JSON 示例：

```json
{
  "timestamp": "2026-05-11T23:24:08",
  "sensor": "parsivel2",
  "type": "type1",
  "data": {
    "telegram_id": "TYP OP4A",
    "rain_intensity": 0.0,
    "rain_amount": 0.0,
    "weather_syn_present": 0,
    "weather_syn_past": 0,
    "weather_metar": "NP",
    "weather_nws": "C",
    "radar_reflectivity": null,
    "mor_visibility": 20000,
    "laser_band_amplitude": 10,
    "sensor_temp": 25.347,
    "particle_count": 0,
    "heating_current": 26,
    "serial_number": 452920,
    "firmware_version": "2.11.11",
    "dsp_version": "2.11.1",
    "sensor_head_fw": "0.51",
    "supply_voltage": 11.9,
    "heating_state": 0,
    "time_str": "11:35:00",
    "date_str": "01:46:53",
    "calib_date": "15.05.2032",
    "sensor_head_calib_date": "",
    "sensor_head_sn": "",
    "rain_intensity_hires": 0.0,
    "sensor_status": 0,
    "laser_status": 37,
    "optics_status": 25,
    "temp_status": 24,
    "precip_intensity": 0.00008,
    "precip_amount": 0.0,
    "precip_type": "0000.0",
    "precip_type_metar": "0000.00",
    "precip_intensity_2": 0.0,
    "precip_amount_2": 0.0,
    "mor_visibility_rain": 20000,
    "mor_visibility_snow": 20000,
    "status_word_hex": "00000000",
    "error_code": 88,
    "snow_intensity": [null, null, -0.0099]
  }
}
```

**Type 1 字段释义：**

| 字段 | 类型 | 单位 | 说明 |
|------|------|------|------|
| `telegram_id` | string | — | 传感器型号标识，固定 `"TYP OP4A"` |
| `rain_intensity` | float | mm/h | 降雨强度 (分辨率 0.001) |
| `rain_amount` | float | mm | 累积降雨量 (分辨率 0.01) |
| `weather_syn_present` | int | code | SYNOP 当前天气码 (4677 表) |
| `weather_syn_past` | int | code | SYNOP 过去天气码 |
| `weather_metar` | string | code | METAR/SPECI 天气类型 (2 字符) |
| `weather_nws` | string | code | NWS 天气类型 (1 字符) |
| `radar_reflectivity` | float or null | dBz | 雷达反射率。原始值 `–9.999` 表示无数据 → JSON 中为 `null` |
| `mor_visibility` | int | m | 气象光学能见度 (MOR), 0–20000 |
| `laser_band_amplitude` | int | — | 激光波段信号幅度 (传感器健康指标) |
| `sensor_temp` | float | °C | 传感器内部温度 (分辨率 0.001) |
| `particle_count` | int | — | 当前采样周期内检测到的粒子总数 |
| `heating_current` | int | — | 加热电流状态值 |
| `serial_number` | int | — | 传感器序列号 |
| `firmware_version` | string | — | 固件版本 |
| `dsp_version` | string | — | DSP 固件版本 |
| `sensor_head_fw` | string | — | 探头端固件版本 |
| `supply_voltage` | float | V | 供电电压 |
| `heating_state` | int | — | 加热状态: 0=关闭, 1=低功率, 2=高功率 |
| `time_str` | string | — | 传感器内部时间 (HH:MM:SS) |
| `date_str` | string | — | 传感器内部日期 (DD:MM:YY) |
| `calib_date` | string | — | 传感器标定日期 |
| `sensor_head_calib_date` | string | — | 探头标定日期 |
| `sensor_head_sn` | string | — | 探头序列号 |
| `rain_intensity_hires` | float | mm/h | 高分辨率雨强 (分辨率 0.01) |
| `sensor_status` | int | — | 传感器总状态字 (0=正常) |
| `laser_status` | int | — | 激光状态 (见注) |
| `optics_status` | int | — | 光学系统状态 (见注) |
| `temp_status` | int | — | 温度状态 (见注) |
| `precip_intensity` | float | mm/h | 降水强度 (高精度, 分辨率 0.001) |
| `precip_amount` | float | mm | 累积降水量 (高精度, 分辨率 0.01) |
| `precip_type` | string | — | 降水类型代码 |
| `precip_type_metar` | string | — | METAR 降水类型代码 |
| `precip_intensity_2` | float | mm/h | 降水强度 (另一个精度等级) |
| `precip_amount_2` | float | mm | 降水量 (另一个精度等级) |
| `mor_visibility_rain` | int | m | 雨中能见度 (MOR) |
| `mor_visibility_snow` | int | m | 雪中能见度 (MOR) |
| `status_word_hex` | string | — | 状态字 (16 进制) |
| `error_code` | int | — | 错误码 (0=正常) |
| `snow_intensity` | array[float\|null] | mm/h | 雪强 (3 个值对应不同降雪类型)。`null` = 无数据 |

> **传感器状态字段 (sensor_status)**: 每位代表一个子系统。第 0 位=激光, 第 1 位=光学, 第 2 位=温度, 第 3 位=加热, 第 4 位=DSP。0 表示正常。
>
> **laser_status / optics_status / temp_status**: 分别报告激光、光学镜头、温度的详细状态。非 0 值时需根据手册查表诊断。
>
> **snow_intensity**: 三个值对应 S1 (颗粒状雪) / S2 (片状雪) / S3 (总雪强) 的降雪强度。`null` 来自原始值 `–9.999` 哨兵 (sentinel)，表示该类别无有效数据。

---

#### Type 2 — 粒子粒径与速度分布

```json
{
  "timestamp": "2026-05-11T23:24:14",
  "sensor": "parsivel2",
  "type": "type2",
  "data": {
    "psd_size_classes": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    "psd_velocity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "psd_particles": [0, 0, 0, 0, 0, 0, 0],
    "reserved_97": "",
    "reserved_98": "",
    "reserved_99": ""
  }
}
```

**Type 2 字段释义：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `psd_size_classes` | array[int] (22) | 各粒径等级的粒子计数 (4 位 16 进制原始值 → JSON 整数)。无降水时全部为 0 |
| `psd_velocity` | array[float] (7) | 各速度等级的值 (m/s) |
| `psd_particles` | array[int] (7) | 各速度等级的粒子计数。无降水时全部为 0 |

> **完整 PSD 矩阵**: Type 2 的 Line 94–96 是 32×32 粒子谱的**汇总数据**。原始 CS/PA 响应中, 前段的二进制数据 (在 Line 94 之前) 是 32×32 完整矩阵 — 每行 32 个 16 进制 4 位数, 代表"粒径 × 速度"交叉格的粒子计数。当前解析器提取了 Summary 部分; 完整矩阵可从 raw 数据中额外解码。

### 2.3 Weather 代码速查

| `weather_metar` | `weather_nws` | 含义 |
|-----------------|---------------|------|
| `"NP"` | `"C"` | 无降水 / 晴天 (No Precipitation / Clear) |
| `"RA"` | `"R"` | 雨 (Rain) |
| `"SN"` | `"S"` | 雪 (Snow) |
| `"DZ"` | `"D"` | 毛毛雨 (Drizzle) |
| `"GR"` | `"H"` | 冰雹 (Hail) |
| `"GS"` | `"P"` | 霰 / 小冰雹 |

### 2.4 轮询时序

```
t=0    CS/PA → Type 1 (metadata)     ← 5s
t=5    CS/PA → Type 2 (PSD)          ← 5s
t=10   CS/PA → Type 1 (metadata)     ← 5s
t=15   CS/PA → Type 2 (PSD)          ← 5s
...
```

每次查询只能获得一种数据页，传感器自动交替返回。如果需要同一时刻的完整数据 (Type1 + Type2), 可以将两个连续轮询的结果配对。

---

## 3. Modbus RTU 气象站

### 3.1 传感器简介

基于 Modbus RTU 协议的多参数气象传感器, 通过 RS485 总线以 19200 baud 通信。一次读取 21 个 32 位浮点数 (功能码 03, 每个浮点占 2 个 Modbus 寄存器, CDAB 字节序)。

### 3.2 数据形状

```json
{
  "timestamp": "2026-05-11T23:24:03",
  "sensor": "modbus",
  "data": {
    "Batt_volt_Min": 12.673,
    "PTemp": 25.936,
    "WD": 255.0,
    "WS_Avg": 0.01,
    "Airtemp_Avg": 26.5,
    "RH_Avg": 57.0,
    "BP_Avg": 1008.1,
    "Dew_temp_Avg": 17.3,
    "LPS_GHI_Avg": 0.214,
    "LPS_GHI_Max": 0.214,
    "Flux_min": 0.0,
    "Flux_avg": 0.0,
    "Flux_max": 0.0,
    "Flux_std": 0.0,
    "Flux_cum": 0.0,
    "wind_min": 0.0,
    "wind_avg": 0.0,
    "wind_max": 0.0,
    "TargetmV_Avg": -0.017,
    "DetectorTC_Avg": 25.636,
    "TargetTC_Avg": 25.039
  }
}
```

### 3.3 字段释义

| 字段 | 类型 | 单位 | Modbus 寄存器 (0-based) | 说明 |
|------|------|------|--------------------------|------|
| `Batt_volt_Min` | float | V | 0 | 电池最低电压 |
| `PTemp` | float | °C | 2 | 面板/处理器温度 |
| `WD` | float | ° (度) | 4 | 风向 (0–360°, 气象学角度) |
| `WS_Avg` | float | m/s | 6 | 平均风速 |
| `Airtemp_Avg` | float | °C | 8 | 空气温度 |
| `RH_Avg` | float | % | 10 | 相对湿度 |
| `BP_Avg` | float | hPa | 12 | 大气压 |
| `Dew_temp_Avg` | float | °C | 14 | 露点温度 |
| `LPS_GHI_Avg` | float | W/m² | 16 | 太阳总辐射 (GHI) 平均值 |
| `LPS_GHI_Max` | float | W/m² | 18 | 太阳总辐射最大值 |
| `Flux_min` | float | g/m²/s | 20 | 沉积通量最小值 |
| `Flux_avg` | float | g/m²/s | 22 | 沉积通量平均值 |
| `Flux_max` | float | g/m²/s | 24 | 沉积通量最大值 |
| `Flux_std` | float | g/m²/s | 26 | 沉积通量标准差 |
| `Flux_cum` | float | g/m²/s | 28 | 沉积通量累积值 |
| `wind_min` | float | km/h | 30 | 最小风速 |
| `wind_avg` | float | km/h | 32 | 平均风速 (km/h) |
| `wind_max` | float | km/h | 34 | 最大风速 (阵风) |
| `TargetmV_Avg` | float | mV | 36 | 目标电压 (辐射相关) |
| `DetectorTC_Avg` | float | °C | 38 | 探测器热电偶温度 |
| `TargetTC_Avg` | float | °C | 40 | 目标热电偶温度 |

### 3.4 数据解读示例

以 2026-05-11 23:24:03 的实际采集数据为例：

- **供电正常**: 电池电压 12.67V, 稳定在额定范围
- **夜间**: 太阳辐射 GHI ≈ 0.21 W/m² (接近 0, 符合夜间特征)
- **温和**: 气温 26.5°C, 相对湿度 57%, 露点 17.3°C — 天气晴好
- **基本无风**: 平均风速 0.01 m/s, 阵风 0
- **大气压**: 1008.1 hPa, 正常范围
- **热电偶正常**: DetectorTC ≈ 25.6°C, TargetTC ≈ 25.0°C (与气温接近, 说明传感器辐射平衡正常)

---

## 4. JSON Lines 格式规范

两个采集脚本均输出 **JSON Lines** (`.jsonl`) 格式: 每行一条完整的 JSON 记录, 以换行符 `\n` 分隔。

**Parsivel2 通用结构:**
```json
{"timestamp": "<ISO8601>", "sensor": "parsivel2", "type": "type1|type2", "data": {...}}
```
解析失败时:
```json
{"timestamp": "...", "sensor": "parsivel2", "error": "parse_failed", "raw": "<truncated>"}
```

**Modbus 通用结构:**
```json
{"timestamp": "<ISO8601>", "sensor": "modbus", "data": {...}}
```

**消费示例 (Python):**
```python
import json
with open("sensor_20260511_230113.jsonl") as f:
    for line in f:
        rec = json.loads(line)
        if rec.get("type") == "type1":
            print(f"Temp: {rec['data']['sensor_temp']}°C")
```

**消费示例 (Shell/jq):**
```bash
# 提取 Parsivel2 的温度和天气
grep '"type1"' sensor_*.jsonl | jq -r '[.timestamp, .data.sensor_temp, .data.weather_metar] | @tsv'

# 提取 Modbus 的风速
jq -r '[.timestamp, .data.WS_Avg, .data.WD] | @tsv' modbus_*.jsonl
```

---

## 5. 哨兵值与异常处理

| 原始值 | JSON 表示 | 含义 |
|--------|-----------|------|
| `–9.999` | `null` | 无有效测量数据 (传感器未获取到此参数) |
| `"ERR: ..."` | string `"ERR: ..."` | Modbus 寄存器读取异常 (通信超时/CRC 错误) |
| 空字符串 `""` | `""` | 字段未配置或不可用 |
| `parse_failed` | error record | Parsivel2 响应的 ASCII 数据无法被解析器识别 |

> **哨兵值 –9.999**: Parsivel2 用此值表示"无效测量"。常见于无降水时的 `radar_reflectivity`、`snow_intensity` 等字段。解析器将其转换为 JSON `null` 以方便程序判断 (`if val is None`)。

---

## 6. 文件清单

| 文件 | 功能 |
|------|------|
| `read_sensor.py` | Parsivel2 主采集脚本 (ttyUSB0, 9600, 5s 间隔) |
| `modbus_reader.py` | Modbus 气象站主采集脚本 (ttyUSB1, 19200, 10s 间隔) |
| `parsivel2_parser.py` | CS/PA 报文解析器 (Type1/Type2 自动识别) |
| `sensor_*.jsonl` | Parsivel2 输出日志 (JSON Lines, 自动轮转文件名含时间戳) |
| `modbus_*.jsonl` | Modbus 输出日志 (JSON Lines, 自动轮转文件名含时间戳) |
