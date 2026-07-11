# AWS C Reader

面向 TL3568/RK3568 工控机的双路串口采集程序。程序以原始数据为主，端侧解析只用于健康预览。

## 默认设备

- Parsivel2：9600 8N1，发送 `CS/PA\r`，遇到 ETX 后结束读取，默认每 10 秒采集。
- Modbus 气象站：19200 8N1，slave 1，FC03，一次读取 42 个寄存器，默认每 10 秒采集。
- 默认使用 `/dev/serial/by-id`，不依赖可能变化的 `ttyUSB0/1` 编号。

板载 `ttyS7/8/9` 可通过配置 `direction_gpio=55/58/59` 启用半双工方向控制。USB CH340 连接应保持 `-1`。

实机完整 p2 帧约 5189 bytes，在 9600 baud 下单次传输约 6.1 秒，因此生产默认周期设为 10 秒、总超时设为 7.5 秒；配置仍可覆盖，但程序不会并发堆积同一串口的请求。

## 构建与测试

```sh
make
make test
bin/aws-reader --config config/aws-reader.conf.example --validate-config
```

程序只依赖 libc、pthread 和 libm。压缩阶段调用系统已有的 `gzip` 与 `sha256sum`。

## 数据契约

- `raw/*.jsonl.active`：当前追加文件，包含完整请求、响应、双时间戳、串口参数和明确状态。
- `health/*.jsonl.active`：通信状态和少量 Modbus 健康预览，不替代原始帧。
- `*.jsonl.gz` 与同名 `.sha256`：已封存、可回传的文件。

程序按单调时间轮转。异常断电遗留的 `.active` 文件会在下次启动时封存为 recovered 文件，不截断或修复原始字节。磁盘可用空间低于阈值时暂停新增记录，不自动删除尚未确认回传的数据。

常见状态包括 `ok`、`open_error`、`io_error`、`timeout`、`short_frame`、`crc_error`、`protocol_error`、`modbus_exception`、`truncated` 和 `interrupted`。有效 Modbus 帧中的 NaN 会在 health 记录中单独计数，不会被归类为串口中断或低温宕机；受控停止时保留的半帧标记为 `interrupted`。

## 工控机部署

先将数据盘固定挂载到配置中的 `required_mountpoint`，再执行：

```sh
sudo make install
sudoedit /etc/aws-reader.conf
sudo systemctl daemon-reload
sudo systemctl enable --now aws-reader.service
sudo systemctl enable --now aws-reader-health.timer
systemctl status aws-reader.service
journalctl -u aws-reader-health.service
journalctl -u aws-reader.service -f
```

健康检查 timer 每 15 分钟执行一次，结果写入 `/data/aws/monitor/latest.json`，并按 UTC 日期追加到 `/data/aws/monitor/history/`。手动检查当前 20 分钟数据：

```sh
sudo systemctl start aws-reader-health.service
systemctl status aws-reader-health.service
cat /data/aws/monitor/latest.json
```

检查条件包括服务、挂载点、设备路径、磁盘空间、两路样本数量和时效、序号连续性、成功率、完整帧长度，以及近期 gzip/SHA-256/JSON 完整性。合法 Modbus NaN 只统计，不会误报为串口故障。

传输程序只应选择同时具有 `.sha256` 的 `.jsonl.gz`。收到远端校验成功的 ACK 后，再由独立维护流程删除对应压缩包和校验文件。

## 远端离线解析

仓库根目录包含 `parsivel2_parser.py`。接收端取得 gzip 和同名 SHA-256 后，可直接批量生成 `aws.parsed.v1` JSONL：

```sh
python3 c_reader/tools/decode_raw_data.py /incoming/aws-data \
  --require-sha256 \
  --output parsed.jsonl
```

处理顺序为：校验 SHA-256、解压 JSONL、按 `schema` 读取、将 `response_hex` 无损还原为 bytes、再次校验 Modbus CRC 或 p2 报文边界，最后解析字段。p2 完整 OP4A 帧同时包含元数据和 94-99 分布区时输出 `type=combined`；Modbus 输出 21 个 CDAB float 字段，非有限值写为 `null` 并列入 `nan_fields`。

这意味着端侧格式已经支持“原始数据压缩回传、远端自动解析”。仍需按实际卫星链路另行接入上传队列、接收端目录监听和 ACK；这些传输组件不属于采集进程，也不应在未收到 ACK 时删除端侧文件。
