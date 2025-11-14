# Auto-Record Conflict Fix

## Issue Summary

Cockpit was unable to obtain a `session_id` from the Q-Sensor API when starting chunked recording, preventing the mirroring service from downloading CSV chunks to the topside computer.

## Root Cause

The API has **two independent recording systems** that were conflicting:

1. **Old System**: `DataStore` + `DataRecorder` (in-memory buffer, used by `/start` endpoint)
2. **New System**: `ChunkedDataStore` + separate `DataRecorder` (chunked CSV files, used by `/record/start`)

### The Conflict Sequence

1. Cockpit calls `/sensor/start` (line 445 in ToolsQSeriesView.vue)
2. API's `/sensor/start` has `auto_record=True` by default (line 429 in api/main.py)
3. API auto-starts the **old DataRecorder** with the old DataStore
4. Cockpit then calls `/record/start` to use chunked recording (line 458 in ToolsQSeriesView.vue)
5. API's `/record/start` checks if a recorder is already running (line 918 in api/main.py)
6. **API returns 409 "Recording already active"** because the old recorder is running
7. Cockpit never receives a `session_id`
8. Without `session_id`, Cockpit can't call `qsensorStore.start()` to begin mirroring
9. All subsequent calls to `/record/status`, `/record/snapshots`, `/files/{session_id}/{filename}` fail with 422/404

### Evidence from Logs

```
2025-11-14 20:29:05 [SENSOR/START] ... auto_record=True
2025-11-14 20:29:08 DataRecorder started (poll interval 0.2s)    ← OLD RECORDER
2025-11-14 20:29:10 [RECORD/START] Request ... mission=Cockpit
2025-11-14 20:29:10 ERROR [RECORD/START] FAILED: Recording already active  ← 409 CONFLICT
... GET /record/status, /record/snapshots → 422 Unprocessable Entity (no session_id)
... GET /files/{session_id}/{filename} → 404
```

No mirroring ever started because `qsensorStore.start()` never received `success: true` with a valid `session_id`.

## Fix Applied

### Change 1: API - Default `auto_record` to `False`

**File**: `/Users/matthuewalsh/qseries-noise/Q_Sensor_API/api/main.py:429`

```python
# BEFORE (BUGGY):
auto_record: bool = Query(True, description="Automatically start DataRecorder")

# AFTER (FIXED):
auto_record: bool = Query(False, description="Automatically start DataRecorder")
```

**Rationale**: Modern clients (like Cockpit) explicitly manage recording via `/record/start` to get session IDs for mirroring. The old auto-record behavior is legacy and conflicts with chunked recording.

### Change 2: Cockpit - Explicitly Pass `auto_record=false`

**File**: `/Users/matthuewalsh/Bio_cockpit/src/electron/services/qsensor-control.ts:134-135`

```typescript
// ADDED:
// CRITICAL: Disable auto_record so /record/start can manage the chunked recorder
url.searchParams.set('auto_record', 'false')
```

**Rationale**: Even though the API now defaults to `False`, being explicit prevents issues if the API default ever changes back.

## Expected Behavior After Fix

### Recording Start Flow

1. Cockpit calls `/sensor/start?auto_record=false`
2. API starts acquisition but **does NOT start the old recorder**
3. Sensor stabilizes for 2s
4. Cockpit calls `/record/start` with mission name
5. API returns `{ session_id: "...", started_at: "...", ... }` ✅
6. Cockpit calls `qsensorStore.arm(sessionId, ...)` and `qsensorStore.start()`
7. Electron mirroring service logs: `[QSensor Mirror] Started session...` ✅
8. Mirroring polls `/record/snapshots?session_id=...` every N seconds
9. Downloads new chunks with SHA256 verification to `{userData}/qsensor/{mission}/{sessionId}/`
10. **CSVs appear on topside computer in real-time** ✅

### Logs You Should See

**Pi API logs:**
```
[SENSOR/START] Request: poll_hz=..., auto_record=False
[SENSOR/START] Starting acquisition: mode=freerun, poll_hz=...
[SENSOR/START] SUCCESS: mode=freerun, recording=False  ← NO AUTO RECORDER
[RECORD/START] Request received: mission=Cockpit, rate_hz=500, ...
[RECORD/START] Initializing session: <session_id>
[RECORD/START] Creating ChunkedDataStore: path=...
[RECORD/START] Starting DataRecorder: poll_interval=0.2s
[RECORD/START] SUCCESS: session=<session_id>, rate=500Hz, ...
Finalized chunk 0: chunk_00000.csv (548 rows, 43818 bytes)
```

**Cockpit Electron logs:**
```
[QSensor Mirror] Service registered
[QSensor Mirror] IPC start request: session=<session_id>, vehicle=blueos.local, cadence=60s, ...
[QSensor Mirror] Started session <session_id>: cadence=60s, path=/Users/.../qsensor/Cockpit/<session_id>
[QSensor Mirror] Found 1 new chunks for session <session_id>
[QSensor Mirror] Downloaded chunk chunk_00000.csv: 43818 bytes
```

## Deployment

### Step 1: Rebuild Pi Image

```bash
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API

# Build for ARM v7 (32-bit Raspberry Pi)
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .

# Save image
docker save q-sensor-api:pi | gzip > q-sensor-api-pi-auto-record-fix.tar.gz

# Transfer to Pi (via USB stick or scp)
# On Pi:
gunzip -c q-sensor-api-pi-auto-record-fix.tar.gz | docker load

# Restart container
docker stop q-sensor
docker rm q-sensor
docker run -d \
  --name q-sensor \
  --privileged \
  -v /dev:/dev \
  -v /data/qsensor_recordings:/data/qsensor_recordings \
  -p 9150:9150 \
  -e SERIAL_PORT=/dev/ttyAMA0 \
  -e SERIAL_BAUD=115200 \
  q-sensor-api:pi
```

### Step 2: Rebuild Cockpit Electron App

The TypeScript change in `qsensor-control.ts` will be compiled when you rebuild the Electron app.

```bash
cd /Users/matthuewalsh/Bio_cockpit
npm run build  # or yarn build
# Then relaunch Cockpit
```

## Testing

1. Open Cockpit Desktop
2. Go to Q-Series Tool
3. Connect to sensor (blueos.local:9150, /dev/ttyAMA0, 115200)
4. Start Recording
5. **Verify in Cockpit logs**: `[QSensor Mirror] Started session...`
6. **Verify in Pi logs**: `[RECORD/START] SUCCESS: session=...`
7. Wait 60-70 seconds for first chunk to finalize
8. **Check topside folder**: `~/Library/Application Support/Cockpit/qsensor/Cockpit/<session_id>/`
9. **Verify CSV file exists**: `chunk_00000.csv` with ~548 rows
10. Stop recording
11. **Verify final sync**: All chunks downloaded to topside

## Related Issues

This fix also addresses:
- `/record/status` 422 errors (now receives valid session_id)
- `/record/snapshots` 422 errors (now receives valid session_id)
- `/files/{session_id}/{filename}` 404 errors (now receives valid session_id)
- Mirroring service never starting (now starts with valid session_id)

## Backward Compatibility

**Breaking Change**: Clients that relied on `/sensor/start` auto-starting the old DataRecorder will need to:
- Either pass `auto_record=true` explicitly, OR
- Migrate to the new chunked recording system via `/record/start`

**Recommended**: All clients should migrate to `/record/start` for proper session management and mirroring support.
