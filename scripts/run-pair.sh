#!/bin/bash
# =============================================================================
# 边缘智能体配对 - 6 位码连接指挥部 (极客终端版)
# 用法: ./scripts/run-pair.sh [BASE_URL]
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

BASE_URL="${1:-${NEXUS_BASE_URL:-http://localhost:3000}}"

JACHIN_CONDA_ENV="${JACHIN_CONDA_ENV:-}"
[ -f "$HOME/.jachin/conda_env" ] && JACHIN_CONDA_ENV=$(cat "$HOME/.jachin/conda_env")

if [ -n "$JACHIN_CONDA_ENV" ] && command -v conda &>/dev/null; then
    conda run -n "$JACHIN_CONDA_ENV" python -m core.cli pair --base-url "$BASE_URL"
else
    PYTHON="${JACHIN_PYTHON:-python3}"
    command -v "$PYTHON" &>/dev/null || PYTHON="python"
    command -v "$PYTHON" &>/dev/null || { echo "[ERROR] Python not found. Run install-layer2.sh first."; exit 1; }
    REQ="$PROJECT_ROOT/core/requirements.txt"
    if [ -f "$REQ" ] && ! "$PYTHON" -c "import click, rich" &>/dev/null; then
        echo "[INFO] Installing CLI deps..."
        PYTHONUTF8=1 "$PYTHON" -m pip install -q click rich
    fi
    "$PYTHON" -m core.cli pair --base-url "$BASE_URL"
fi
