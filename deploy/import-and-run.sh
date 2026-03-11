#!/bin/bash
set -e

echo ""
echo "========================================"
echo "  L1+L2 无代码部署（目标机器）"
echo "========================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/../docker-compose.deploy-l1-l2-images.yml"
TAR_FILE="$SCRIPT_DIR/../jachin-l1-l2-images.tar"

if [ ! -f "$TAR_FILE" ]; then
    TAR_FILE="$(pwd)/jachin-l1-l2-images.tar"
fi
if [ ! -f "$COMPOSE_FILE" ]; then
    COMPOSE_FILE="$(pwd)/docker-compose.deploy-l1-l2-images.yml"
fi

if [ ! -f "$TAR_FILE" ]; then
    echo "[错误] 未找到 jachin-l1-l2-images.tar"
    echo "请将导出包与脚本放在同一目录"
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo "[错误] 未安装 Docker"
    exit 1
fi

COMPOSE_CMD="docker compose"
docker compose version &>/dev/null || COMPOSE_CMD="docker-compose"

echo "[1/2] 导入镜像..."
docker load -i "$TAR_FILE"

echo ""
echo "[2/2] 启动服务..."
cd "$(dirname "$COMPOSE_FILE")"
$COMPOSE_CMD -f "$(basename "$COMPOSE_FILE")" up -d

echo ""
echo "========================================"
echo "  部署完成"
echo "========================================"
echo "  L1: http://localhost:3000"
echo "  L2: http://localhost:18888"
echo ""
