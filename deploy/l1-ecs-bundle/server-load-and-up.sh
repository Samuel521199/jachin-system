#!/usr/bin/env bash
# 在服务器 /opt/jachin-l1 目录执行（与 compose、l1.env、镜像包同级）
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [[ ! -f jachin-l1-latest.tar.gz && ! -f jachin-l1-latest.tar ]]; then
  echo "[ERROR] 缺少 jachin-l1-latest.tar.gz 或 jachin-l1-latest.tar，请先 scp 上传" >&2
  exit 1
fi
if [[ ! -f compose.l1.runtime.yml || ! -f l1.env ]]; then
  echo "[ERROR] 缺少 compose.l1.runtime.yml 或 l1.env" >&2
  exit 1
fi

# 避免 gunzip | docker load 管道在部分 Docker/内核下触发 docker-import tmp 报错
if [[ -f jachin-l1-latest.tar.gz ]]; then
  echo "[load] 校验 gzip..." >&2
  gzip -t jachin-l1-latest.tar.gz
  TMP_TAR="/tmp/jachin-l1-import-$$.tar"
  trap 'rm -f -- "$TMP_TAR"' EXIT INT TERM
  echo "[load] 解压到 $TMP_TAR（需数倍镜像大小的 /tmp 空间）..." >&2
  gunzip -c jachin-l1-latest.tar.gz >"$TMP_TAR"
  docker load -i "$TMP_TAR"
  rm -f -- "$TMP_TAR"
  trap - EXIT INT TERM
else
  docker load -i jachin-l1-latest.tar
fi

docker compose -f compose.l1.runtime.yml --profile host up -d nexus-host
echo "[OK] nexus-host 已启动。访问: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 本机IP):3000"
