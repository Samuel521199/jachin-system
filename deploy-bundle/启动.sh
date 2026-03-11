#!/bin/bash
set -e

echo ""
echo "========================================"
echo "  Jachin L1 + L2 部署"
echo "========================================"
echo ""

cd "$(dirname "$0")"
COMPOSE_FILE="docker-compose.deploy-l1-l2-images.yml"
TAR_FILE="jachin-l1-l2-images.tar"

if [ ! -f "$TAR_FILE" ]; then
    echo "[错误] 未找到 $TAR_FILE"
    echo "请先在有代码的机器上运行 deploy/生成部署包.sh 生成完整部署包"
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
$COMPOSE_CMD -f "$COMPOSE_FILE" up -d

echo ""
echo "========================================"
echo "  部署完成"
echo "========================================"
echo "  L1 平台: http://localhost:3000"
echo "  L2 API:  http://localhost:18888"
echo ""
