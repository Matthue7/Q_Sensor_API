#!/usr/bin/env bash
# Q-Sensor API Startup Script for Raspberry Pi
#
# This script starts the Q-Sensor API server with proper configuration
# for remote access on the Raspberry Pi.
#
# Usage:
#   ./pi_runbooks/01_start_api.sh
#
# Or with custom settings:
#   API_HOST=192.168.1.100 API_PORT=8080 ./pi_runbooks/01_start_api.sh

set -euo pipefail

# Change to project root
cd "$(dirname "$0")/.."

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
else
    echo "WARNING: No .venv found. Make sure dependencies are installed."
fi

# Configuration (with sensible defaults for Pi)
export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${API_PORT:-8000}"
export SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
export SERIAL_BAUD="${SERIAL_BAUD:-9600}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Print configuration
echo "============================================"
echo "Starting Q-Sensor API..."
echo "============================================"
echo "Host: $API_HOST"
echo "Port: $API_PORT"
echo "Serial Port: $SERIAL_PORT"
echo "Serial Baud: $SERIAL_BAUD"
echo "CORS Origins: $CORS_ORIGINS"
echo "Log Level: $LOG_LEVEL"
echo "============================================"
echo ""
echo "API will be available at:"
echo "  - Local:   http://localhost:$API_PORT"
echo "  - Network: http://<pi-ip>:$API_PORT"
echo "  - Docs:    http://<pi-ip>:$API_PORT/docs"
echo ""
echo "Press Ctrl+C to stop"
echo "============================================"
echo ""

# Start uvicorn
python -m uvicorn api.main:app \
    --host "${API_HOST}" \
    --port "${API_PORT}" \
    --reload \
    --log-level "${LOG_LEVEL,,}"
