#!/bin/bash
# Stop script - 停止所有服务

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Stopping all services..."

# Stop Docker services and remove orphan containers
docker-compose -f docker-compose.dev.yml down --remove-orphans 2>/dev/null || true
docker-compose down --remove-orphans 2>/dev/null || true

# Stop backend process (if running on port 8000)
if command -v lsof &> /dev/null; then
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    lsof -ti:3500 | xargs kill -9 2>/dev/null || true
fi

echo "[OK] All services stopped"
