#!/bin/bash
# Generate gRPC Code from protocol.proto
# 从 protocol.proto 生成 gRPC 代码

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTO_FILE="$SCRIPT_DIR/protocol.proto"

echo "Generating gRPC code from protocol.proto..."

if [ ! -f "$PROTO_FILE" ]; then
    echo "Error: protocol.proto not found at $PROTO_FILE"
    exit 1
fi

# Check if grpc_tools is installed
if ! python -m grpc_tools.protoc --version > /dev/null 2>&1; then
    echo "Error: grpc_tools not installed. Install with: pip install grpcio-tools"
    exit 1
fi

# Generate Python code
echo "Running protoc..."
python -m grpc_tools.protoc \
    -I "$SCRIPT_DIR" \
    --python_out="$SCRIPT_DIR" \
    --grpc_python_out="$SCRIPT_DIR" \
    "$PROTO_FILE"

echo "Success! Generated files:"
echo "  - protocol_pb2.py"
echo "  - protocol_pb2_grpc.py"
