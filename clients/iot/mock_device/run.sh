#!/bin/bash
# Mock IoT Device - Linux/macOS 启动脚本
# 使用 Dapr Run 启动模拟设备

echo "============================================================"
echo "Mock IoT Device - Capability Discovery Test"
echo "============================================================"
echo ""

# 切换到项目根目录
cd "$(dirname "$0")/../../.."

# 检查 Dapr 是否安装
if ! command -v dapr &> /dev/null; then
    echo "❌ Dapr CLI not found. Please install Dapr first."
    echo "   Visit: https://docs.dapr.io/getting-started/install-dapr-cli/"
    exit 1
fi

# 检查 Python 环境
if ! command -v python &> /dev/null; then
    echo "❌ Python not found. Please activate conda environment first."
    echo "   Run: conda activate jachin-dev"
    exit 1
fi

echo "Starting Mock IoT Device with Dapr..."
echo ""

# 使用 Dapr Run 启动
dapr run \
  --app-id mock-iot-device \
  --app-port 8001 \
  --dapr-http-port 3501 \
  --dapr-grpc-port 50002 \
  --resources-path ./dapr/components \
  --config ./dapr/config/config.yaml \
  -- python clients/iot/mock_device/main.py

if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Failed to start Mock IoT Device"
    exit 1
fi
