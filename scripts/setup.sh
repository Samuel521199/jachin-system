#!/bin/bash
# Setup script - 一键设置开发环境

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "Jachin-System Setup"
echo "=========================================="
echo ""

# Step 1: Check Conda
echo "Step 1: Checking Conda..."
if ! conda env list | grep -q "jachin-dev"; then
    echo "  Creating Conda environment..."
    conda env create -f environment.yml
    echo "[OK] Conda environment created"
else
    echo "[OK] Conda environment exists"
fi

# Step 2: Install dependencies
echo ""
echo "Step 2: Installing dependencies..."
eval "$(conda shell.bash hook)"
conda activate jachin-dev
pip install -q -r backend/requirements.txt
echo "[OK] Dependencies installed"

# Step 3: Setup Dapr
echo ""
echo "Step 3: Setting up Dapr..."
if [ ! -f "$HOME/.dapr/bin/daprd" ]; then
    echo "  Initializing Dapr..."
    dapr uninstall 2>/dev/null || true
    dapr init -s --runtime-version 1.16.5 2>&1 | grep -v "warning" || true
    echo "[OK] Dapr initialized"
else
    echo "[OK] Dapr runtime exists"
fi

# Step 4: Setup environment
echo ""
echo "Step 4: Setting up environment..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "[OK] Created .env file"
        echo "  Please edit .env and set QWEN_API_KEY"
    fi
else
    echo "[OK] .env file exists"
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next: conda activate jachin-dev"
echo "Then: ./scripts/start.sh"
echo ""
