#!/bin/bash
# Desktop Client Setup Script - Linux/macOS

set -e

echo "=========================================="
echo "Setting up Jachin Desktop Client"
echo "=========================================="
echo ""

# Check Node.js
echo "Checking Node.js..."
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version)
    echo "[OK] Node.js: $NODE_VERSION"
else
    echo "[ERROR] Node.js not found. Please install Node.js v18+"
    exit 1
fi

# Check Rust
echo "Checking Rust..."
if command -v rustc &> /dev/null; then
    RUST_VERSION=$(rustc --version)
    echo "[OK] Rust: $RUST_VERSION"
else
    echo "[WARN] Rust not found. Installing Rust..."
    echo "  Run: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
    exit 1
fi

# Check Tauri CLI
echo "Checking Tauri CLI..."
if command -v tauri &> /dev/null; then
    TAURI_VERSION=$(tauri --version)
    echo "[OK] Tauri CLI: $TAURI_VERSION"
else
    echo "[INFO] Installing Tauri CLI..."
    npm install -g @tauri-apps/cli@next
    if [ $? -eq 0 ]; then
        echo "[OK] Tauri CLI installed"
    else
        echo "[ERROR] Failed to install Tauri CLI"
        exit 1
    fi
fi

# Install dependencies
echo ""
echo "Installing dependencies..."
npm install
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install dependencies"
    exit 1
fi
echo "[OK] Dependencies installed"

echo ""
echo "=========================================="
echo "Setup completed!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Make sure backend is running: ./scripts/start.sh"
echo "  2. Start desktop client: npm run tauri:dev"
