#!/usr/bin/env bash
# Desktop Sprite - 使用 Dapr 启动脚本 (macOS/Linux)
# 按照 v2.0 架构，桌面客户端需要运行 Dapr sidecar 来接收命令

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "============================================================"
echo "Jachin Desktop Sprite - With Dapr"
echo "============================================================"
echo ""

# 检查 Dapr
if ! command -v dapr &> /dev/null; then
    echo "[ERROR] Dapr CLI not found. Please install Dapr first."
    echo "   Visit: https://docs.dapr.io/getting-started/install-dapr-cli/"
    exit 1
fi

# 检查 npm
if ! command -v npm &> /dev/null; then
    echo "[ERROR] npm not found. Please install Node.js first."
    exit 1
fi

APP_ID="desktop-client"
APP_PORT=8002
DAPR_HTTP_PORT=3502
DAPR_GRPC_PORT=50003
COMPONENTS_PATH="$PROJECT_ROOT/dapr/components"
CONFIG_PATH="$PROJECT_ROOT/dapr/config/config.yaml"

echo "Device ID: desktop-$(hostname)"
echo ""
echo "Starting Desktop Sprite with Dapr..."
echo "  App ID: $APP_ID"
echo "  App Port: $APP_PORT"
echo "  Dapr HTTP Port: $DAPR_HTTP_PORT"
echo ""

# 检查配置
if [ ! -d "$COMPONENTS_PATH" ]; then
    echo "[ERROR] Dapr components not found: $COMPONENTS_PATH"
    exit 1
fi

if [ ! -f "$CONFIG_PATH" ]; then
    echo "[ERROR] Dapr config not found: $CONFIG_PATH"
    exit 1
fi

echo "[OK] Dapr configuration found"
echo ""

cd "$SCRIPT_DIR"

dapr run \
  --app-id "$APP_ID" \
  --app-port "$APP_PORT" \
  --dapr-http-port "$DAPR_HTTP_PORT" \
  --dapr-grpc-port "$DAPR_GRPC_PORT" \
  --resources-path "$COMPONENTS_PATH" \
  --config "$CONFIG_PATH" \
  -- npm run tauri:dev
