#!/usr/bin/env bash
# Bundle Python venv + 官方 MCP PyPI wheels 到便携 L3 输出目录（macOS）
# 对应 Windows 的 bundle_l3_mcp_runtime.ps1
#
# 布局: <OutDir>/runtime/python-venv/ — 与 core/mcp_embedded_runtime.py 路径约定一致
#
# 用法（从项目根执行）:
#   bash scripts/bundle_l3_mcp_runtime_mac.sh --out-dir dist_jachin_desktop
#   bash scripts/bundle_l3_mcp_runtime_mac.sh --out-dir dist_jachin_desktop --force
#
# 前置条件: Python 3.10+（优先 Homebrew: brew install python@3.12）

set -euo pipefail

OUT_DIR=""
FORCE=0
ROOT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out-dir)  OUT_DIR="$2";  shift 2 ;;
        --force)    FORCE=1;       shift   ;;
        --root)     ROOT="$2";     shift 2 ;;
        *)
            echo "Unknown arg: $1  (支持: --out-dir <dir> --root <dir> --force)" >&2
            exit 1
            ;;
    esac
done

if [ -z "$OUT_DIR" ]; then
    echo "错误: --out-dir 必须指定" >&2
    exit 1
fi

if [ -z "$ROOT" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

REQ_FILE="$ROOT/tools/mcp-official/requirements-official-mcp.txt"
if [ ! -f "$REQ_FILE" ]; then
    echo "[ERR] requirements not found: $REQ_FILE" >&2
    exit 1
fi

VENV_DIR="$OUT_DIR/runtime/python-venv"
MARKER="$VENV_DIR/.jachin_mcp_runtime_ok"
PYTHON_BIN="$VENV_DIR/bin/python"

# 已存在且非强制时跳过
if [ -f "$MARKER" ] && [ -f "$PYTHON_BIN" ] && [ "$FORCE" -eq 0 ]; then
    echo "[MCP Runtime] Already bundled (use --force to refresh): $VENV_DIR"
    exit 0
fi

if [ "$FORCE" -eq 1 ] && [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
    echo "[MCP Runtime] Removed existing venv (--force)"
fi

mkdir -p "$(dirname "$VENV_DIR")"

# ---------- 寻找 Python 3.10+ ----------
SYSTEM_PYTHON=""
for candidate in \
    python3.12 \
    python3.11 \
    python3.10 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.10 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    /usr/local/bin/python3.10 \
    /usr/local/bin/python3 \
    python3 \
    python
do
    if command -v "$candidate" &>/dev/null; then
        PY_VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        PY_MAJ="${PY_VER%%.*}"
        PY_MIN="${PY_VER#*.}"
        if [ "${PY_MAJ:-0}" -ge 3 ] && [ "${PY_MIN:-0}" -ge 10 ]; then
            SYSTEM_PYTHON="$(command -v "$candidate")"
            echo "[MCP Runtime] Found Python $PY_VER at $SYSTEM_PYTHON"
            break
        fi
    fi
done

if [ -z "$SYSTEM_PYTHON" ]; then
    echo "[ERR] Python 3.10+ not found. Install via:" >&2
    echo "  brew install python@3.12" >&2
    echo "  または pyenv install 3.12.9 && pyenv global 3.12.9" >&2
    exit 1
fi

# ---------- 创建 venv ----------
echo "[MCP Runtime] Creating venv: $VENV_DIR"
"$SYSTEM_PYTHON" -m venv "$VENV_DIR"

# ---------- pip install ----------
echo "[MCP Runtime] Upgrading pip..."
"$PYTHON_BIN" -m pip install --upgrade pip --quiet

echo "[MCP Runtime] pip install -r $(basename "$REQ_FILE")..."
"$PYTHON_BIN" -m pip install -r "$REQ_FILE" --quiet

echo "[MCP Runtime] pip install local vocabulary model runtime deps..."
"$PYTHON_BIN" -m pip install ctranslate2==4.8.0 sentencepiece==0.2.0 --only-binary=:all: --quiet

echo "[MCP Runtime] pip install optional GGUF example runtime (llama-cpp-python)..."
if ! "$PYTHON_BIN" -m pip install 'llama-cpp-python>=0.3.9,<0.4' --only-binary=:all: --quiet; then
  echo "[MCP Runtime][WARN] llama-cpp-python binary wheel unavailable. English examples will use dictionary/cache and remote fallback until installed."
fi

# ---------- 复制 runtime 文档/manifest ----------
MCP_RT_DIR="$ROOT/tools/mcp-runtime"
RUNTIME_OUT="$OUT_DIR/runtime"
mkdir -p "$RUNTIME_OUT"
[ -f "$MCP_RT_DIR/README.txt" ]               && cp "$MCP_RT_DIR/README.txt"               "$RUNTIME_OUT/README_MCP_RUNTIME.txt"
[ -f "$MCP_RT_DIR/manifest.example.json" ]    && cp "$MCP_RT_DIR/manifest.example.json"    "$RUNTIME_OUT/manifest.example.json"

# ---------- 写入 marker ----------
cat > "$MARKER" <<EOF
schema_version=1
python_venv=true
system_python=$SYSTEM_PYTHON
python_version=$PY_VER
bundled_at_utc=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
packages=see tools/mcp-official/requirements-official-mcp.txt
EOF

echo "[MCP Runtime] Done: $PYTHON_BIN"
echo "  Test: \"$PYTHON_BIN\" -c \"import mcp_server_fetch\""
