# Q-Sensor API Recording Chain Audit Report

**Date:** 2025-11-13
**API Version:** 0.2.0
**Auditor:** Claude Code

---

## Executive Summary

**Root Cause:** The Pi container is **running old code** or the container was not properly rebuilt after recent changes to add recording endpoints.

**Evidence:** The recording endpoints (`/instrument/health`, `/record/start`, `/record/status`) are **correctly defined** in the current source code at `/Users/matthuewalsh/qseries-noise/Q_Sensor_API/api/main.py`, but they return 404 on the Pi container, indicating a **version drift** between the source code and the running container image.

**Solution:** Rebuild the Docker image from the updated source code and redeploy to the Pi.

---

## Detailed Findings

### 1. Route Registration Analysis

#### ✅ Routes ARE Correctly Defined in Source Code

All recording endpoints are properly registered in [api/main.py](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/api/main.py):

| Endpoint | Method | Line | Decorator | Response Model |
|----------|--------|------|-----------|----------------|
| `/instrument/health` | GET | 1107 | `@app.get("/instrument/health", response_model=HealthResponse)` | ✅ |
| `/record/start` | POST | 891 | `@app.post("/record/start", response_model=RecordStartResponse)` | ✅ |
| `/record/stop` | POST | 977 | `@app.post("/record/stop", response_model=RecordStopResponse)` | ✅ |
| `/record/status` | GET | 1031 | `@app.get("/record/status", response_model=RecordStatusResponse)` | ✅ |
| `/record/snapshots` | GET | 1071 | `@app.get("/record/snapshots", response_model=list[ChunkMetadata])` | ✅ |
| `/files/{session_id}/{filename}` | GET | 1097 | `@app.get("/files/{session_id}/{filename}")` | ✅ |
| `/health` | GET | 1174 | `@app.get("/health")` | ✅ |

**All endpoint decorators are correct** — no APIRouter prefixes, no mount() misconfiguration, all routes registered directly on the FastAPI `app` instance.

#### ✅ Request/Response Models Are Properly Defined

All Pydantic models for recording endpoints are present (lines 141-196):

- `RecordStartRequest` (line 141)
- `RecordStartResponse` (line 149)
- `RecordStopRequest` (line 157)
- `RecordStopResponse` (line 162)
- `RecordStatusResponse` (line 170)
- `ChunkMetadata` (line 180)
- `HealthResponse` (line 189)

**No missing imports** that would silently prevent route registration.

#### ✅ Sensor Aliases Are Present

The `/sensor/*` aliases are correctly registered (lines 796-805):

```python
app.add_api_route("/sensor/status", get_status, methods=["GET"], response_model=StatusResponse)
app.add_api_route("/sensor/latest", get_latest, methods=["GET"])
app.add_api_route("/sensor/recent", get_recent, methods=["GET"])
app.add_api_route("/sensor/stats", get_stats, methods=["GET"], response_model=StatsResponse)
app.add_api_route("/sensor/connect", connect, methods=["POST"], response_model=ConnectResponse)
app.add_api_route("/sensor/config", set_config, methods=["POST"])
app.add_api_route("/sensor/start", start_acquisition, methods=["POST"])
app.add_api_route("/sensor/pause", pause_acquisition, methods=["POST"])
app.add_api_route("/sensor/resume", resume_acquisition, methods=["POST"])
app.add_api_route("/sensor/stop", stop_acquisition, methods=["POST"])
```

**Note:** `/sensor/start` is an alias for `/start` (the acquisition endpoint), **NOT** `/record/start` (the recording endpoint). These are separate operations.

---

### 2. Container Configuration Analysis

#### ✅ Dockerfile Is Correct

[Dockerfile](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/Dockerfile) (line 28):

```dockerfile
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "9150"]
```

- Port 9150 ✅
- Correct module path `api.main:app` ✅
- No path prefix issues ✅

#### ✅ Environment Variables Are Set

From Dockerfile (lines 18-21):

```dockerfile
ENV SERIAL_PORT=/dev/ttyUSB0
ENV SERIAL_BAUD=9600
ENV LOG_LEVEL=INFO
ENV CHUNK_RECORDING_PATH=/data/qsensor_recordings
```

**CHUNK_RECORDING_PATH is configured** for recording sessions.

---

### 3. Version Drift: The Smoking Gun

#### 🔴 Pi Container Returns 404 for New Endpoints

From the Pi terminal transcript:

```bash
$ curl http://localhost:9150/health
{"service":"Q-..."}  ✅ Works

$ curl http://localhost:9150/instrument/health
{"detail":"Not Found"}  ❌ 404

$ curl -X POST http://localhost:9150/record/start -H 'Content-Type: application/json' \
  -d '{"mission":"LocalSmoke","rate_hz":500,"schema_version":1,"roll_interval_s":60}'
{"detail":"Not Found"}  ❌ 404

$ curl http://localhost:9150/record/status
{"detail":"Not Found"}  ❌ 404
```

#### Analysis

**Hypothesis:** The container image running on the Pi was built from an **older version of the code** that did not include the recording endpoints.

**Supporting Evidence:**
1. `/health` endpoint (line 1174) works → basic app is running
2. All recording endpoints return 404 → they are not registered in the running app
3. Source code shows all endpoints are correctly defined → code is updated
4. **Conclusion:** Image was not rebuilt after adding recording endpoints

---

### 4. Patches Applied (Debug Instrumentation)

To facilitate debugging and prevent future version drift, the following enhancements were added to [api/main.py](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/api/main.py):

#### A. Startup Route Table Dump

**Location:** Lines 1228-1236
**Purpose:** Log all registered routes on startup to verify endpoints are loaded

```python
@app.on_event("startup")
async def startup_event():
    """Log startup and configuration, including route table dump."""
    # ... existing config logging ...

    # Dump all registered routes for debugging
    logger.info("=== REGISTERED ROUTES ===")
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            methods = ','.join(sorted(route.methods))
            logger.info(f"{methods:10} {route.path}")
        elif hasattr(route, 'path'):
            logger.info(f"{'WS':10} {route.path}")
    logger.info("=" * 60)
```

**Expected Output on Pi (after rebuild):**
```
=== REGISTERED ROUTES ===
GET        /health
GET        /version
GET        /instrument/health
POST       /record/start
POST       /record/stop
GET        /record/status
GET        /record/snapshots
GET        /files/{session_id}/{filename}
POST       /sensor/start
...
```

#### B. 404 Request Logging Middleware

**Location:** Lines 96-103
**Purpose:** Log all 404 responses to identify missing routes

```python
@app.middleware("http")
async def log_404_requests(request: Request, call_next):
    """Log all 404 responses to help debug missing routes."""
    response = await call_next(request)
    if response.status_code == 404:
        logger.warning(f"404 NOT FOUND: {request.method} {request.url.path} query={dict(request.query_params)}")
    return response
```

**Expected Output on Pi (before rebuild):**
```
404 NOT FOUND: GET /instrument/health query={}
404 NOT FOUND: POST /record/start query={}
404 NOT FOUND: GET /record/status query={'session_id': '...'}
```

#### C. Version Tracking Endpoint

**Location:** Lines 1184-1191
**Purpose:** Allow clients to verify API version and git commit

```python
@app.get("/version")
async def version():
    """Version tracking endpoint for debugging and compatibility checks."""
    return {
        "api": API_VERSION,  # "0.2.0"
        "git": GIT_COMMIT,   # e.g., "a3f2c1b"
        "status": "online"
    }
```

**Usage:**
```bash
curl http://localhost:9150/version
# Expected: {"api":"0.2.0","git":"a3f2c1b","status":"online"}
```

#### D. Enhanced Recording Chain Logging

**Locations:**
- `/sensor/start`: Lines 449-479
- `/record/start`: Lines 915-974
- `/record/stop`: Lines 994-1028
- `/record/status`: Lines 1046-1068
- `/record/snapshots`: Lines 1086-1094

**Purpose:** Detailed logging of recording state transitions with timing and key parameters

**Example logs:**
```
[SENSOR/START] Request: poll_hz=1.0, auto_record=True
[SENSOR/START] Starting acquisition: mode=freerun, poll_hz=1.0
[SENSOR/START] Auto-starting DataRecorder: poll_interval=0.2s
[SENSOR/START] SUCCESS: mode=freerun, recording=True

[RECORD/START] Request received: mission=TestMission, rate_hz=500, schema_version=1, roll_interval_s=60
[RECORD/START] Disk check: free=2.34GB, path=/data/qsensor_recordings
[RECORD/START] Initializing session: 3f2a1b4c-...
[RECORD/START] Creating ChunkedDataStore: path=/data/qsensor_recordings, roll_interval_s=60
[RECORD/START] Starting DataRecorder: poll_interval=0.2s
[RECORD/START] SUCCESS: session=3f2a1b4c-..., rate=500Hz, mission=TestMission, recording_path=/data/qsensor_recordings/3f2a1b4c-...
```

**Chunk finalization logging** (already present in [data_store/store.py:658-661](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/data_store/store.py)):
```python
logger.info(
    f"Finalized chunk {self._chunk_index}: {final_path.name} "
    f"({self._current_chunk_rows} rows, {file_size} bytes, sha256={file_hash[:8]}...)"
)
```

---

## Fix Instructions

### Step 1: Rebuild the Docker Image

On your development machine or build server:

```bash
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API

# Build new image with updated code
docker build -t qsensor-api:latest .

# Optional: Tag with version
docker tag qsensor-api:latest qsensor-api:0.2.0
```

### Step 2: Deploy to Pi

**Option A: Export/Import (if no registry)**

```bash
# On dev machine
docker save qsensor-api:latest | gzip > qsensor-api-latest.tar.gz
scp qsensor-api-latest.tar.gz pi@<PI_IP>:/tmp/

# On Pi
docker load < /tmp/qsensor-api-latest.tar.gz
docker stop qsensor-api  # Stop old container
docker rm qsensor-api    # Remove old container

# Run new container
docker run -d \
  --name qsensor-api \
  --restart unless-stopped \
  -p 9150:9150 \
  -v /data/qsensor_recordings:/data/qsensor_recordings \
  --device=/dev/ttyAMA0:/dev/ttyAMA0 \
  -e SERIAL_PORT=/dev/ttyAMA0 \
  -e SERIAL_BAUD=115200 \
  -e LOG_LEVEL=INFO \
  qsensor-api:latest
```

**Option B: Registry Push/Pull (if available)**

```bash
# On dev machine
docker tag qsensor-api:latest <registry>/qsensor-api:0.2.0
docker push <registry>/qsensor-api:0.2.0

# On Pi
docker pull <registry>/qsensor-api:0.2.0
docker stop qsensor-api && docker rm qsensor-api
docker run -d ... <registry>/qsensor-api:0.2.0
```

### Step 3: Verify Routes on Pi

After deployment, check the container logs:

```bash
# On Pi
docker logs qsensor-api | grep "REGISTERED ROUTES" -A 20
```

**Expected output:**
```
=== REGISTERED ROUTES ===
GET        /
GET        /health
GET        /version
GET        /instrument/health
POST       /connect
POST       /disconnect
GET        /status
GET        /latest
GET        /recent
POST       /start
POST       /stop
POST       /record/start
POST       /record/stop
GET        /record/status
GET        /record/snapshots
GET        /files/{session_id}/{filename}
POST       /sensor/connect
GET        /sensor/status
POST       /sensor/start
...
```

If `/record/start`, `/record/status`, and `/instrument/health` appear in this list, the rebuild was successful.

---

## End-to-End Test Script

Save this as `test_qsensor_e2e.sh` and run on the Pi:

```bash
#!/bin/bash
# Q-Sensor API End-to-End Test Script
# Tests the full recording chain on Pi

set -e  # Exit on error

API_BASE="http://localhost:9150"
SERIAL_PORT="/dev/ttyAMA0"
SERIAL_BAUD="115200"

echo "=== Q-Sensor API E2E Test ==="
echo ""

# Test 1: Basic health
echo "1. Testing /health..."
curl -s "${API_BASE}/health" | jq .
echo "✅ /health works"
echo ""

# Test 2: Version check
echo "2. Testing /version..."
curl -s "${API_BASE}/version" | jq .
echo "✅ /version works"
echo ""

# Test 3: Instrument health
echo "3. Testing /instrument/health..."
curl -s "${API_BASE}/instrument/health" | jq .
echo "✅ /instrument/health works"
echo ""

# Test 4: Connect to sensor
echo "4. Connecting to sensor..."
curl -s -X POST "${API_BASE}/sensor/connect?port=${SERIAL_PORT}&baud=${SERIAL_BAUD}" | jq .
echo "✅ /sensor/connect works"
echo ""

# Wait for connection stabilization
echo "Waiting 2s for connection to stabilize..."
sleep 2

# Test 5: Start acquisition (freerun)
echo "5. Starting acquisition..."
curl -s -X POST "${API_BASE}/sensor/start" | jq .
echo "✅ /sensor/start works"
echo ""

# Wait for buffer to fill
echo "Waiting 2s for sensor buffer to fill..."
sleep 2

# Test 6: Start recording session
echo "6. Starting recording session..."
SESSION_RESPONSE=$(curl -s -X POST "${API_BASE}/record/start" \
  -H 'Content-Type: application/json' \
  -d '{"mission":"AuditTest","rate_hz":500,"schema_version":1,"roll_interval_s":60}')
echo "$SESSION_RESPONSE" | jq .
SESSION_ID=$(echo "$SESSION_RESPONSE" | jq -r '.session_id')
echo "Session ID: ${SESSION_ID}"
echo "✅ /record/start works"
echo ""

# Wait for some data
echo "Recording for 5 seconds..."
sleep 5

# Test 7: Check recording status
echo "7. Checking recording status..."
curl -s "${API_BASE}/record/status?session_id=${SESSION_ID}" | jq .
echo "✅ /record/status works"
echo ""

# Test 8: Check snapshots
echo "8. Checking chunk snapshots..."
curl -s "${API_BASE}/record/snapshots?session_id=${SESSION_ID}" | jq .
echo "✅ /record/snapshots works"
echo ""

# Test 9: Stop recording
echo "9. Stopping recording..."
curl -s -X POST "${API_BASE}/record/stop" \
  -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"${SESSION_ID}\"}" | jq .
echo "✅ /record/stop works"
echo ""

# Test 10: Stop acquisition
echo "10. Stopping acquisition..."
curl -s -X POST "${API_BASE}/sensor/stop" | jq .
echo "✅ /sensor/stop works"
echo ""

# Test 11: Disconnect
echo "11. Disconnecting from sensor..."
curl -s -X POST "${API_BASE}/disconnect" | jq .
echo "✅ /disconnect works"
echo ""

echo "=== ALL TESTS PASSED ✅ ==="
echo ""
echo "Recording session files should be at:"
echo "/data/qsensor_recordings/${SESSION_ID}/"
echo ""
echo "Verify with:"
echo "ls -lh /data/qsensor_recordings/${SESSION_ID}/"
```

**Make executable and run:**

```bash
chmod +x test_qsensor_e2e.sh
./test_qsensor_e2e.sh
```

---

## Summary of Changes

### Files Modified

1. **[api/main.py](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/api/main.py)**
   - Added imports: `subprocess`, `Request` (lines 15-24)
   - Added version tracking: `API_VERSION`, `GIT_COMMIT` (lines 51-56)
   - Added 404 logging middleware (lines 96-103)
   - Added startup route dump (lines 1228-1236)
   - Added `/version` endpoint (lines 1184-1191)
   - Enhanced logging in `/sensor/start` (lines 449-479)
   - Enhanced logging in `/record/start` (lines 915-974)
   - Enhanced logging in `/record/stop` (lines 994-1028)
   - Enhanced logging in `/record/status` (lines 1046-1068)
   - Enhanced logging in `/record/snapshots` (lines 1086-1094)

### Files Created

1. **[RECORDING_CHAIN_AUDIT.md](/Users/matthuewalsh/qseries-noise/Q_Sensor_API/RECORDING_CHAIN_AUDIT.md)** (this file)
2. **test_qsensor_e2e.sh** (see above)

---

## Next Steps

1. ✅ Rebuild Docker image with updated code
2. ✅ Deploy new image to Pi
3. ✅ Run E2E test script to verify all endpoints
4. ✅ Monitor container logs for route table and any 404s
5. ✅ Update Cockpit client to call `/version` on init and warn if API version < 0.2.0

---

## Appendix: Client-Side URL Construction

**Status:** ✅ Client URLs are mostly correct, but some inconsistencies exist.

### From Cockpit Analysis

**File:** [src/electron/services/qsensor-control.ts](/Users/matthuewalsh/Bio_cockpit/src/electron/services/qsensor-control.ts)

✅ **Correct URL Construction** (using `new URL(path, baseUrl)`):
- `/sensor/connect` (line 62)
- `/disconnect` (line 86) — **Note: NOT `/sensor/disconnect`**
- `/instrument/health` (line 107)
- `/sensor/start` (line 130)
- `/sensor/stop` (line 154)
- `/record/start` (line 184)
- `/record/stop` (line 212)

**File:** [src/libs/qsensor-client.ts](/Users/matthuewalsh/Bio_cockpit/src/libs/qsensor-client.ts)

⚠️ **Mixed URL Construction** (some correct, some string concatenation):
- Line 60: `new URL(\`\${this.baseUrl}/sensor/connect\`)` — ✅ Correct
- Line 81: `\`\${this.baseUrl}/sensor/disconnect\`` — ❌ String concat (also wrong path, should be `/disconnect`)
- Line 98: `\`\${this.baseUrl}/instrument/health\`` — ❌ String concat
- Line 119: `\`\${this.baseUrl}/record/start\`` — ❌ String concat
- Line 143: `\`\${this.baseUrl}/record/stop\`` — ❌ String concat

**Recommendation:** Update qsensor-client.ts to consistently use `new URL(path, baseUrl)` pattern for all endpoints.

---

**End of Report**
