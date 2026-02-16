#!/bin/bash
# Test script - 测试 API

echo "Testing API..."
echo ""

# Health check
echo "1. Health check:"
if curl -s http://localhost:8000/health > /dev/null; then
    echo "   [OK] Service is running"
else
    echo "   [ERROR] Service not available"
    exit 1
fi

# Chat test
echo ""
echo "2. Chat API test:"
response=$(curl -s -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "你好"}')

if [ $? -eq 0 ]; then
    echo "   [OK] Response received"
    echo "$response" | python -m json.tool 2>/dev/null || echo "$response"
else
    echo "   [ERROR] Chat API failed"
fi

echo ""
echo "Test complete!"
