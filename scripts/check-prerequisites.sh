#!/bin/bash
# =============================================================================
# 前置依赖检查 - 一键安装前需满足的条件
# 用法: ./scripts/check-prerequisites.sh [cloud|layer2|layer3]
# =============================================================================

LAYER="${1:-all}"
MISSING=()
WARNINGS=()

cmd_exists() { command -v "$1" &>/dev/null; }

[ "$LAYER" = "all" ] || [ "$LAYER" = "cloud" ] && {
    cmd_exists node || MISSING+=("Node.js (https://nodejs.org/ 或 apt install nodejs / brew install node)")
    cmd_exists npm || MISSING+=("npm")
}

[ "$LAYER" = "all" ] || [ "$LAYER" = "layer2" ] && {
    cmd_exists python3 || cmd_exists python || MISSING+=("Python 3.10+ (apt install python3 / brew install python)")
    cmd_exists docker || [ "$LAYER" = "all" ] || WARNINGS+=("Docker (Qdrant 需 Docker)")
}

[ "$LAYER" = "all" ] || [ "$LAYER" = "layer3" ] && {
    cmd_exists node || MISSING+=("Node.js")
    cmd_exists tauri || [ "$LAYER" = "all" ] || WARNINGS+=("Rust + Tauri CLI (完整桌面端)")
}

echo ""
echo "=========================================="
echo "  前置依赖检查 [$LAYER]"
echo "=========================================="

if [ ${#MISSING[@]} -gt 0 ]; then
    echo ""
    echo "[缺失] 以下软件需先安装:"
    printf '  - %s\n' "${MISSING[@]}"
    echo ""
    echo "Linux: sudo apt install nodejs python3 docker.io"
    echo "macOS: brew install node python docker"
    echo ""
    exit 1
fi

[ ${#WARNINGS[@]} -gt 0 ] && {
    echo ""
    echo "[可选] 以下未安装，部分功能受限:"
    printf '  - %s\n' "${WARNINGS[@]}"
}

echo ""
echo "[OK] 前置依赖满足"
echo ""
exit 0
