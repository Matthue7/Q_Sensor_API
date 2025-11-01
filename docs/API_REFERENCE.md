# Q-Sensor API Reference

REST and WebSocket API for Biospherical Q-Series sensor data acquisition.

**Base URL:** `http://localhost:8000`

---

## Quick Start

### Running the Server

```bash
# Install dependencies
pip install -e .

# Start server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Server runs at http://localhost:8000
# Docs at http://localhost:8000/docs (Swagger UI)
```

### Basic Workflow

```bash
# 1. Health check
curl http://localhost:8000/

# 2. Connect to sensor
curl -X POST "http://localhost:8000/connect?port=/dev/ttyUSB0&baud=9600"

# 3. Configure sensor
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"}'

# 4. Start acquisition
curl -X POST http://localhost:8000/start

# 5. Start recording to DataFrame
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2"

# 6. Check status
curl http://localhost:8000/status

# 7. Get latest reading
curl http://localhost:8000/latest

# 8. Get recent readings (last 60 seconds)
curl "http://localhost:8000/recent?seconds=60"

# 9. Stop recording (with CSV export)
curl -X POST "http://localhost:8000/recording/stop?flush=csv"

# 10. Stop acquisition
curl -X POST http://localhost:8000/stop

# 11. Disconnect
curl -X POST http://localhost:8000/disconnect
```

---

## Endpoints

### Health Check

#### `GET /`

Returns service information and health status.

**Response:**
```json
{
  "service": "Q-Sensor API",
  "version": "1.0.0",
  "status": "online"
}
```

---

### Connection Management

#### `POST /connect`

Connect to sensor and enter configuration menu.

**Parameters:**
- `port` (query, required): Serial port path (e.g., `/dev/ttyUSB0`)
- `baud` (query, optional): Baud rate (default 9600)

**Example:**
```bash
curl -X POST "http://localhost:8000/connect?port=/dev/ttyUSB0&baud=9600"
```

**Response (200):**
```json
{
  "status": "connected",
  "sensor_id": "Q12345"
}
```

**Errors:**
- `400`: Already connected
- `503`: Cannot open serial port (SerialIOError)
- `504`: Menu timeout (MenuTimeout)

---

#### `POST /disconnect`

Disconnect from sensor and clean up resources.

Stops acquisition and recording if running.

**Example:**
```bash
curl -X POST http://localhost:8000/disconnect
```

**Response (200):**
```json
{
  "status": "disconnected"
}
```

---

### Configuration

#### `POST /config`

Apply configuration changes. Only provided fields are updated.

Sensor must be in CONFIG_MENU state (not acquiring).

**Request Body (all fields optional):**
```json
{
  "averaging": 125,        // Number of readings to average (1-65535)
  "adc_rate_hz": 125,      // ADC sample rate in Hz (4, 8, 16, 33, 62, 125, 250, 500)
  "mode": "freerun",       // Operating mode ("freerun" or "polled")
  "tag": "A"               // TAG character for polled mode (A-Z, required if mode="polled")
}
```

**Example (set freerun mode):**
```bash
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"}'
```

**Example (set polled mode):**
```bash
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "polled", "tag": "A"}'
```

**Response (200):**
```json
{
  "averaging": 125,
  "adc_rate_hz": 125,
  "mode": "freerun",
  "tag": null,
  "sample_period_s": 1.0
}
```

**Errors:**
- `400`: Invalid configuration value (InvalidConfigValue)
- `503`: Not connected (SerialIOError)
- `504`: Menu timeout (MenuTimeout)

---

### Acquisition Control

#### `POST /start`

Start data acquisition in configured mode.

- **Freerun:** Device streams continuously at `adc_rate_hz / averaging`
- **Polled:** Controller queries at `poll_hz` rate

**Parameters:**
- `poll_hz` (query, optional): Poll rate for polled mode (default 1.0 Hz)

**Example (freerun):**
```bash
curl -X POST http://localhost:8000/start
```

**Example (polled at 2 Hz):**
```bash
curl -X POST "http://localhost:8000/start?poll_hz=2.0"
```

**Response (200):**
```json
{
  "status": "started",
  "mode": "freerun"
}
```

**Errors:**
- `400`: Already acquiring
- `503`: Not connected

---

#### `POST /stop`

Stop acquisition and return to CONFIG_MENU state.

**CRITICAL:** If recorder is running, it is stopped first to prevent data loss.

**Example:**
```bash
curl -X POST http://localhost:8000/stop
```

**Response (200):**
```json
{
  "status": "stopped"
}
```

**Errors:**
- `400`: Not acquiring
- `503`: Not connected

---

### DataFrame Recording

#### `POST /recording/start`

Start DataRecorder to poll controller buffer and write to pandas DataFrame.

Must be called after `/start` (acquisition must be running).

**Parameters:**
- `poll_interval_s` (query, optional): Recorder poll interval (default 0.2s)
- `auto_flush_interval_s` (query, optional): Auto-flush interval (default None)
- `max_rows` (query, optional): Max DataFrame rows in memory (default 100000)

**Example:**
```bash
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2&max_rows=50000"
```

**Response (200):**
```json
{
  "status": "recording"
}
```

**Errors:**
- `400`: Already recording or acquisition not started
- `503`: Not connected

---

#### `POST /recording/stop`

Stop DataRecorder and optionally flush DataFrame to disk.

**Parameters:**
- `flush` (query, optional): Export format (`"csv"`, `"parquet"`, or `"none"`, default `"none"`)

**Example (no flush):**
```bash
curl -X POST "http://localhost:8000/recording/stop?flush=none"
```

**Example (export to CSV):**
```bash
curl -X POST "http://localhost:8000/recording/stop?flush=csv"
```

**Response (200):**
```json
{
  "status": "stopped",
  "flush_path": "/path/to/qsensor_data_20251101_153454.csv"
}
```

**Errors:**
- `400`: Recorder not running

---

### Data Access

#### `GET /status`

Get current system status.

**Example:**
```bash
curl http://localhost:8000/status
```

**Response (200):**
```json
{
  "connected": true,
  "recording": true,
  "sensor_id": "Q12345",
  "mode": "freerun",
  "rows": 1523,
  "state": "acq_freerun"
}
```

**Fields:**
- `connected`: True if connected to sensor
- `recording`: True if DataRecorder is running
- `sensor_id`: Sensor serial number
- `mode`: Operating mode (`"freerun"` or `"polled"`)
- `rows`: Total rows in DataFrame
- `state`: Controller state (`"disconnected"`, `"config_menu"`, `"acq_freerun"`, `"acq_polled"`, `"paused"`)

---

#### `GET /latest`

Get the most recent reading.

**Example:**
```bash
curl http://localhost:8000/latest
```

**Response (200, with data):**
```json
{
  "timestamp": "2025-11-01T15:34:54.123456+00:00",
  "sensor_id": "Q12345",
  "mode": "freerun",
  "value": 123.456789,
  "TempC": 21.5,
  "Vin": 12.3
}
```

**Response (200, no data):**
```json
{}
```

**Schema:**
- `timestamp`: UTC ISO 8601 timestamp
- `sensor_id`: Sensor serial number
- `mode`: `"freerun"` or `"polled"`
- `value`: Main calibrated reading (always present)
- `TempC`: Temperature in Celsius (optional, may be `null`)
- `Vin`: Line voltage in volts (optional, may be `null`)

---

#### `GET /recent`

Get recent readings from last N seconds.

**Parameters:**
- `seconds` (query, required): Time window in seconds (1-300, capped at 300)

**Example:**
```bash
curl "http://localhost:8000/recent?seconds=60"
```

**Response (200):**
```json
{
  "rows": [
    {
      "timestamp": "2025-11-01T15:34:54.123456+00:00",
      "sensor_id": "Q12345",
      "mode": "freerun",
      "value": 123.456789,
      "TempC": 21.5,
      "Vin": null
    },
    {
      "timestamp": "2025-11-01T15:34:55.234567+00:00",
      "sensor_id": "Q12345",
      "mode": "freerun",
      "value": 123.567890,
      "TempC": 21.5,
      "Vin": null
    }
  ]
}
```

**Errors:**
- `422`: Validation error (seconds out of range)

**Limits:**
- Maximum `seconds`: 300 (5 minutes)
- Minimum `seconds`: 1

---

### WebSocket Streaming

#### `WS /stream`

WebSocket endpoint for real-time streaming of latest readings.

Sends JSON updates every ~100ms (10 Hz) with latest reading.

**Example (JavaScript):**
```javascript
const ws = new WebSocket("ws://localhost:8000/stream");

ws.onopen = () => {
    console.log("Connected to stream");
};

ws.onmessage = (event) => {
    const reading = JSON.parse(event.data);
    console.log(`Value: ${reading.value}, Time: ${reading.timestamp}`);
};

ws.onerror = (error) => {
    console.error("WebSocket error:", error);
};

ws.onclose = () => {
    console.log("Disconnected from stream");
};
```

**Example (Python):**
```python
import websocket
import json

def on_message(ws, message):
    reading = json.loads(message)
    print(f"Value: {reading['value']}, Time: {reading['timestamp']}")

def on_error(ws, error):
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("Connection closed")

ws = websocket.WebSocketApp(
    "ws://localhost:8000/stream",
    on_message=on_message,
    on_error=on_error,
    on_close=on_close
)
ws.run_forever()
```

**Message Format:**
```json
{
  "timestamp": "2025-11-01T15:34:54.123456+00:00",
  "sensor_id": "Q12345",
  "mode": "freerun",
  "value": 123.456789,
  "TempC": 21.5,
  "Vin": 12.3
}
```

**Notes:**
- Updates sent only when new reading arrives (timestamp changes)
- Update rate: ~10 Hz (100ms intervals)
- Multiple clients can connect simultaneously
- If no data store exists, sends error message and closes

---

## Error Codes

| HTTP Code | Exception | Description |
|-----------|-----------|-------------|
| **400** | `InvalidConfigValue` | Invalid parameter (averaging, rate, mode, tag) |
| **422** | Validation Error | FastAPI parameter validation failed (e.g., `seconds > 300`) |
| **503** | `SerialIOError` | Serial port error (cannot open, not connected) |
| **504** | `MenuTimeout` | Device menu timeout (no response within expected time) |
| **500** | Other | Unhandled exception |

---

## Complete Example: Freerun Workflow

```bash
#!/bin/bash

# 1. Connect to sensor
curl -X POST "http://localhost:8000/connect?port=/dev/ttyUSB0&baud=9600"

# 2. Configure for freerun at 1 Hz (125 Hz / 125 averaging)
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "averaging": 125,
    "adc_rate_hz": 125,
    "mode": "freerun"
  }'

# 3. Start acquisition
curl -X POST http://localhost:8000/start

# 4. Start recording (poll controller buffer every 200ms)
curl -X POST "http://localhost:8000/recording/start?poll_interval_s=0.2"

# 5. Wait for data collection (60 seconds)
sleep 60

# 6. Get latest reading
curl http://localhost:8000/latest | jq

# 7. Get all readings from last 60 seconds
curl "http://localhost:8000/recent?seconds=60" | jq '.rows | length'

# 8. Stop recording and export to CSV
curl -X POST "http://localhost:8000/recording/stop?flush=csv" | jq

# 9. Stop acquisition
curl -X POST http://localhost:8000/stop

# 10. Disconnect
curl -X POST http://localhost:8000/disconnect
```

---

## Complete Example: Polled Workflow

```bash
#!/bin/bash

# 1. Connect
curl -X POST "http://localhost:8000/connect?port=/dev/ttyUSB0&baud=9600"

# 2. Configure for polled mode with TAG 'A'
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "averaging": 125,
    "adc_rate_hz": 125,
    "mode": "polled",
    "tag": "A"
  }'

# 3. Start acquisition with 2 Hz polling
curl -X POST "http://localhost:8000/start?poll_hz=2.0"

# 4. Start recording
curl -X POST http://localhost:8000/recording/start

# 5. Wait for data
sleep 30

# 6. Get recent readings (should have ~60 readings at 2 Hz)
curl "http://localhost:8000/recent?seconds=30" | jq '.rows | length'

# 7. Stop recording
curl -X POST "http://localhost:8000/recording/stop?flush=parquet"

# 8. Stop acquisition and disconnect
curl -X POST http://localhost:8000/stop
curl -X POST http://localhost:8000/disconnect
```

---

## CORS Configuration

API allows cross-origin requests from:
- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://localhost:8000`
- `http://127.0.0.1:8000`

For production, update CORS origins in `api/main.py`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://your-production-domain.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Testing

### Run API Tests

```bash
# All API endpoint tests (uses FakeSerial, no hardware required)
pytest tests/test_api_endpoints.py -v

# WebSocket tests
pytest tests/test_api_ws.py -v

# All tests
pytest tests/test_api*.py -v
```

### Interactive API Documentation

FastAPI provides auto-generated interactive documentation:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

---

## Firmware Alignment

API parameters align with firmware behavior from [2150REV4.003.bas](../2150REV4.003.bas):

| API Parameter | Firmware Mapping | Valid Values | Notes |
|---------------|------------------|--------------|-------|
| `averaging` | `adcToAverage` (EEPROM) | 1-65535 | Number of ADC readings to average |
| `adc_rate_hz` | `adcRate` (EEPROM) | 4, 8, 16, 33, 62, 125, 250, 500 | ADC sample rate in Hz |
| `mode="freerun"` | `OperatingMode="0"` | - | Continuous streaming |
| `mode="polled"` | `OperatingMode="1"` | - | On-demand queries |
| `tag` | `sTAG` (EEPROM) | A-Z | Single uppercase character for polled mode |
| `poll_hz` | Software-controlled | 1-15 Hz recommended | Query frequency (polled mode only) |

**Sample Rate Calculation:**
```
sample_rate_hz = adc_rate_hz / averaging
```

**Examples:**
- 125 Hz / 125 avg = 1 Hz (1 sample per second)
- 125 Hz / 12 avg = ~10 Hz (10 samples per second)

**Optional Fields (TempC, Vin):**
- Controlled by firmware `TempOutput` and `LineVoutput` flags
- If flag is 0, field is omitted from output
- API normalizes by inserting `null` for missing fields in JSON

---

## Performance Considerations

### Memory Usage

- DataStore default: 100,000 rows ≈ 8 MB
- Adjust via `/recording/start?max_rows=N`

### CPU Usage

- Recorder poll interval: 200ms (5 Hz) → minimal CPU
- WebSocket push rate: 100ms (10 Hz) → low overhead

### Limits

- `/recent` capped at 300 seconds (5 minutes)
- WebSocket messages only sent when new data arrives (no polling spam)

---

## Troubleshooting

### "Not connected" errors

Ensure `/connect` was called successfully before other operations.

### "Acquisition not running" error

Call `/start` before `/recording/start`.

### No data in `/latest` or `/recent`

1. Check `/status` - is `recording: true`?
2. Wait longer for data to accumulate (sample rate may be slow)
3. Verify sensor is actually streaming (check hardware/FakeSerial config)

### WebSocket closes immediately

If no `DataStore` exists, WebSocket returns error and closes.
Ensure `/connect` was called to create the store.

---

**API Version:** 1.0.0
**Last Updated:** 2025-11-01
**Firmware Version:** 4.003 (2150 Mode - Digital Output, Linear)
