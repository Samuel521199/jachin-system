#!/bin/bash
# =============================================================================
# Cloud (平台商) - 一键启动 (Linux / macOS)
# cloud/nexus - Nexus Console @ http://localhost:3000
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

NEXUS_DIR="$PROJECT_ROOT/cloud/nexus"
[ ! -d "$NEXUS_DIR" ] && { echo "[ERROR] 未找到 cloud/nexus"; exit 1; }
[ ! -d "$NEXUS_DIR/node_modules" ] && { echo "[INFO] 依赖未安装..."; (cd "$NEXUS_DIR" && npm install); }

echo ""
echo "=========================================="
echo "  Cloud (Nexus Console) 启动"
echo "=========================================="
echo "  http://localhost:3000"
echo "  Press Ctrl+C to stop"
echo ""

(cd "$NEXUS_DIR" && npm run dev)
