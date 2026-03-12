#!/bin/bash
# L3 本地伴生 MCP - Boss 直聘收网（Stdio 模式）
# 用法: ./scripts/run_local_mcp_boss_harvester.sh

cd "$(dirname "$0")/.."
echo "L3 Local MCP: boss_harvester (stdio)"
echo "  Data volume: ~/.jachin/client_volumes/"
python -m l3_client.local_mcps.boss_harvester.server
