#!/usr/bin/env bash
# 清除所有 macOS Build 产物
# 用法（从项目根目录或脚本目录执行）:
#   bash scripts/build_clean_mac.sh
#   bash scripts/build_clean_mac.sh /path/to/project-root

set -euo pipefail

if [ -n "${1:-}" ]; then
    ROOT="$1"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

if [ ! -d "$ROOT/l3_node" ]; then
    echo "[WARN] l3_node not found under $ROOT, aborting clean" >&2
    exit 1
fi

cd "$ROOT"
cleaned=()

# PyInstaller 临时目录
for d in dist_l3 build_l3; do
    if [ -d "$ROOT/$d" ]; then
        rm -rf "$ROOT/$d"
        cleaned+=("$d")
    fi
done

# PyInstaller spec 文件
if [ -f "$ROOT/l3_node.spec" ]; then
    rm -f "$ROOT/l3_node.spec"
    cleaned+=("l3_node.spec")
fi

# 前端构建产物
if [ -d "$ROOT/clients/desktop/dist" ]; then
    rm -rf "$ROOT/clients/desktop/dist"
    cleaned+=("clients/desktop/dist")
fi

# Tauri target（含 Rust 编译缓存；完整清理）
if [ -d "$ROOT/clients/desktop/src-tauri/target" ]; then
    rm -rf "$ROOT/clients/desktop/src-tauri/target"
    cleaned+=("clients/desktop/src-tauri/target")
fi

# 便携版输出目录
if [ -d "$ROOT/dist_jachin_desktop" ]; then
    rm -rf "$ROOT/dist_jachin_desktop"
    cleaned+=("dist_jachin_desktop")
fi

# macOS 侧车二进制（Tauri bin 目录）
BIN_DIR="$ROOT/clients/desktop/src-tauri/bin"
if [ -d "$BIN_DIR" ]; then
    for f in "$BIN_DIR"/l3_node-*-apple-darwin; do
        if [ -f "$f" ]; then
            rm -f "$f"
            cleaned+=("bin/$(basename "$f")")
        fi
    done
fi

if [ "${#cleaned[@]}" -gt 0 ]; then
    echo "[build_clean_mac] 已清除: ${cleaned[*]}"
else
    echo "[build_clean_mac] 无待清除的 Build 产物"
fi
