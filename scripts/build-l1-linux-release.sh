#!/usr/bin/env bash
# =============================================================================
# 将 Layer 1（cloud/nexus）打成 Linux x86_64 上可直接运行的便携包（目录 + tar.gz）
# — 内含 Next standalone + 官方 Node linux-x64 运行时（runtime/node），服务器无需 Docker、无需 apt 安装 Node。
# 构建环境：Linux / WSL / macOS，或 Windows 上仅用 Docker 当「linux 构建机」（见 build-l1-linux-via-docker.ps1）。
# 产物：dist/jachin-l1-linux-amd64-v<version>/ 与 dist/jachin-l1-linux-amd64-v<version>.tar.gz
# 跳过内置 Node（减小体积、改用系统 node）：JACHIN_L1_SKIP_BUNDLE_NODE=1 ./scripts/build-l1-linux-release.sh
# 指定内置 Node 版本：NODE_RUNTIME_VERSION=20.20.2（须与 https://nodejs.org/dist 一致）
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NEXUS="$ROOT/cloud/nexus"
START_SH="$ROOT/scripts/packaging/l1-linux/start.sh"
DOC="$ROOT/docs/L1_LINUX_CLOUD_DEPLOY.md"

command -v node >/dev/null 2>&1 || { echo "[ERROR] 需要 Node.js"; exit 1; }
command -v npm >/dev/null 2>&1 || { echo "[ERROR] 需要 npm"; exit 1; }

[[ -d "$NEXUS" ]] || { echo "[ERROR] 未找到 $NEXUS"; exit 1; }
[[ -f "$START_SH" ]] || { echo "[ERROR] 未找到 $START_SH"; exit 1; }

VERSION="$(node -p "require('$NEXUS/package.json').version" 2>/dev/null || echo 0.1.0)"
OUT_NAME="jachin-l1-linux-amd64-v${VERSION}"
DIST="$ROOT/dist"
OUT_DIR="$DIST/$OUT_NAME"
STANDALONE="$NEXUS/.next/standalone"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 版本=$VERSION"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 进入 $NEXUS 执行 production build ..."

cd "$NEXUS"
export NODE_ENV=production
npm ci
npm run build

[[ -d "$STANDALONE" ]] || { echo "[ERROR] 未生成 $STANDALONE — 请确认 cloud/nexus/next.config.mjs 含 output: 'standalone'"; exit 1; }

# Next 可能将 server.js 放在 standalone 子目录（视项目结构而定）
SERVER_JS=""
if [[ -f "$STANDALONE/server.js" ]]; then
  COPY_SRC="$STANDALONE"
elif SERVER_JS="$(find "$STANDALONE" -name server.js -type f 2>/dev/null | head -1)" && [[ -n "$SERVER_JS" ]]; then
  COPY_SRC="$(cd "$(dirname "$SERVER_JS")" && pwd)"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 使用嵌套 standalone: $COPY_SRC"
else
  echo "[ERROR] 在 $STANDALONE 下未找到 server.js"
  find "$STANDALONE" -maxdepth 4 -type f 2>/dev/null | head -40 || true
  exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 复制 standalone -> $OUT_DIR"
cp -a "$COPY_SRC/." "$OUT_DIR/"

mkdir -p "$OUT_DIR/.next"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 复制 .next/static"
cp -a "$NEXUS/.next/static" "$OUT_DIR/.next/static"

if [[ -d "$NEXUS/public" ]] && [[ -n "$(ls -A "$NEXUS/public" 2>/dev/null)" ]]; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 复制 public/"
  cp -a "$NEXUS/public" "$OUT_DIR/public"
fi

if [[ -d "$NEXUS/drizzle" ]]; then
  mkdir -p "$OUT_DIR/drizzle"
  cp -a "$NEXUS/drizzle/." "$OUT_DIR/drizzle/"
fi

cp -a "$START_SH" "$OUT_DIR/start.sh"
chmod +x "$OUT_DIR/start.sh"

README_PORTABLE="$ROOT/scripts/packaging/l1-linux/README-PORTABLE.txt"
if [[ -f "$README_PORTABLE" ]]; then
  cp -a "$README_PORTABLE" "$OUT_DIR/README-PORTABLE.txt"
fi

# 打入官方 Node.js Linux x64 二进制（glibc），与「Windows 便携 exe + 目录」同理
if [[ "${JACHIN_L1_SKIP_BUNDLE_NODE:-}" != "1" ]]; then
  NODE_RUNTIME_VERSION="${NODE_RUNTIME_VERSION:-20.20.2}"
  NURL="https://nodejs.org/dist/v${NODE_RUNTIME_VERSION}/node-v${NODE_RUNTIME_VERSION}-linux-x64.tar.xz"
  TMP_N="${TMPDIR:-/tmp}/jachin-node-$$.tar.xz"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 下载内置 Node v${NODE_RUNTIME_VERSION} linux-x64: $NURL"
  command -v curl >/dev/null 2>&1 || { echo "[ERROR] 内置 Node 需要 curl"; exit 1; }
  curl -fsSL "$NURL" -o "$TMP_N" || { echo "[ERROR] 下载 Node 失败，可检查版本号或网络；或设置 JACHIN_L1_SKIP_BUNDLE_NODE=1"; rm -f "$TMP_N"; exit 1; }
  mkdir -p "$OUT_DIR/runtime"
  tar -xJf "$TMP_N" -C "$OUT_DIR/runtime"
  rm -f "$TMP_N"
  if [[ -d "$OUT_DIR/runtime/node-v${NODE_RUNTIME_VERSION}-linux-x64" ]]; then
    mv "$OUT_DIR/runtime/node-v${NODE_RUNTIME_VERSION}-linux-x64" "$OUT_DIR/runtime/node"
  else
    echo "[ERROR] 解压后未找到 node-v${NODE_RUNTIME_VERSION}-linux-x64 目录"
    exit 1
  fi
  chmod +x "$OUT_DIR/runtime/node/bin/node" 2>/dev/null || true
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 已嵌入 runtime/node -> $OUT_DIR/runtime/node"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 已跳过内置 Node（JACHIN_L1_SKIP_BUNDLE_NODE=1），目标机需自行安装 Node 20+"
fi

if [[ -f "$DOC" ]]; then
  cp -a "$DOC" "$OUT_DIR/DEPLOY.md"
fi

mkdir -p "$DIST"
TAR="$DIST/${OUT_NAME}.tar.gz"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [L1-build] 打包 $TAR"
tar -czf "$TAR" -C "$DIST" "$OUT_NAME"

echo ""
echo "[OK] 目录: $OUT_DIR"
echo "[OK] 压缩包: $TAR"
echo "  上传到服务器后:"
echo "    tar xzf $(basename "$TAR") && cd $OUT_NAME"
echo "    复制 .env.production.local（DATABASE_URL 等）到当前目录"
echo "    ./start.sh"
