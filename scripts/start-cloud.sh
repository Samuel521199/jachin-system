#!/bin/bash
# =============================================================================
# Cloud (Layer 1) - One-click start (Linux / macOS)
# cloud/nexus - Nexus Console @ http://localhost:3000
# =============================================================================

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

NEXUS_DIR="$PROJECT_ROOT/cloud/nexus"
[ ! -d "$NEXUS_DIR" ] && { echo "(ERROR) cloud/nexus not found. Run: ./scripts/install-cloud.sh"; exit 1; }
command -v node &>/dev/null || { echo "(ERROR) Node.js not found. Run: ./scripts/install-cloud.sh"; exit 1; }

[ ! -d "$NEXUS_DIR/node_modules/next" ] && { echo "(INFO) Installing deps (first run)..."; (cd "$NEXUS_DIR" && npm install --silent); }

# .env.local optional: copy from .env.example if missing (SKIP_AUTH for quick dev)
if [ ! -f "$NEXUS_DIR/.env.local" ] && [ -f "$NEXUS_DIR/.env.example" ]; then
    cp "$NEXUS_DIR/.env.example" "$NEXUS_DIR/.env.local"
    echo -e "\n# Auto-added for first run\nSKIP_AUTH=true" >> "$NEXUS_DIR/.env.local"
    echo "(INFO) Created .env.local with SKIP_AUTH=true"
fi

echo ""
echo "=========================================="
echo "  Cloud (Layer 1) - Nexus Console"
echo "=========================================="
echo "  http://localhost:3000"
echo "  Press Ctrl+C to stop"
echo ""

(cd "$NEXUS_DIR" && npm run dev)
