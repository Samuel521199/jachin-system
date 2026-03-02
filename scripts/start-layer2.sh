#!/bin/bash
# =============================================================================
# Layer2 (用户) - 一键启动 (Linux / macOS)
# nexus_daemon @ http://127.0.0.1:9000
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

[ ! -d "$PROJECT_ROOT/core/nexus_daemon" ] && { echo "[ERROR] core/nexus_daemon not found"; exit 1; }

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
