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

# 已安装则跳过（node_modules/next 存在表示依赖已就绪）
if [ -d "$NEXUS_DIR/node_modules/next" ]; then
    echo "[OK] cloud/nexus 依赖已安装，跳过 npm install"
else
    (cd "$NEXUS_DIR" && npm install --silent)
fi

# Drizzle 迁移（PostgreSQL，需 DATABASE_URL）
if [ -d "$NEXUS_DIR/drizzle" ]; then
    echo "> Drizzle 迁移..."
    (cd "$NEXUS_DIR" && npm run db:migrate) && echo "[OK] Drizzle migrations applied" || echo "[SKIP] DATABASE_URL 未配置或迁移失败，跳过"
fi

echo "[OK] Cloud (Nexus Console) 已安装"
echo "  启动: ./scripts/start-cloud.sh"
echo ""
