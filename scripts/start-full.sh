#!/bin/bash
# Start script - 启动所有服务

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "Starting Jachin-System"
echo "=========================================="
echo ""

# Activate Conda environment automatically
echo "Activating Conda environment..."
if ! conda env list | grep -q "jachin-dev"; then
    echo "[ERROR] Conda environment 'jachin-dev' not found"
    echo "  Run: ./scripts/setup.sh"
    exit 1
fi

# Initialize conda for bash
eval "$(conda shell.bash hook)"
conda activate jachin-dev

echo "[OK] Activated jachin-dev environment"

# Check dependencies
echo "Checking dependencies..."
if ! python -c "import fastapi" 2>/dev/null; then
    echo "[ERROR] Dependencies not installed"
    echo "  Run: ./scripts/setup.sh"
    echo "  Or: pip install -r backend/requirements.txt"
    exit 1
fi
echo "[OK] Dependencies found"

# Start infrastructure
echo "Starting infrastructure..."
docker-compose -f docker-compose.dev.yml up -d
sleep 3

# Start backend
echo ""
echo "Starting backend..."
echo "  App: jachin-brain"
echo "  Port: 8000"
echo "  Dapr: 3500 (HTTP), 50001 (gRPC)"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Execute in jachin-dev environment (already activated above)
# Keep working directory at project root for Dapr components (secrets path is relative)
# Use PYTHONPATH to ensure Python can find backend modules
# Set PYTHONPATH to include both project root (for uvicorn backend.main:app) and backend directory (for from api.chat import)
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/backend"

# Note: Use uvicorn to start FastAPI app from project root
# Filter scheduler errors (known Dapr 1.16.5 limitation)
echo "[INFO] Filtering scheduler connection errors (harmless, known Dapr limitation)"
echo ""

SCHEDULER_ERROR_LAST_SHOWN=0
dapr run \
    --app-id jachin-brain \
    --app-port 8000 \
    --dapr-http-port 3500 \
    --dapr-grpc-port 50001 \
    --resources-path "$PROJECT_ROOT/dapr/components" \
    --config "$PROJECT_ROOT/dapr/config/config.yaml" \
    --log-level error \
    -- python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 2>&1 | \
    while IFS= read -r line; do
        # Filter scheduler connection errors
        if echo "$line" | grep -q "Failed to connect to scheduler host\|scheduler.watchhosts"; then
            # Show warning only once per minute
            CURRENT_TIME=$(date +%s)
            if [ $((CURRENT_TIME - SCHEDULER_ERROR_LAST_SHOWN)) -ge 60 ]; then
                echo "[WARN] Scheduler connection retrying (harmless, known Dapr 1.16.5 limitation)"
                SCHEDULER_ERROR_LAST_SHOWN=$CURRENT_TIME
            fi
        else
            # Show all other output
            echo "$line"
        fi
    done
