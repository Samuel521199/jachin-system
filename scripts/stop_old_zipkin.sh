#!/bin/bash
# Stop old Zipkin container script
# 停止旧的Zipkin容器脚本

set -e

echo "========================================"
echo "  Stopping Old Zipkin Container"
echo "========================================"
echo ""

# Find zipkin containers
echo "[1/3] Finding Zipkin containers..."
ZIPKIN_CONTAINERS=$(docker ps -a --filter "name=zipkin" --format "{{.Names}}")

if [ -n "$ZIPKIN_CONTAINERS" ]; then
    echo "  Found Zipkin containers:"
    echo "$ZIPKIN_CONTAINERS" | while read -r container; do
        echo "    - $container"
    done
    
    echo ""
    echo "[2/3] Stopping containers..."
    
    echo "$ZIPKIN_CONTAINERS" | while read -r container; do
        echo "  Stopping $container..."
        if docker stop "$container" 2>/dev/null; then
            echo "    [OK] Stopped $container"
        else
            echo "    [WARN] Failed to stop $container (may already be stopped)"
        fi
    done
    
    echo ""
    echo "[3/3] Removing containers..."
    
    echo "$ZIPKIN_CONTAINERS" | while read -r container; do
        echo "  Removing $container..."
        if docker rm "$container" 2>/dev/null; then
            echo "    [OK] Removed $container"
        else
            echo "    [WARN] Failed to remove $container"
        fi
    done
    
    echo ""
    echo "========================================"
    echo "  Old Zipkin containers cleaned up"
    echo "========================================"
    echo ""
    echo "You can now start services with:"
    echo "  docker-compose up -d"
    echo ""
else
    echo "  [INFO] No Zipkin containers found"
    echo ""
fi
