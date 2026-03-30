#!/usr/bin/env bash
# 在服务器 /opt/jachin-l2 执行
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [[ ! -f compose.l2.runtime.yml || ! -f l2.env ]]; then
  echo "[ERROR] 缺少 compose.l2.runtime.yml 或 l2.env" >&2
  exit 1
fi

echo "[l2] pull redis (需可访问 docker.io 或已配 mirror)..."
docker compose -f compose.l2.runtime.yml pull redis

if docker image inspect jachin-l2:latest >/dev/null 2>&1; then
  echo "[l2] 已存在镜像 jachin-l2:latest"
else
  echo "[ERROR] 未找到 jachin-l2:latest，请先 docker load -i jachin-l2-latest.tar" >&2
  exit 1
fi

docker compose -f compose.l2.runtime.yml up -d
_priv="$(hostname -I 2>/dev/null | awk '{print $1}' || echo ?)"
echo "[OK] L2 已启动（监听宿主机 0.0.0.0:18888）"
echo "  下方常为 VPC 内网 IP，同 VPC 内可用: http://${_priv}:18888"
echo "  公网请用 ECS 控制台里的公网 IP 或 EIP: http://<公网IP>:18888（安全组放行 TCP 18888）"
