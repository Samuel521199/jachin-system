#!/bin/bash
# =============================================================================
# Cloud (平台商) - 一键安装 (Linux / macOS)
# cloud/nexus - Nexus Console
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

"$SCRIPT_DIR/check-prerequisites.sh" cloud || exit 1

echo ""
echo "=========================================="
echo "  Cloud (平台商) - 一键安装"
echo "=========================================="
echo ""

NEXUS_DIR="$PROJECT_ROOT/cloud/nexus"
[ ! -d "$NEXUS_DIR" ] && { echo "[ERROR] 未找到 cloud/nexus"; exit 1; }
command -v node &>/dev/null || { echo "[ERROR] 未找到 Node.js"; exit 1; }

(cd "$NEXUS_DIR" && npm install --silent)

# 可选：Supabase 迁移（悬赏大厅等表）
if command -v npx &>/dev/null; then
    (cd "$NEXUS_DIR" && npx supabase db push 2>/dev/null) && echo "[OK] Supabase migrations applied" || true
fi

echo "[OK] Cloud (Nexus Console) 已安装"
echo "  启动: ./scripts/start-cloud.sh"
echo ""
