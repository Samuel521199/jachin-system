#!/bin/bash
set -e
cd "$(dirname "$0")"
TarFile="jachin-l2-images.tar"
ComposeFile="docker-compose.yml"
[ ! -f "$TarFile" ] && echo "[Error] $TarFile not found" && exit 1
[ ! -x "$(command -v docker)" ] && echo "[Error] Docker not found" && exit 1
COMPOSE_CMD="docker compose"; docker compose version &>/dev/null || COMPOSE_CMD="docker-compose"
echo "[1/2] Loading images..."
docker load -i "$TarFile"
echo "[2/2] Starting services..."
$COMPOSE_CMD -f "$ComposeFile" up -d
echo ""
echo "L2: http://localhost:${L2_PORT:-18888}"
