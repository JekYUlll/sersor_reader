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
systemctl status aws-reader.service
journalctl -u aws-reader.service -f
```

传输程序只应选择同时具有 `.sha256` 的 `.jsonl.gz`。收到远端校验成功的 ACK 后，再由独立维护流程删除对应压缩包和校验文件。
