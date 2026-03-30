#!/usr/bin/env bash
# =============================================================================
# Jachin Nexus (Layer 1) — Linux 生产启动（须放在 standalone 包根目录，与 server.js 同级）
# 原则：先打印阶段日志，再检查路径；不通过 shell source 密钥文件，由 Next 加载 .env*。
# 环境：PORT（默认 3000）、HOSTNAME（默认 0.0.0.0）、NODE_ENV（默认 production）
# =============================================================================
set -euo pipefail

l1_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
L1_LOG_DIR="${L1_LOG_DIR:-$ROOT/logs}"
mkdir -p "$L1_LOG_DIR"
BOOT_LOG="$L1_LOG_DIR/l1-boot.log"
RUN_LOG="$L1_LOG_DIR/l1-$(date -u +%Y%m%d).log"

l1_line() {
  local line="[$(l1_ts)] [L1] $*"
  printf '%s\n' "$line"
  printf '%s\n' "$line" >>"$BOOT_LOG"
}

# ---------- 阶段 0：尚未读取任何业务文件 ----------
l1_line "========== Jachin Nexus (Layer 1) 生产启动 =========="
l1_line "standalone 根目录: $ROOT"
l1_line "PID=$$ USER=${USER:-unknown}"

# 优先使用包内 Node（官方 linux-x64 便携运行时，类 Windows 绿色版）
NODE_EXEC=""
if [[ -x "$ROOT/runtime/node/bin/node" ]]; then
  NODE_EXEC="$ROOT/runtime/node/bin/node"
  l1_line "使用包内 Node（便携）: $NODE_EXEC ($("$NODE_EXEC" -v 2>/dev/null || echo '?'))"
elif command -v node >/dev/null 2>&1; then
  NODE_EXEC="$(command -v node)"
  l1_line "使用系统 PATH 中的 Node: $NODE_EXEC ($(node -v 2>/dev/null || echo '?'))"
else
  l1_line "FATAL: 未找到 Node。请使用完整发行包（含 runtime/node/），或在服务器安装 Node 20+"
  exit 1
fi

# ---------- 阶段 1：先打印再检查每个 env 文件 ----------
l1_line "---------- 环境文件探测（存在性 only；内容由 Next 启动时加载）----------"
for f in ".env.production.local" ".env.production" ".env.local" ".env"; do
  p="$ROOT/$f"
  l1_line "即将检查: $p"
  if [[ -f "$p" ]]; then
    l1_line "结果: 存在"
  else
    l1_line "结果: 不存在"
  fi
done

# ---------- 阶段 2：server.js ----------
l1_line "---------- 运行时入口 ----------"
l1_line "即将检查: $ROOT/server.js"
if [[ ! -f "$ROOT/server.js" ]]; then
  l1_line "FATAL: 缺少 server.js（需 next build + output standalone 且完整拷贝产物）"
  exit 1
fi
l1_line "结果: server.js 存在"

# ---------- 阶段 3：静态资源 ----------
l1_line "即将检查目录: $ROOT/.next/static"
if [[ ! -d "$ROOT/.next/static" ]]; then
  l1_line "WARN: 缺少 .next/static，请从构建机复制 .next/static 到此处"
else
  l1_line "结果: .next/static 存在"
fi

export NODE_ENV="${NODE_ENV:-production}"
export PORT="${PORT:-3000}"
export HOSTNAME="${HOSTNAME:-0.0.0.0}"

l1_line "---------- 启动 Node（运行期日志 tee -> $RUN_LOG）----------"
l1_line "NODE_ENV=$NODE_ENV PORT=$PORT HOSTNAME=$HOSTNAME"

cd "$ROOT"
set +e
"$NODE_EXEC" "$ROOT/server.js" 2>&1 | tee -a "$RUN_LOG"
rc=${PIPESTATUS[0]}
set -e
l1_line "进程结束 exit=$rc"
exit "$rc"
