#!/bin/bash
# =============================================================================
# Layer3 (用户) - 一键安装 (Linux / macOS)
# clients/desktop - Jachin Terminal (Tauri + React)
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

"$SCRIPT_DIR/check-prerequisites.sh" layer3 || exit 1

echo ""
echo "=========================================="
echo "  Layer3 (用户) - 一键安装"
echo "=========================================="
echo ""

DESKTOP_DIR="$PROJECT_ROOT/clients/desktop"
[ ! -d "$DESKTOP_DIR" ] && { echo "[ERROR] 未找到 clients/desktop"; exit 1; }
command -v node &>/dev/null || { echo "[ERROR] 未找到 Node.js"; exit 1; }

(cd "$DESKTOP_DIR" && npm install --silent)

echo "[OK] Layer3 (Desktop) 已安装"
echo "  启动: ./scripts/start-layer3.sh"
echo "  完整构建需 Rust + Tauri CLI，见 clients/desktop/scripts/setup.sh"
echo ""
