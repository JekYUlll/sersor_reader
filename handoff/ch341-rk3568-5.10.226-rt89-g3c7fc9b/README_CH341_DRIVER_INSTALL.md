# TL3568 / RK3568 工控机 CH340/CH341 驱动安装说明

适用对象：在另一台同款创龙 TL3568 / RK3568 Ubuntu 20.04 工控机上，安装 CH340 USB 转串口驱动。

本文档配套的 `ch341.ko` 已在测试机完成编译、加载、标准模块路径安装和两路真实传感器通信测试。交接安装不需要重新编译，但目标机必须与测试机使用完全相同的内核。

## 1. 配套文件

将整个交接目录传到工控机，目录中应包含：

- `ch341.ko`：已编译的 ARM64 内核模块
- `install_ch341.sh`：带环境检查的安装脚本
- `SHA256SUMS`：文件校验值
- 本说明文档

已验证模块信息：

| 项目 | 值 |
|---|---|
| 工控机架构 | `aarch64` |
| 目标内核 | `5.10.226-rt89-g3c7fc9b` |
| 支持的 USB ID | `1a86:7523`，即常见 CH340/HL-340 |
| 模块 vermagic | `5.10.226-rt89-g3c7fc9b SMP mod_unload aarch64` |
| `ch341.ko` SHA-256 | `40e070f262c2ec88db7dae41b023d72deb574d32f1fb86cd12c04dbecc6db0e9` |

> 该模块来自创龙精确版本 Linux 5.10.226 内核源码中的原生 `drivers/usb/serial/ch341.c`。不要使用旧版 `CH341SER_LINUX` 包中的 `ch34x.c` 替换它，该旧源码已确认不能直接通过当前 Linux 5.10 编译。

## 2. 安装前检查

在目标工控机执行：

```sh
uname -m
uname -r
lsusb | grep -i '1a86:7523' || true
```

前两项必须分别输出：

```text
aarch64
5.10.226-rt89-g3c7fc9b
```

如果架构或内核版本有任何不同，不要强制安装此模块。即使工控机型号相同，系统镜像或内核更新也可能造成不兼容，此时应使用对应内核源码重新编译。

`lsusb` 中看到 `1a86:7523` 说明 CH340 已被 USB 总线识别。未连接 USB 转串口时没有输出是正常的，可以先安装再连接。

## 3. 推荐安装方法

进入交接目录，以 root 身份运行脚本：

```sh
cd /交接目录/ch341-rk3568-5.10.226-rt89-g3c7fc9b
chmod +x install_ch341.sh
sudo ./install_ch341.sh
```

工控机通常直接使用 root 登录；此时可省略 `sudo`。

脚本将依次完成：

1. 检查 root 权限、CPU 架构和运行内核。
2. 校验 `ch341.ko` 的 SHA-256 与 vermagic。
3. 将已有同名模块备份到当前目录的 `backup/`。
4. 安装到 `/lib/modules/5.10.226-rt89-g3c7fc9b/extra/ch341.ko`。
5. 执行 `depmod -a` 和 `modprobe ch341`。
6. 写入 `/etc/modules-load.d/ch341.conf`，使模块开机自动加载。

脚本结束时应显示 `安装完成`。连接 CH340 后检查：

```sh
lsmod | grep '^ch341'
lsusb -t
ls -l /dev/ttyUSB* /dev/serial/by-id/ 2>/dev/null
dmesg | tail -n 50
```

预期结果：

- `lsmod` 能看到 `ch341`。
- `lsusb -t` 中 CH340 接口显示 `Driver=ch341`。
- 至少出现一个 `/dev/ttyUSB*`；连接两只转换器时通常出现两个。
- `dmesg` 出现类似 `ch341-uart converter now attached to ttyUSB0`，且没有 `unknown symbol`、`invalid module format` 或持续断连错误。

## 4. 重启后验收

确认当前业务已停止并允许重启后执行：

```sh
sync
sudo reboot
```

重新登录后执行：

```sh
uname -r
lsmod | grep '^ch341'
cat /etc/modules-load.d/ch341.conf
ls -l /dev/ttyUSB* /dev/serial/by-id/ 2>/dev/null
journalctl -b -u systemd-modules-load.service --no-pager
dmesg | grep -i -E 'ch341|ttyUSB' | tail -n 30
```

验收标准：

- 当前内核仍为 `5.10.226-rt89-g3c7fc9b`。
- `ch341` 已自动加载，配置文件内容为一行 `ch341`。
- CH340 连接后能自动生成串口节点和 `/dev/serial/by-id/` 稳定路径。
- `systemd-modules-load` 没有模块加载失败记录。

建议业务程序使用 `/dev/serial/by-id/` 路径，不要长期依赖可能随插拔顺序变化的 `/dev/ttyUSB0`、`/dev/ttyUSB1` 编号。

## 5. 手动安装命令

仅在安装脚本无法使用、并且第 2 节检查全部通过时采用：

```sh
KERNEL="$(uname -r)"
sudo mkdir -p "/lib/modules/${KERNEL}/extra"
sudo install -m 0644 ch341.ko "/lib/modules/${KERNEL}/extra/ch341.ko"
sudo depmod -a "${KERNEL}"
sudo modprobe ch341
printf '%s\n' ch341 | sudo tee /etc/modules-load.d/ch341.conf >/dev/null
```

随后按第 3、4 节完成当前启动和重启后的验收。

## 6. 常见问题

### `invalid module format`

通常表示目标机内核或模块 vermagic 不匹配。执行：

```sh
uname -r
modinfo -F vermagic ./ch341.ko
dmesg | tail -n 30
```

不要使用 `--force` 绕过版本检查，应为目标内核重新编译模块。

### `Module ch341 not found`

检查模块是否放在当前内核目录，并重新生成索引：

```sh
ls -l "/lib/modules/$(uname -r)/extra/ch341.ko"
sudo depmod -a
sudo modprobe ch341
```

### 已加载模块但没有 `/dev/ttyUSB*`

先确认设备和绑定关系：

```sh
lsusb
lsusb -t
dmesg | grep -i -E 'usb|ch341|ttyUSB' | tail -n 50
```

如果 `lsusb` 中没有 `1a86:7523`，应先检查 USB 供电、线缆、接口和转换器，而不是重复安装驱动。

## 7. 回退

停止所有正在使用 `/dev/ttyUSB*` 的程序，然后执行：

```sh
sudo rm -f /etc/modules-load.d/ch341.conf
sudo modprobe -r ch341
sudo rm -f "/lib/modules/$(uname -r)/extra/ch341.ko"
sudo depmod -a
```

如果安装脚本创建了 `backup/`，可将其中的旧模块恢复到原路径，再运行 `depmod -a`。回退后重启一次并复查 `lsmod` 与系统日志。

## 8. 测试机验证记录

测试机为 RK3568 / aarch64，运行内核 `5.10.226-rt89-g3c7fc9b`。模块安装后已验证：

- `insmod`、`rmmod`、标准 `modprobe ch341` 均成功。
- 两只 `1a86:7523` CH340 均绑定到 `ch341`，生成两个 ttyUSB 节点。
- Modbus RTU 完整 42 寄存器读取成功。
- Parsivel2 使用 `CS/PA\r` 返回完整 `TYP OP4A` 数据帧。
- `/etc/modules-load.d/ch341.conf` 已被 `systemd-modules-load` 正确解析。

交接时应把目标机的安装日期、`uname -a`、模块校验值和重启验收结果补充到现场记录中。
