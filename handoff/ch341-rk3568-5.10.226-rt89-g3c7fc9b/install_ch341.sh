#!/bin/sh
set -eu

EXPECTED_ARCH="aarch64"
EXPECTED_KERNEL="5.10.226-rt89-g3c7fc9b"
EXPECTED_VERMAGIC="5.10.226-rt89-g3c7fc9b SMP mod_unload aarch64"
EXPECTED_SHA256="40e070f262c2ec88db7dae41b023d72deb574d32f1fb86cd12c04dbecc6db0e9"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MODULE_SOURCE="${SCRIPT_DIR}/ch341.ko"
MODULE_DIR="/lib/modules/${EXPECTED_KERNEL}/extra"
MODULE_TARGET="${MODULE_DIR}/ch341.ko"
LOAD_CONFIG="/etc/modules-load.d/ch341.conf"

fail() {
    printf '错误：%s\n' "$*" >&2
    exit 1
}

[ "$(id -u)" -eq 0 ] || fail "请使用 root 或 sudo 运行此脚本"
[ "$(uname -m)" = "$EXPECTED_ARCH" ] || fail "CPU 架构不是 ${EXPECTED_ARCH}，当前为 $(uname -m)"
[ "$(uname -r)" = "$EXPECTED_KERNEL" ] || fail "内核不匹配，要求 ${EXPECTED_KERNEL}，当前为 $(uname -r)"
[ -f "$MODULE_SOURCE" ] || fail "未找到 ${MODULE_SOURCE}"

for command_name in sha256sum modinfo depmod modprobe install; do
    command -v "$command_name" >/dev/null 2>&1 || fail "缺少命令：${command_name}"
done

actual_hash=$(sha256sum "$MODULE_SOURCE" | awk '{print $1}')
[ "$actual_hash" = "$EXPECTED_SHA256" ] || fail "模块 SHA-256 校验失败"

actual_vermagic=$(modinfo -F vermagic "$MODULE_SOURCE")
[ "$actual_vermagic" = "$EXPECTED_VERMAGIC" ] || fail "模块 vermagic 不匹配：${actual_vermagic}"

if [ -e "$MODULE_TARGET" ]; then
    backup_dir="${SCRIPT_DIR}/backup/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    cp -a "$MODULE_TARGET" "$backup_dir/ch341.ko"
    printf '已备份原模块到 %s\n' "$backup_dir/ch341.ko"
fi

mkdir -p "$MODULE_DIR"
install -m 0644 "$MODULE_SOURCE" "$MODULE_TARGET"
depmod -a "$EXPECTED_KERNEL"
modprobe ch341
printf '%s\n' ch341 >"$LOAD_CONFIG"
chmod 0644 "$LOAD_CONFIG"

printf '\n安装完成。\n'
printf '模块路径：%s\n' "$MODULE_TARGET"
printf '开机加载：%s\n' "$LOAD_CONFIG"
printf '模块校验：%s\n' "$(sha256sum "$MODULE_TARGET" | awk '{print $1}')"
printf '当前状态：\n'
lsmod | awk 'NR == 1 || $1 == "ch341"'

if command -v lsusb >/dev/null 2>&1 && ! lsusb | grep -qi '1a86:7523'; then
    printf '\n提示：当前未检测到 1a86:7523；如设备尚未连接，可在连接后继续验收。\n'
fi

printf '\n请按照 README 的“重启后验收”一节完成最终检查。\n'
