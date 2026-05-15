#!/usr/bin/env bash
# L3 节点 PyInstaller 打包脚本 — macOS 版本入口
#
# 用法（在项目根目录执行）:
#   bash scripts/build_l3_sidecar_mac.sh [--force]
#   --force  强制重新打包，忽略「二进制比源码新」的跳过逻辑
#
# 产出:
#   clients/desktop/src-tauri/bin/l3_node-{x86_64|aarch64}-apple-darwin
#   （与 tauri.conf.json 的 bundle.externalBin: bin/l3_node 对应）
#   dist_jachin_desktop/bin/l3_node-{target}
#
# 前置条件:
#   pip3 install pyinstaller
#   pip3 install -r core/requirements.txt
#   推荐: brew install python@3.12

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

echo "[build_l3_sidecar_mac] 当前目录: $ROOT"

# 检查 Python
if command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null && python -c "import sys; exit(0 if sys.version_info.major==3 else 1)" 2>/dev/null; then
    PYTHON_BIN="python"
else
    echo "错误: 未找到 Python 3，请安装: brew install python@3.12" >&2
    exit 1
fi

PY_VERSION=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "[build_l3_sidecar_mac] Python: $("$PYTHON_BIN" --version) ($PY_VERSION)"

# 检查 PyInstaller
if ! "$PYTHON_BIN" -c "import PyInstaller" 2>/dev/null; then
    echo "错误: 未找到 PyInstaller，请安装: pip3 install pyinstaller" >&2
    exit 1
fi

# 检查 l3_node
if [ ! -f "$ROOT/l3_node/__main__.py" ]; then
    echo "错误: l3_node/__main__.py 未找到，请确认在项目根目录执行" >&2
    exit 1
fi

# 传递所有参数给跨平台 Python 打包脚本（已内置 macOS target triple 检测）
echo "[build_l3_sidecar_mac] 调用 build_l3_sidecar.py $*"
"$PYTHON_BIN" scripts/build_l3_sidecar.py "$@"
