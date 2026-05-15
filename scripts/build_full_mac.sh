#!/usr/bin/env bash
# Full Build Script for macOS（对应 Windows 的 build_full.ps1）
#
# 用法: bash scripts/build_full_mac.sh [选项]
# 选项:
#   --skip-tauri        仅打 L3，跳过 Tauri 桌面构建
#   --no-clean          跳过清理，增量构建
#   --force             强制重新打包 L3（忽略「二进制比源码新」跳过）
#   --skip-mcp-runtime  跳过内嵌 Python venv + 官方 MCP wheels

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_TAURI=0
NO_CLEAN=0
FORCE=0
SKIP_MCP_RUNTIME=0

for arg in "$@"; do
    case "$arg" in
        --skip-tauri)       SKIP_TAURI=1 ;;
        --no-clean)         NO_CLEAN=1 ;;
        --force)            FORCE=1 ;;
        --skip-mcp-runtime) SKIP_MCP_RUNTIME=1 ;;
        *)
            echo "未知参数: $arg  (支持: --skip-tauri --no-clean --force --skip-mcp-runtime)" >&2
            exit 1
            ;;
    esac
done

if [ ! -d "$ROOT/l3_node" ]; then
    echo "错误: 未找到 l3_node，请在项目根目录运行" >&2
    exit 1
fi

cd "$ROOT"

# ---------- 1. Clean ----------
echo ""
if [ "$NO_CLEAN" -eq 0 ]; then
    echo "[1/5] Cleaning build artifacts..."
    bash "$SCRIPT_DIR/build_clean_mac.sh" "$ROOT"
else
    echo "[1/5] Skip clean (--no-clean)"
fi

# ---------- 2. Build L3 Sidecar ----------
echo ""
echo "[2/5] Building L3 Sidecar (PyInstaller)..."
L3_ARGS=()
[ "$FORCE" -eq 1 ] && L3_ARGS+=("--force")
bash "$SCRIPT_DIR/build_l3_sidecar_mac.sh" "${L3_ARGS[@]+"${L3_ARGS[@]}"}"

# ---------- 3. Build Tauri Desktop ----------
echo ""
if [ "$SKIP_TAURI" -eq 0 ]; then
    echo "[3/5] Building Tauri Desktop..."
    pushd "$ROOT/clients/desktop" > /dev/null
    npm run tauri build
    popd > /dev/null
else
    echo "[3/5] Skip Tauri (--skip-tauri)"
fi

# ---------- 4. Assemble portable output ----------
echo ""
echo "[4/5] Assembling portable output..."

OUT_DIR="$ROOT/dist_jachin_desktop"
TAURI_RELEASE="$ROOT/clients/desktop/src-tauri/target/release"

mkdir -p "$OUT_DIR/scripts" "$OUT_DIR/bin" "$OUT_DIR/config" "$OUT_DIR/logs"

# 复制 Tauri 产物（.app bundle 或 .dmg）
if [ -d "$TAURI_RELEASE/bundle/macos" ]; then
    APP=$(find "$TAURI_RELEASE/bundle/macos" -maxdepth 1 -name "*.app" -type d | head -1)
    if [ -n "$APP" ]; then
        cp -R "$APP" "$OUT_DIR/"
        echo "  Copied .app bundle: $(basename "$APP")"
    fi
fi
if [ -d "$TAURI_RELEASE/bundle/dmg" ]; then
    DMG=$(find "$TAURI_RELEASE/bundle/dmg" -maxdepth 1 -name "*.dmg" | head -1)
    if [ -n "$DMG" ]; then
        cp "$DMG" "$OUT_DIR/"
        echo "  Copied .dmg: $(basename "$DMG")"
    fi
fi

# 复制 bin/l3_node（macOS，无 .exe 后缀）
BIN_SRC="$ROOT/clients/desktop/src-tauri/bin"
if [ -d "$BIN_SRC" ]; then
    for f in "$BIN_SRC"/l3_node-*-apple-darwin; do
        if [ -f "$f" ]; then
            cp "$f" "$OUT_DIR/bin/"
            chmod +x "$OUT_DIR/bin/$(basename "$f")"
            echo "  Copied bin/$(basename "$f")"
        fi
    done
fi

# 复制 scripts
cp "$SCRIPT_DIR/run_l3.sh" "$OUT_DIR/scripts/run_l3.sh"
chmod +x "$OUT_DIR/scripts/run_l3.sh"
echo "  Copied scripts/run_l3.sh"
# launch_chrome_debug.sh（若存在）
for candidate in \
    "$ROOT/scripts/launch_chrome_debug.sh" \
    "$ROOT/skills_repo/plugin/scripts/launch_chrome_debug.sh"
do
    if [ -f "$candidate" ]; then
        cp "$candidate" "$OUT_DIR/scripts/"
        chmod +x "$OUT_DIR/scripts/launch_chrome_debug.sh"
        echo "  Copied scripts/launch_chrome_debug.sh"
        break
    fi
done
# 用户双击启动脚本
if [ -f "$ROOT/scripts/run_l3_mac.sh" ]; then
    cp "$ROOT/scripts/run_l3_mac.sh" "$OUT_DIR/run_l3_mac.sh"
    chmod +x "$OUT_DIR/run_l3_mac.sh"
    echo "  Copied run_l3_mac.sh"
elif [ -f "$ROOT/dist_jachin_desktop/run_l3_mac.sh" ]; then
    chmod +x "$ROOT/dist_jachin_desktop/run_l3_mac.sh"
fi

# 复制 config
for f in skills_config.yaml l3_recruitment.yaml.example im_channels.yaml.example; do
    SRC="$ROOT/config/$f"
    if [ -f "$SRC" ]; then
        cp "$SRC" "$OUT_DIR/config/"
        echo "  Copied config/$f"
    fi
done
echo "  Created logs/"

# .env 复制策略（与 build_full.ps1 一致）
ENV_SRC=""
if [ -n "${JACHIN_DESKTOP_BUNDLE_ENV_FILE:-}" ] && [ -f "${JACHIN_DESKTOP_BUNDLE_ENV_FILE}" ]; then
    ENV_SRC="${JACHIN_DESKTOP_BUNDLE_ENV_FILE}"
elif [ -f "$ROOT/clients/desktop/.jachin_bundle_env_path" ]; then
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"
        [[ "$line" =~ ^#|^$ ]] && continue
        if [ -f "$line" ]; then
            ENV_SRC="$line"
            break
        fi
    done < "$ROOT/clients/desktop/.jachin_bundle_env_path"
fi
[ -z "$ENV_SRC" ] && [ -f "$ROOT/.env" ] && ENV_SRC="$ROOT/.env"

if [ -f "$ROOT/.env.example" ]; then
    cp "$ROOT/.env.example" "$OUT_DIR/.env.example"
    echo "  Copied .env.example"
fi
if [ -n "$ENV_SRC" ]; then
    cp "$ENV_SRC" "$OUT_DIR/.env"
    echo "  Copied .env <- $ENV_SRC"
elif [ -f "$ROOT/.env.example" ]; then
    cp "$ROOT/.env.example" "$OUT_DIR/.env"
    echo "  Copied .env from .env.example (no repo .env / override)"
fi

# README_DEPLOY.md
for r in "docs/README_DEPLOY.md" "README_DEPLOY.md"; do
    if [ -f "$ROOT/$r" ]; then
        cp "$ROOT/$r" "$OUT_DIR/"
        break
    fi
done

# ---------- 5. Bundle MCP runtime ----------
echo ""
if [ "$SKIP_MCP_RUNTIME" -eq 0 ]; then
    echo "[5/5] Bundling MCP runtime (Python venv + official MCP wheels)..."
    FORCE_FLAG=""
    [ "$FORCE" -eq 1 ] && FORCE_FLAG="--force"
    bash "$SCRIPT_DIR/bundle_l3_mcp_runtime_mac.sh" \
        --root "$ROOT" \
        --out-dir "$OUT_DIR" \
        $FORCE_FLAG
else
    echo "[5/5] Skip MCP runtime (--skip-mcp-runtime)"
fi

echo ""
echo "[Done] Portable output: $OUT_DIR"
if [ "$SKIP_TAURI" -eq 0 ]; then
    APP_OUT=$(find "$OUT_DIR" -maxdepth 1 -name "*.app" -type d | head -1)
    [ -n "$APP_OUT" ] && echo "  Run app: open \"$APP_OUT\""
fi
echo "  Debug L3: JACHIN_SKIP_L3_SPAWN=1 bash \"$OUT_DIR/scripts/run_l3.sh\" --ws-only"
if [ "$SKIP_MCP_RUNTIME" -eq 0 ]; then
    echo "  MCP runtime: $OUT_DIR/runtime/python-venv/bin/python"
fi
