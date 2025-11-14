#!/bin/bash
# Q-Sensor API End-to-End Test Script
# Tests the full recording chain on Pi
#
# Usage:
#   ./test_qsensor_e2e.sh [API_BASE] [SERIAL_PORT] [SERIAL_BAUD]
#
# Examples:
#   ./test_qsensor_e2e.sh
#   ./test_qsensor_e2e.sh http://localhost:9150
#   ./test_qsensor_e2e.sh http://localhost:9150 /dev/ttyAMA0 115200

set -e  # Exit on error

# Configuration (with defaults)
API_BASE="${1:-http://localhost:9150}"
SERIAL_PORT="${2:-/dev/ttyAMA0}"
SERIAL_BAUD="${3:-115200}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== Q-Sensor API End-to-End Test ==="
echo "API Base: ${API_BASE}"
echo "Serial Port: ${SERIAL_PORT}"
echo "Serial Baud: ${SERIAL_BAUD}"
echo ""

# Helper function for test steps
test_step() {
    local step_num=$1
    local description=$2
    echo -e "${YELLOW}${step_num}. ${description}${NC}"
}

success() {
    echo -e "${GREEN}✅ $1${NC}"
    echo ""
}

error() {
    echo -e "${RED}❌ $1${NC}"
    echo ""
    exit 1
}

# Test 1: Basic health
test_step "1" "Testing /health..."
HEALTH_RESPONSE=$(curl -s "${API_BASE}/health")
echo "$HEALTH_RESPONSE" | jq . 2>/dev/null || error "/health failed or returned invalid JSON"
if echo "$HEALTH_RESPONSE" | jq -e '.service' > /dev/null 2>&1; then
    success "/health works"
else
    error "/health response missing 'service' field"
fi

# Test 2: Version check
test_step "2" "Testing /version..."
VERSION_RESPONSE=$(curl -s "${API_BASE}/version")
echo "$VERSION_RESPONSE" | jq . 2>/dev/null || error "/version failed or returned invalid JSON"
if echo "$VERSION_RESPONSE" | jq -e '.api' > /dev/null 2>&1; then
    API_VERSION=$(echo "$VERSION_RESPONSE" | jq -r '.api')
    GIT_COMMIT=$(echo "$VERSION_RESPONSE" | jq -r '.git')
    echo "API Version: ${API_VERSION}, Git: ${GIT_COMMIT}"
    success "/version works"
else
    error "/version response missing 'api' field"
fi

# Test 3: Instrument health (before connection)
test_step "3" "Testing /instrument/health (before connection)..."
INST_HEALTH_RESPONSE=$(curl -s "${API_BASE}/instrument/health")
echo "$INST_HEALTH_RESPONSE" | jq . 2>/dev/null || error "/instrument/health failed or returned invalid JSON"
if echo "$INST_HEALTH_RESPONSE" | jq -e '.connected' > /dev/null 2>&1; then
    CONNECTED=$(echo "$INST_HEALTH_RESPONSE" | jq -r '.connected')
    echo "Connected: ${CONNECTED}"
    success "/instrument/health works (not connected yet, as expected)"
else
    error "/instrument/health response missing 'connected' field"
fi

# Test 4: Connect to sensor
test_step "4" "Connecting to sensor (${SERIAL_PORT} @ ${SERIAL_BAUD} baud)..."
CONNECT_RESPONSE=$(curl -s -X POST "${API_BASE}/sensor/connect?port=${SERIAL_PORT}&baud=${SERIAL_BAUD}")
echo "$CONNECT_RESPONSE" | jq . 2>/dev/null || error "/sensor/connect failed or returned invalid JSON"
if echo "$CONNECT_RESPONSE" | jq -e '.sensor_id' > /dev/null 2>&1; then
    SENSOR_ID=$(echo "$CONNECT_RESPONSE" | jq -r '.sensor_id')
    echo "Sensor ID: ${SENSOR_ID}"
    success "/sensor/connect works"
else
    error "/sensor/connect response missing 'sensor_id' field"
fi

# Wait for connection stabilization
echo "Waiting 2s for connection to stabilize..."
sleep 2

# Test 5: Start acquisition (freerun)
test_step "5" "Starting acquisition..."
START_ACQ_RESPONSE=$(curl -s -X POST "${API_BASE}/sensor/start")
echo "$START_ACQ_RESPONSE" | jq . 2>/dev/null || error "/sensor/start failed or returned invalid JSON"
if echo "$START_ACQ_RESPONSE" | jq -e '.mode' > /dev/null 2>&1; then
    ACQ_MODE=$(echo "$START_ACQ_RESPONSE" | jq -r '.mode')
    echo "Acquisition Mode: ${ACQ_MODE}"
    success "/sensor/start works"
else
    error "/sensor/start response missing 'mode' field"
fi

# Wait for buffer to fill
echo "Waiting 2s for sensor buffer to fill..."
sleep 2

# Test 6: Start recording session
test_step "6" "Starting recording session..."
SESSION_RESPONSE=$(curl -s -X POST "${API_BASE}/record/start" \
  -H 'Content-Type: application/json' \
  -d '{"mission":"AuditTest","rate_hz":500,"schema_version":1,"roll_interval_s":60}')
echo "$SESSION_RESPONSE" | jq . 2>/dev/null || error "/record/start failed or returned invalid JSON"
if echo "$SESSION_RESPONSE" | jq -e '.session_id' > /dev/null 2>&1; then
    SESSION_ID=$(echo "$SESSION_RESPONSE" | jq -r '.session_id')
    echo "Session ID: ${SESSION_ID}"
    success "/record/start works"
else
    error "/record/start response missing 'session_id' field"
fi

# Wait for some data to accumulate
echo "Recording for 5 seconds..."
sleep 5

# Test 7: Check recording status
test_step "7" "Checking recording status..."
STATUS_RESPONSE=$(curl -s "${API_BASE}/record/status?session_id=${SESSION_ID}")
echo "$STATUS_RESPONSE" | jq . 2>/dev/null || error "/record/status failed or returned invalid JSON"
if echo "$STATUS_RESPONSE" | jq -e '.state' > /dev/null 2>&1; then
    REC_STATE=$(echo "$STATUS_RESPONSE" | jq -r '.state')
    REC_ROWS=$(echo "$STATUS_RESPONSE" | jq -r '.rows')
    REC_BYTES=$(echo "$STATUS_RESPONSE" | jq -r '.bytes')
    echo "State: ${REC_STATE}, Rows: ${REC_ROWS}, Bytes: ${REC_BYTES}"
    success "/record/status works"
else
    error "/record/status response missing 'state' field"
fi

# Test 8: Check snapshots
test_step "8" "Checking chunk snapshots..."
SNAPSHOTS_RESPONSE=$(curl -s "${API_BASE}/record/snapshots?session_id=${SESSION_ID}")
echo "$SNAPSHOTS_RESPONSE" | jq . 2>/dev/null || error "/record/snapshots failed or returned invalid JSON"
CHUNK_COUNT=$(echo "$SNAPSHOTS_RESPONSE" | jq '. | length')
echo "Chunks available: ${CHUNK_COUNT}"
success "/record/snapshots works"

# If chunks exist, try downloading the first one
if [ "$CHUNK_COUNT" -gt "0" ]; then
    test_step "8a" "Testing chunk file download..."
    FIRST_CHUNK_NAME=$(echo "$SNAPSHOTS_RESPONSE" | jq -r '.[0].name')
    echo "Downloading first chunk: ${FIRST_CHUNK_NAME}"
    CHUNK_DOWNLOAD=$(curl -s "${API_BASE}/files/${SESSION_ID}/${FIRST_CHUNK_NAME}" | head -c 200)
    if [ -n "$CHUNK_DOWNLOAD" ]; then
        echo "First 200 bytes: ${CHUNK_DOWNLOAD}"
        success "Chunk file download works"
    else
        error "Chunk file download failed (empty response)"
    fi
fi

# Test 9: Stop recording
test_step "9" "Stopping recording..."
STOP_REC_RESPONSE=$(curl -s -X POST "${API_BASE}/record/stop" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"${SESSION_ID}\"}")
echo "$STOP_REC_RESPONSE" | jq . 2>/dev/null || error "/record/stop failed or returned invalid JSON"
if echo "$STOP_REC_RESPONSE" | jq -e '.stopped_at' > /dev/null 2>&1; then
    STOPPED_AT=$(echo "$STOP_REC_RESPONSE" | jq -r '.stopped_at')
    FINAL_CHUNKS=$(echo "$STOP_REC_RESPONSE" | jq -r '.chunks')
    FINAL_ROWS=$(echo "$STOP_REC_RESPONSE" | jq -r '.rows')
    echo "Stopped at: ${STOPPED_AT}, Final Chunks: ${FINAL_CHUNKS}, Final Rows: ${FINAL_ROWS}"
    success "/record/stop works"
else
    error "/record/stop response missing 'stopped_at' field"
fi

# Test 10: Stop acquisition
test_step "10" "Stopping acquisition..."
STOP_ACQ_RESPONSE=$(curl -s -X POST "${API_BASE}/sensor/stop")
echo "$STOP_ACQ_RESPONSE" | jq . 2>/dev/null || error "/sensor/stop failed or returned invalid JSON"
if echo "$STOP_ACQ_RESPONSE" | jq -e '.status' > /dev/null 2>&1; then
    success "/sensor/stop works"
else
    error "/sensor/stop response missing 'status' field"
fi

# Test 11: Disconnect
test_step "11" "Disconnecting from sensor..."
DISCONNECT_RESPONSE=$(curl -s -X POST "${API_BASE}/disconnect")
echo "$DISCONNECT_RESPONSE" | jq . 2>/dev/null || error "/disconnect failed or returned invalid JSON"
if echo "$DISCONNECT_RESPONSE" | jq -e '.status' > /dev/null 2>&1; then
    success "/disconnect works"
else
    error "/disconnect response missing 'status' field"
fi

# Summary
echo "=========================================="
echo -e "${GREEN}=== ALL TESTS PASSED ✅ ===${NC}"
echo "=========================================="
echo ""
echo "Recording session files should be at:"
echo "/data/qsensor_recordings/${SESSION_ID}/"
echo ""
echo "Verify with:"
echo "  ls -lh /data/qsensor_recordings/${SESSION_ID}/"
echo "  cat /data/qsensor_recordings/${SESSION_ID}/manifest.json | jq ."
echo ""
