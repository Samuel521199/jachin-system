#!/usr/bin/env bash
# L3 独立运行脚本 (macOS / Linux) — 对应 Windows 的 run_l3.ps1
#
# 用法: 默认 --ws-only（不依赖 L2）。需 L2 配对/心跳时: bash scripts/run_l3.sh --gateway
# 需 .env 有 DASHSCOPE_API_KEY（或 OPENAI_API_KEY）
# 打包模式：无 Python 时自动调用 bin/l3_node-*-apple-darwin

set -uo pipefail

export PYTHONUNBUFFERED=1
export PYTHONUTF8=1
export LOG_LEVEL=DEBUG
# 深度执行日志：ReAct/LLM 全文、工具入出参、耗时 → 控制台 + l3_debug.log（设为 0 可关）
export JACHIN_L3_DEEP_LOG=1
# 与 start-layer3 一致：避免 l3_mcp_cache 旧 HR 包盖过仓库 recruitment_scheduler
export JACHIN_DEV_HR_FIRST=1
export JACHIN_MERGE_LOCAL_MCP_INTO_TOOL_POOL=1

# ---------- 推断应用根 ----------
# 脚本在 scripts/ 下时，APP_ROOT 为父目录；脚本被复制到其他地方时向上寻找含 bin/ 或 l3_node/ 的目录
_BASH_SOURCE="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$_BASH_SOURCE")" && pwd)"
APP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 优先找含 bin/ 的目录（便携包）
for candidate in "$(pwd)" "$(cd "$(pwd)/.." && pwd 2>/dev/null || echo '')" "$APP_ROOT"; do
    [ -z "$candidate" ] && continue
    if [ -d "$candidate/bin" ]; then
        APP_ROOT="$candidate"
        break
    fi
done

if [ ! -d "$APP_ROOT/l3_node" ] && [ ! -d "$APP_ROOT/bin" ]; then
    echo "[ERR] Project root (l3_node or bin) not found. APP_ROOT=$APP_ROOT" >&2
    exit 1
fi

export JACHIN_APP_ROOT="$APP_ROOT"

# 便携包模式：日志写入 logs/
BIN_DIR="$APP_ROOT/bin"
if [ -d "$BIN_DIR" ]; then
    LOGS_DIR="$APP_ROOT/logs"
    mkdir -p "$LOGS_DIR"
    export JACHIN_LOG_DIR="$LOGS_DIR"
fi

# ---------- 运行模式 ----------
MODE="--ws-only"
for arg in "$@"; do
    [ "$arg" = "--gateway" ] && MODE="--gateway"
done

# ---------- 加载 .env ----------
ENV_FILE="$APP_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# ---------- 检测运行方式 ----------
L3_EXE=""
if [ -d "$BIN_DIR" ]; then
    # macOS 侧车：优先带 target triple 命名的二进制
    for f in \
        "$BIN_DIR/l3_node-aarch64-apple-darwin" \
        "$BIN_DIR/l3_node-x86_64-apple-darwin"
    do
        if [ -f "$f" ]; then
            L3_EXE="$f"
            break
        fi
    done
    # 无 triple 时尝试通用名（开发/测试用）
    if [ -z "$L3_EXE" ] && [ -f "$BIN_DIR/l3_node" ]; then
        L3_EXE="$BIN_DIR/l3_node"
    fi
fi

HAS_PYTHON=0
if command -v python3 &>/dev/null && [ -d "$APP_ROOT/l3_node" ]; then
    HAS_PYTHON=1
fi

# ---------- Python 源码模式 ----------
if [ "$HAS_PYTHON" -eq 1 ]; then
    cd "$APP_ROOT"
    echo "[L3] Python mode, logs to terminal (cwd=$APP_ROOT)"
    LOG_FILE="${JACHIN_LOG_DIR:-$APP_ROOT}/.jachin/l3_powershell_transcript.log"
    LOG_DIR="$(dirname "$LOG_FILE")"
    mkdir -p "$LOG_DIR"
    python3 -m l3_node "$MODE" 2>&1 | tee "$LOG_FILE"

# ---------- exe 模式（打包后便携包） ----------
elif [ -n "$L3_EXE" ]; then
    cd "$APP_ROOT"
    echo "[L3] Exe mode (cwd=$APP_ROOT)"
    echo "[L3] Binary: $L3_EXE"

    # macOS Gatekeeper：首次运行可能有 quarantine 标记，自动移除
    if command -v xattr &>/dev/null; then
        if xattr -p com.apple.quarantine "$L3_EXE" &>/dev/null; then
            echo "[L3] Removing macOS quarantine flag..."
            xattr -d com.apple.quarantine "$L3_EXE" 2>/dev/null || true
        fi
    fi

    chmod +x "$L3_EXE"
    "$L3_EXE" "$MODE" &
    L3_PID=$!
    echo "[L3] PID: $L3_PID. Health: http://127.0.0.1:18991/api/health"

    HEALTH_SHOWN=0
    WAIT_COUNT=0
    LOG_CHECKED=0
    PORTS=(18991 18990 18992 18993 18994)

    _cleanup() {
        if kill -0 "$L3_PID" 2>/dev/null; then
            kill "$L3_PID" 2>/dev/null || true
            echo "[L3] Killed by user (Ctrl+C)"
        fi
    }
    trap '_cleanup' INT TERM

    while kill -0 "$L3_PID" 2>/dev/null; do
        # 日志文件出现时提示路径
        if [ "$LOG_CHECKED" -eq 0 ]; then
            LOG_PATH="${JACHIN_LOG_DIR:-$APP_ROOT}/l3_debug.log"
            if [ -f "$LOG_PATH" ]; then
                LOG_CHECKED=1
                echo "[L3] Debug log: $LOG_PATH"
            fi
        fi

        # 轮询健康检查
        if [ "$HEALTH_SHOWN" -eq 0 ]; then
            for port in "${PORTS[@]}"; do
                if curl -sf "http://127.0.0.1:$port/api/health" \
                        --max-time 2 \
                        -o /tmp/l3_health_$$.json 2>/dev/null; then
                    echo "[L3] Health OK at :$port"
                    cat /tmp/l3_health_$$.json && echo ""
                    rm -f /tmp/l3_health_$$.json
                    HEALTH_SHOWN=1
                    break
                fi
            done
            WAIT_COUNT=$((WAIT_COUNT + 1))
            if [ "$WAIT_COUNT" -eq 5 ]; then
                echo "[L3] Still waiting for L3 HTTP... (if exe crashed, rebuild: python3 scripts/build_l3_sidecar.py --force)"
            fi
        fi
        sleep 2
    done

    wait "$L3_PID" 2>/dev/null || true
    CODE=$?
    echo "[L3] Process exited (code=$CODE)"
    if [ "$CODE" -ne 0 ] && [ "$HEALTH_SHOWN" -eq 0 ]; then
        echo "[L3] L3 may have crashed. Rebuild: python3 scripts/build_l3_sidecar.py --force"
    fi

else
    echo "[ERR] Python (l3_node) or bin/l3_node not found. Run build first." >&2
    echo "  Build: bash scripts/build_l3_sidecar_mac.sh" >&2
    exit 1
fi
