#!/bin/bash
set -e

echo ""
echo "========================================"
echo "  Jachin L1 + L2 一键部署"
echo "========================================"
echo ""

# 检测 Docker
if ! command -v docker &>/dev/null; then
    echo "[错误] 未检测到 Docker，请先安装: https://docs.docker.com/engine/install/"
    exit 1
fi

# 检测 Docker Compose
if docker compose version &>/dev/null; then
    COMPOSE_CMD="docker compose"
elif docker-compose --version &>/dev/null; then
    COMPOSE_CMD="docker-compose"
else
    echo "[错误] 未检测到 Docker Compose"
    exit 1
fi

# 切换到项目根目录
cd "$(dirname "$0")/.."

echo "[1/2] 构建并启动 L1 + L2..."
$COMPOSE_CMD -f docker-compose.deploy-l1-l2.yml up -d --build

echo ""
echo "[2/2] 等待服务就绪..."
sleep 15

echo ""
echo "========================================"
echo "  部署完成"
echo "========================================"
echo ""
echo "  L1 平台 (Nexus):  http://localhost:3000"
echo "  L2 控制面 API:    http://localhost:18888"
echo ""
echo "  停止服务: $COMPOSE_CMD -f docker-compose.deploy-l1-l2.yml down"
echo ""
