#!/bin/bash
# =============================================================================
# Layer2 (用户) - 一键安装 (Linux / macOS)
# nexus_daemon + Qdrant (Docker)
#
# 用法: ./scripts/install-layer2.sh [--systemd]
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

"$SCRIPT_DIR/check-prerequisites.sh" layer2 || exit 1

INSTALL_SYSTEMD=false
[ "${1:-}" = "--systemd" ] && INSTALL_SYSTEMD=true

echo ""
echo "=========================================="
echo "  Layer2 (用户) - 一键安装"
echo "=========================================="
echo ""

# Qdrant
echo "[1/3] Qdrant (Docker)..."
if command -v docker &>/dev/null && docker ps &>/dev/null; then
    QDRANT_DATA="${JACHIN_QDRANT_DATA:-}"
    [ -z "$QDRANT_DATA" ] && QDRANT_DATA="$([ -d /data ] && echo /data/qdrant || echo $PROJECT_ROOT/qdrant_storage)"
    mkdir -p "$QDRANT_DATA"
    export JACHIN_QDRANT_DATA="$QDRANT_DATA"
    docker-compose -f docker-compose.qdrant.yml up -d
    echo "  [OK] Qdrant 已启动"
else
    echo "  [WARN] Docker 未运行，跳过"
fi

# nexus_daemon (Conda preferred for Ray/Python 3.11 compatibility)
echo "[2/3] nexus_daemon (Python)..."
REQ="$PROJECT_ROOT/core/requirements.txt"
USE_CONDA=false
if command -v conda &>/dev/null; then
    if ! conda env list 2>/dev/null | grep -q "jachin-layer2"; then
        echo "  Creating conda env jachin-layer2 (Python 3.11)..."
        conda create -n jachin-layer2 python=3.11 -y 2>/dev/null
    fi
    if PYTHONUTF8=1 conda run -n jachin-layer2 pip install -q -r "$REQ" 2>/dev/null; then
        USE_CONDA=true
        mkdir -p "$HOME/.jachin"
        echo "jachin-layer2" > "$HOME/.jachin/conda_env"
    fi
fi
if ! $USE_CONDA; then
    REQ_ALT="$PROJECT_ROOT/core/requirements-layer2.txt"
    [ ! -f "$REQ_ALT" ] && REQ_ALT="$REQ"
    [ ! -f "$REQ_ALT" ] && { echo "[ERROR] core requirements not found"; exit 1; }
    PYTHON="${JACHIN_PYTHON:-python3}"
    command -v "$PYTHON" &>/dev/null || PYTHON="python"
    PYTHONUTF8=1 "$PYTHON" -m pip install -q -r "$REQ_ALT" || { echo "[ERROR] pip install failed (Ray needs Python 3.10-3.12). Install conda and retry."; exit 1; }
fi
echo "  [OK] nexus_daemon installed"

# 配对：已配对则跳过，未配对则自动执行
CONFIG_PATH="$HOME/.jachin/nexus_config.json"
ALREADY_PAIRED=false
if [ -f "$CONFIG_PATH" ]; then
    if command -v jq &>/dev/null; then
        [ -n "$(jq -r '.instance_id // empty' "$CONFIG_PATH" 2>/dev/null)" ] && [ -n "$(jq -r '.access_token // empty' "$CONFIG_PATH" 2>/dev/null)" ] && ALREADY_PAIRED=true
    else
        grep -q '"instance_id"' "$CONFIG_PATH" 2>/dev/null && grep -q '"access_token"' "$CONFIG_PATH" 2>/dev/null && ALREADY_PAIRED=true
    fi
fi
if $ALREADY_PAIRED; then
    echo ""
    echo "[3/3] Pairing..."
    echo "  [SKIP] Already paired"
else
    echo ""
    echo "[3/3] Pairing (first time)..."
    export JACHIN_CONDA_ENV
    [ -f "$HOME/.jachin/conda_env" ] && JACHIN_CONDA_ENV=$(cat "$HOME/.jachin/conda_env")
    "$SCRIPT_DIR/run-pair.sh" || echo "  [WARN] Pairing incomplete, run: ./scripts/run-pair.sh later"
fi

# systemd (仅 Linux)
if $INSTALL_SYSTEMD && [ "$(uname -s)" = "Linux" ] && [ "$(id -u)" -eq 0 ]; then
    echo "[4/4] systemd 服务..."
    RUN_USER="${SUDO_USER:-$USER}"
    [ -z "$RUN_USER" ] && RUN_USER="root"
    USER_HOME=$(eval echo "~$RUN_USER")
    PYTHON_PATH="$(command -v "${JACHIN_PYTHON:-python3}" 2>/dev/null || command -v python)"
    if [ -f "$USER_HOME/.jachin/conda_env" ] && command -v conda &>/dev/null; then
        CE="$(cat "$USER_HOME/.jachin/conda_env")"
        CONDA_BASE="$(conda info --base 2>/dev/null)"
        [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/envs/$CE/bin/python" ] && PYTHON_PATH="$CONDA_BASE/envs/$CE/bin/python"
    fi
    [ -d "$PROJECT_ROOT/.venv" ] && [ -f "$PROJECT_ROOT/.venv/bin/python" ] && PYTHON_PATH="$PROJECT_ROOT/.venv/bin/python"
    cat > /etc/systemd/system/jachin-nexus.service << EOF
[Unit]
Description=Jachin Nexus Layer 2 Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_ROOT
ExecStart=$PYTHON_PATH -m core.nexus_daemon
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    echo "  [OK] systemd 服务已生成"
    echo "  sudo systemctl daemon-reload && sudo systemctl start jachin-nexus"
fi

echo ""
echo "[OK] Layer2 安装完成"
echo "  启动: ./scripts/start-layer2.sh"
echo ""
