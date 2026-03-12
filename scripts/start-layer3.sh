#!/bin/bash
# =============================================================================
# Layer3 (用户) - 一键启动 (Linux / macOS)
# clients/desktop - Jachin Terminal
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

DESKTOP_DIR="$PROJECT_ROOT/clients/desktop"
[ ! -d "$DESKTOP_DIR" ] && { echo "[ERROR] 未找到 clients/desktop"; exit 1; }
[ ! -d "$DESKTOP_DIR/node_modules" ] && { echo "[INFO] 依赖未安装..."; (cd "$DESKTOP_DIR" && npm install); }

echo ""
echo "=========================================="
echo "  Layer3 (Desktop) 启动"
echo "=========================================="
echo "  Press Ctrl+C to stop"
echo ""

cd "$DESKTOP_DIR"
if command -v tauri &>/dev/null; then
    npm run tauri:dev
else
    echo "[INFO] Tauri 未安装，使用 Vite 开发模式"
    npm run dev
fi
