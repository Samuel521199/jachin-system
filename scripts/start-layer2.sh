#!/bin/bash
# =============================================================================
# Layer2 (用户) - 一键启动 (Linux / macOS)
# 支持选择：nexus_daemon (完整版) 或 daemon (轻量版)
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

MODE="${1:-}"

# 交互式选择
if [ -z "$MODE" ]; then
    echo ""
    echo "=========================================="
    echo "  Layer2 启动选项"
    echo "=========================================="
    echo ""
    echo "  [1] nexus_daemon (完整版)"
    echo "      Event Bus + Ingress + Telemetry + Updater"
    echo "      端口: 9000 | 需 core/nexus_daemon"
    echo ""
    echo "  [2] daemon (轻量版)"
    echo "      心跳 + 蓝图执行引擎 | 需先配对"
    echo ""
    printf "  请选择 [1/2] (默认 1): "
    read -r choice
    if [ "$choice" = "2" ]; then
        mode="daemon"
    else
        mode="nexus"
    fi
else
    mode="$MODE"
fi

# 轻量版 daemon
if [ "$mode" = "daemon" ]; then
    exec "$SCRIPT_DIR/run-daemon.sh"
fi

# 完整版 nexus_daemon
if [ ! -d "$PROJECT_ROOT/core/nexus_daemon" ]; then
    echo ""
    echo "[提示] 未找到 nexus_daemon，可改用轻量版 daemon："
    echo "  ./scripts/start-layer2.sh daemon"
    echo ""
    echo "[ERROR] core/nexus_daemon not found. Run install-layer2.sh first."
    exit 1
fi

JACHIN_CONDA_ENV="${JACHIN_CONDA_ENV:-}"
[ -f "$HOME/.jachin/conda_env" ] && JACHIN_CONDA_ENV=$(cat "$HOME/.jachin/conda_env")

if [ -n "$JACHIN_CONDA_ENV" ] && command -v conda &>/dev/null; then
    echo ""
    echo "=========================================="
    echo "  Layer2 (nexus_daemon) starting"
    echo "=========================================="
    echo "  Ingress: http://127.0.0.1:9000"
    echo "  Press Ctrl+C to stop"
    echo ""
    exec conda run -n "$JACHIN_CONDA_ENV" python -m core.nexus_daemon
fi

PYTHON="${JACHIN_PYTHON:-python3}"
command -v "$PYTHON" &>/dev/null || PYTHON="python"
command -v "$PYTHON" &>/dev/null || { echo "[ERROR] Python not found. Run install-layer2.sh first."; exit 1; }

REQ="$PROJECT_ROOT/core/requirements.txt"
if [ -f "$REQ" ] && ! "$PYTHON" -c "import fastapi" &>/dev/null; then
    echo "[INFO] Installing deps..."
    PYTHONUTF8=1 "$PYTHON" -m pip install -q -r "$REQ"
fi

echo ""
echo "=========================================="
echo "  Layer2 (nexus_daemon) starting"
echo "=========================================="
echo "  Ingress: http://127.0.0.1:9000"
echo "  Press Ctrl+C to stop"
echo ""

exec "$PYTHON" -m core.nexus_daemon
