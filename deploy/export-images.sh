#!/bin/bash
set -e

echo ""
echo "========================================"
echo "  导出 L1+L2 镜像（用于无代码部署）"
echo "========================================"
echo ""

cd "$(dirname "$0")/.."

echo "[1/3] 构建镜像..."
docker compose -f docker-compose.deploy-l1-l2.yml build

echo ""
echo "[2/3] 导出到 jachin-l1-l2-images.tar ..."
docker save -o jachin-l1-l2-images.tar \
    jachin/l1-db-init:latest \
    jachin/l1-nexus:latest \
    jachin/l2-control:latest

echo ""
echo "========================================"
echo "  完成"
echo "========================================"
echo ""
echo "请将以下文件拷贝到目标机器:"
echo "  - docker-compose.deploy-l1-l2-images.yml"
echo "  - jachin-l1-l2-images.tar"
echo "  - deploy/import-and-run.sh (或 import-and-run.bat)"
echo ""
