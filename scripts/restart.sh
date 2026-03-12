#!/bin/bash
# Restart script - 重启所有服务

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Restarting services..."
echo ""

# Stop
bash "$SCRIPT_DIR/stop.sh"
sleep 2

# Start 完整栈
bash "$SCRIPT_DIR/start-full.sh"
