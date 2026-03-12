#!/bin/bash
# =============================================================================
# 边缘智能体守护进程 (轻量版) - 心跳 + 蓝图执行引擎
# 用法: ./scripts/run-daemon.sh [BASE_URL]
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

BASE_URL="${1:-${NEXUS_BASE_URL:-}}"

JACHIN_CONDA_ENV="${JACHIN_CONDA_ENV:-}"
[ -f "$HOME/.jachin/conda_env" ] && JACHIN_CONDA_ENV=$(cat "$HOME/.jachin/conda_env")

PYTHON="${JACHIN_PYTHON:-python3}"
command -v "$PYTHON" &>/dev/null || PYTHON="python"
command -v "$PYTHON" &>/dev/null || { echo "[ERROR] Python not found. Run install-layer2.sh first."; exit 1; }

REQ="$PROJECT_ROOT/core/requirements.txt"
if [ -f "$REQ" ] && ! "$PYTHON" -c "import httpx, rich" &>/dev/null; then
    echo "[INFO] Installing daemon deps (httpx, rich)..."
    PYTHONUTF8=1 "$PYTHON" -m pip install -q httpx rich click
fi

if [ -n "$JACHIN_CONDA_ENV" ] && command -v conda &>/dev/null; then
    if [ -n "$BASE_URL" ]; then
        exec conda run -n "$JACHIN_CONDA_ENV" python -m core.cli daemon --base-url "$BASE_URL"
    else
        exec conda run -n "$JACHIN_CONDA_ENV" python -m core.cli daemon
    fi
else
    if [ -n "$BASE_URL" ]; then
        exec "$PYTHON" -m core.cli daemon --base-url "$BASE_URL"
    else
        exec "$PYTHON" -m core.cli daemon
    fi
fi
