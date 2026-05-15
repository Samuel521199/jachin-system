#!/usr/bin/env bash
# L3 macOS 启动脚本 — 对应 Windows 的 run_l3.bat
# 用法: bash run_l3_mac.sh [--gateway]
# 双击运行提示: Finder → 右键 → 打开（首次需要授权）

# 切换到脚本所在目录（便携包根）
cd "$(dirname "$0")"
APP_ROOT="$(pwd)"

mkdir -p logs
export JACHIN_LOG_DIR="$APP_ROOT/logs"
export JACHIN_APP_ROOT="$APP_ROOT"

MODE="--ws-only"
[ "${1:-}" = "--gateway" ] && MODE="--gateway"

# 加载 .env（若存在）
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    source ".env"
    set +a
fi

# 寻找 macOS L3 二进制
L3_EXE=""
for f in \
    "bin/l3_node-aarch64-apple-darwin" \
    "bin/l3_node-x86_64-apple-darwin" \
    "bin/l3_node"
do
    if [ -f "$f" ]; then
        L3_EXE="$f"
        break
    fi
done

if [ -z "$L3_EXE" ]; then
    echo "[ERR] bin/l3_node-*-apple-darwin not found."
    echo "  Build: bash scripts/build_l3_sidecar_mac.sh"
    # macOS: keep terminal open if launched via double-click
    read -r -p "Press Enter to exit..." || true
    exit 1
fi

# macOS Gatekeeper：首次运行移除 quarantine 标记
if command -v xattr &>/dev/null; then
    xattr -d com.apple.quarantine "$L3_EXE" 2>/dev/null || true
fi
chmod +x "$L3_EXE"

echo "[L3] Starting $L3_EXE $MODE"
echo "[L3] Logs: $APP_ROOT/logs/l3_debug.log"
"$L3_EXE" "$MODE" &
L3_PID=$!
echo "[L3] Started (PID=$L3_PID). Health: http://127.0.0.1:18991/api/health"

# 等待进程结束（Ctrl+C 时正常退出）
trap 'kill "$L3_PID" 2>/dev/null; echo "[L3] Stopped."' INT TERM
wait "$L3_PID" 2>/dev/null || true
