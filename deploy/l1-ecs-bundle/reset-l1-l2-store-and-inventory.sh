#!/usr/bin/env bash
# 在 ECS 上执行：清空 L1 商城数据（plugins_registry + user_licenses）+ L1 容器内 zip 包 + L2 已同步的 inventory
# 数据库：与 l1.env 一致（jachin / postgres / jachin_nexus / 127.0.0.1）
# 警告：不可恢复，执行前确认已备份。
# 若执行报 env: bash\r：文件为 Windows 换行，在服务器执行 sed -i 's/\r$//' 本脚本后再运行。

set -euo pipefail

PGPASSWORD=postgres
export PGPASSWORD

L1_CONTAINER="${L1_CONTAINER:-jachin-l1-nexus-host-1}"
L2_CONTAINER="${L2_CONTAINER:-jachin-l2-l2-1}"

echo ">>> [1/4] TRUNCATE L1 plugins_registry（级联 user_licenses）"
psql -h 127.0.0.1 -U jachin -d jachin_nexus -v ON_ERROR_STOP=1 -c "TRUNCATE TABLE plugins_registry CASCADE;"

echo ">>> [2/4] 清空 L1 容器内 /app/public/packages/*.zip"
docker exec "$L1_CONTAINER" sh -c 'rm -f /app/public/packages/*.zip /app/public/packages/*.part.* 2>/dev/null || true'

echo ">>> [3/4] 清空 L2 inventory + 隐藏列表缓存"
docker exec "$L2_CONTAINER" sh -c '
  rm -rf /root/.jachin/inventory/skills/* /root/.jachin/inventory/l3_mcps/* /root/.jachin/inventory/mcps/* 2>/dev/null || true
  rm -f /root/.jachin/hidden_inventory.json 2>/dev/null || true
  find /root/.jachin/inventory -mindepth 1 -maxdepth 1 -type d -empty -delete 2>/dev/null || true
'

echo ">>> [4/4] 建议重启 L2 使内存态一致（可选）"
echo "    cd /opt/jachin-l2 && docker compose -f compose.l2.runtime.yml restart l2"

echo "完成。下一步：加载新 L1 镜像、up 容器、重新 publish zip、补 user_licenses、L2 trigger-sync。"
