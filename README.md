# Q-Sensor API - BlueOS Extension

A production-ready BlueOS extension providing real-time data acquisition and control for Biospherical Q-Series sensors (firmware 4.003, 2150 mode). Deploys as a Docker container on Raspberry Pi 4 with integrated web UI, REST API, and WebSocket streaming.

## Overview

The Q-Sensor API is a **self-contained BlueOS extension** that:

- Runs a FastAPI service inside a Docker container on Raspberry Pi (ARMv7)
- Provides browser-based sensor control via integrated React webapp
- Streams real-time data through REST endpoints and WebSocket
- Records data to pandas DataFrames with CSV/Parquet export
- Automatically appears in the BlueOS sidebar via service discovery
- Persists data to `/usr/blueos/userdata/q_sensor/`

**Target Platform**: Raspberry Pi 4 running BlueOS (ARM 32-bit)
**Sensor Compatibility**: Q-Series firmware 4.003 in Digital Output, Linear (2150 mode)
**Communication**: Serial (USB) or Ethernet connection

## Architecture

```
BlueOS (Raspberry Pi 4)
├── Docker Container: q-sensor-api
│   ├── FastAPI Service (port 9150)
│   │   ├── REST API endpoints (/sensor/*, /recording/*)
│   │   ├── WebSocket streaming (/stream)
│   │   ├── Service discovery (/register_service)
│   │   └── Static file serving (React webapp)
│   ├── q_sensor_lib (core sensor library)
│   │   ├── SensorController (state machine)
│   │   ├── Serial transport (pyserial)
│   │   └── Thread-safe ring buffer
│   └── data_store (DataFrame recording)
│       ├── DataStore (pandas)
│       └── DataRecorder (background polling)
└── BlueOS Helper
    └── Scans /register_service → adds to sidebar
```

**Data Flow**:
1. User clicks "Q-Sensor API" in BlueOS sidebar
2. React webapp loads from `/static/webapp/`
3. User clicks "Connect" → API calls sensor via `/dev/ttyUSB0`
4. User clicks "Start" → Acquisition begins, DataRecorder polls sensor buffer
5. Data flows: Sensor → Controller → RingBuffer → DataStore (pandas)
6. Frontend polls `/sensor/latest` or subscribes to WebSocket `/stream`
7. User exports data via `/recording/export/csv` or `/recording/export/parquet`

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed system design.

## Quick Start (BlueOS Deployment)

### Prerequisites

- **Development Machine**: Mac or Linux with Docker Desktop
- **Target Device**: Raspberry Pi 4 with BlueOS installed
- **Network**: SSH access to Pi at `blueos.local`
- **Sensor**: Q-Series connected via USB serial adapter

### Build and Deploy

```bash
# 1. Clone repository
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API

# 2. Build Docker image for ARMv7 (cross-compile from Mac/Linux)
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .

# 3. Save image to tarball
docker save q-sensor-api:pi -o ~/q-sensor-api-pi.tar

# 4. Transfer to Pi
scp ~/q-sensor-api-pi.tar pi@blueos.local:/tmp/

# 5. Deploy on Pi
ssh pi@blueos.local << 'EOF'
  # Load image
  docker load -i /tmp/q-sensor-api-pi.tar

  # Stop old container (if exists)
  docker stop q-sensor 2>/dev/null || true
  docker rm q-sensor 2>/dev/null || true

  # Run container
  docker run -d --name q-sensor \
    --network host \
    --device /dev/ttyUSB0:/dev/ttyUSB0 \
    --group-add dialout \
    -v /usr/blueos/userdata/q_sensor:/data \
    -e SERIAL_PORT=/dev/ttyUSB0 \
    -e SERIAL_BAUD=9600 \
    -e LOG_LEVEL=INFO \
    --restart unless-stopped \
    q-sensor-api:pi

  # Verify running
  docker ps | grep q-sensor
EOF

# 6. Access via BlueOS
# Open http://blueos.local in browser
# Click "Q-Sensor API" in sidebar → webapp opens
```

### Alternative: Build Directly on Pi

For slow but simpler deployment (no cross-compilation):

```bash
# Use provided script
./scripts/build_on_pi.sh

# Takes ~100 minutes on Pi 4
# Builds and runs container automatically
```

## Access Points

Once deployed, the Q-Sensor API is accessible at:

| Interface | URL | Description |
|-----------|-----|-------------|
| **BlueOS Sidebar** | Click "Q-Sensor API" | Opens integrated React webapp |
| **Direct Webapp** | `http://blueos.local:9150/` | Main web interface |
| **API Docs (Swagger)** | `http://blueos.local:9150/docs` | Interactive API documentation |
| **Service Metadata** | `http://blueos.local:9150/register_service` | BlueOS discovery endpoint |
| **Health Check** | `http://blueos.local:9150/health` | Container health status |

## Using the Web Interface

1. **Open webapp** - Click "Q-Sensor API" in BlueOS sidebar
2. **Connect to sensor** - Click "Connect" (uses `/dev/ttyUSB0` by default)
3. **Configure** (optional) - Set mode (freerun/polled), averaging, ADC rate
4. **Start acquisition** - Click "Start" (auto-starts DataRecorder)
5. **View data** - Live chart updates, latest reading displayed
6. **Export data** - Download as CSV or Parquet when done
7. **Stop acquisition** - Click "Stop"
8. **Disconnect** - Click "Disconnect" to release serial port

## REST API Examples

### Basic Workflow (cURL)

```bash
# 1. Connect to sensor
curl -X POST "http://blueos.local:9150/sensor/connect?port=/dev/ttyUSB0&baud=9600"
# Response: {"status": "connected", "sensor_id": "serial ######   ", ...}

# 2. Get current configuration
curl http://blueos.local:9150/sensor/config
# Response: {"averaging": 100, "adc_rate_hz": 125, "mode": "polled", ...}

# 3. Configure sensor (optional)
curl -X POST http://blueos.local:9150/sensor/config \
  -H "Content-Type: application/json" \
  -d '{"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"}'

# 4. Start acquisition (auto-starts DataRecorder)
curl -X POST "http://blueos.local:9150/sensor/start?poll_hz=1.0"
# Response: {"status": "started", "mode": "freerun", "recording": true}

# 5. Get latest reading
curl http://blueos.local:9150/sensor/latest
# Response: {"value": 1.234567, "timestamp": "2025-11-10T12:34:56.789Z", ...}

# 6. Get recent data (last 60 seconds)
curl "http://blueos.local:9150/sensor/recent?seconds=60"
# Response: {"readings": [{...}, {...}], "count": 58}

# 7. Get statistics
curl http://blueos.local:9150/sensor/stats
# Response: {"row_count": 58, "start_time": "...", "est_sample_rate_hz": 1.02}

# 8. Export data
curl "http://blueos.local:9150/recording/export/csv" -o data.csv

# 9. Stop acquisition
curl -X POST http://blueos.local:9150/sensor/stop

# 10. Disconnect
curl -X POST http://blueos.local:9150/sensor/disconnect
```

### WebSocket Streaming (JavaScript)

```javascript
// Connect to WebSocket (from React webapp)
const ws = new WebSocket("ws://blueos.local:9150/stream");

ws.onopen = () => console.log("Connected to sensor stream");

ws.onmessage = (event) => {
  const reading = JSON.parse(event.data);
  console.log(`Value: ${reading.data.value}, Time: ${reading.ts}`);
  // Update chart, display, etc.
};

ws.onerror = (error) => console.error("WebSocket error:", error);

ws.onclose = () => console.log("Stream disconnected");
```

## Python Library Usage (Development/Testing)

The core `q_sensor_lib` can be used standalone for development and testing:

### Installation

```bash
# Clone repository
git clone https://github.com/biospherical/q-sensor-api.git
cd q-sensor-api

# Install in development mode
pip install -e ".[dev]"
```

### Freerun Mode Example

```python
from q_sensor_lib import SensorController
import time

# Create controller and connect
controller = SensorController()
controller.connect(port="/dev/ttyUSB0", baud=9600)

# Configure for freerun mode
controller.set_averaging(125)  # Average 125 readings
controller.set_adc_rate(125)   # ADC at 125 Hz -> 1 sample/sec
controller.set_mode("freerun")

# Start acquisition
controller.start_acquisition()

# Read data continuously
for i in range(10):
    readings = controller.read_buffer_snapshot()
    if readings:
        latest = readings[-1]
        print(f"Reading {i+1}: {latest.data['value']:.6f}")
    time.sleep(1.0)

# Stop and disconnect
controller.stop()
controller.disconnect()
```

### Polled Mode Example

```python
from q_sensor_lib import SensorController
import time

controller = SensorController()
controller.connect(port="/dev/ttyUSB0")

# Configure for polled mode with TAG 'A'
controller.set_mode("polled", tag="A")
controller.set_averaging(100)
controller.set_adc_rate(125)

# Start acquisition with 2 Hz polling
controller.start_acquisition(poll_hz=2.0)

# Wait for data collection
time.sleep(5.0)

# Get buffered readings
readings = controller.read_buffer_snapshot()
print(f"Collected {len(readings)} readings in 5 seconds")

for reading in readings[-5:]:  # Last 5 readings
    print(f"{reading.ts.isoformat()}: {reading.data['value']:.6f}")

controller.disconnect()
```

### DataFrame Recording Example

```python
from q_sensor_lib import SensorController
from data_store import DataStore, DataRecorder
import time

# Setup controller
controller = SensorController()
controller.connect(port="/dev/ttyUSB0")
controller.set_mode("freerun")
controller.start_acquisition()

# Create DataStore and DataRecorder
store = DataStore(max_rows=10000)
recorder = DataRecorder(controller, store, poll_interval_s=0.2)

# Start recording
recorder.start()
print("Recording for 10 seconds...")
time.sleep(10.0)

# Stop recording
recorder.stop()

# Get DataFrame
df = store.get_dataframe()
print(f"\nRecorded {len(df)} rows")
print(df.head())

# Export to CSV
output_path = store.export_csv("/tmp/sensor_data.csv")
print(f"Exported to: {output_path}")

controller.disconnect()
```

## Configuration

### Environment Variables

Set in `docker run` command or Docker Compose:

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_PORT` | `/dev/ttyUSB0` | Serial device path |
| `SERIAL_BAUD` | `9600` | Baud rate (9600 or 115200) |
| `LOG_LEVEL` | `INFO` | Logging: DEBUG, INFO, WARNING, ERROR |
| `CORS_ORIGINS` | (multiple) | Comma-separated allowed origins |

Example with custom config:

```bash
docker run -d --name q-sensor \
  --network host \
  --device /dev/ttyUSB1:/dev/ttyUSB1 \
  -e SERIAL_PORT=/dev/ttyUSB1 \
  -e SERIAL_BAUD=115200 \
  -e LOG_LEVEL=DEBUG \
  q-sensor-api:pi
```

### Sensor Modes

**Freerun Mode**:
- Sensor streams data continuously
- Sample rate = `ADC_rate / averaging`
- Example: 125 Hz ADC ÷ 125 averaging = 1 Hz output
- Best for: Continuous monitoring, high-frequency data

**Polled Mode**:
- API polls sensor on demand
- Requires TAG identifier (A-Z)
- Poll frequency set by `poll_hz` parameter
- Best for: Power efficiency, low-frequency sampling

## Web UI Development

The integrated React webapp is built from a separate repository:

### Build Process

```bash
# Navigate to React app source
cd /Users/matthuewalsh/qseries-noise/Q_Web

# Install dependencies (first time)
npm install

# Build production bundle
npm run build

# Copy to API static directory
cp -r dist/* ../Q_Sensor_API/api/static/webapp/

# Rebuild Docker image
cd ../Q_Sensor_API
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .
```

### Local Development

```bash
# Terminal 1: Run API server
cd Q_Sensor_API
uvicorn api.main:app --reload --port 9150

# Terminal 2: Run React dev server
cd Q_Web
npm run dev
# Opens at http://localhost:3000
# Proxies API calls to localhost:9150
```

See [docs/WEBAPP_BUILD.md](docs/WEBAPP_BUILD.md) for detailed build documentation.

## Testing

### Unit Tests (No Hardware Required)

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run full test suite
pytest

# Run with coverage
pytest --cov=q_sensor_lib --cov=api --cov=data_store

# Run specific test file
pytest tests/test_freerun_stream.py -v

# Run with timeout (prevents hanging tests)
pytest --timeout=10
```

The test suite uses `FakeSerial` - a complete device simulator that mimics firmware 4.003 behavior. No physical sensor required.

### Hardware Validation (Requires Real Sensor)

```bash
cd pi_hw_tests

# Run individual test
python 02_freerun_smoke.py --port /dev/ttyUSB0

# Run in fake mode (dry-run, no hardware)
python 02_freerun_smoke.py --fake

# All tests support --fake for CI/CD
```

Available hardware tests:
- `00_env_check.py` - Python version, dependencies, ports
- `01_connect_and_identify.py` - Connection and sensor ID
- `02_freerun_smoke.py` - 10s freerun acquisition
- `03_polled_smoke.py` - 10s polled acquisition
- `04_menu_consistency.py` - Menu navigation robustness
- `05_temp_vin_presence.py` - Optional fields (TempC, Vin)
- `06_pause_resume_durability.py` - Pause/resume cycles
- `07_disconnect_reconnect.py` - Connection resilience
- `08_burn_in_30min.py` - 30-minute continuous operation
- `09_export_throughput.py` - DataFrame export performance

### Local API Testing (Mac/Linux)

```bash
# Start API server
uvicorn api.main:app --reload --port 9150

# Open browser
open http://localhost:9150/docs

# Test with FakeSerial (simulated sensor)
# In Swagger UI:
# 1. POST /sensor/connect with port="FAKE"
# 2. POST /sensor/start
# 3. GET /sensor/latest (should return simulated data)
```

## Troubleshooting

### Container Not Seeing Sensor

```bash
# 1. Verify USB device exists on Pi
ssh pi@blueos.local 'ls -la /dev/ttyUSB*'
# Should show: /dev/ttyUSB0

# 2. Check device mapping in container
ssh pi@blueos.local 'docker exec q-sensor ls -la /dev/ttyUSB*'
# Should show: /dev/ttyUSB0

# 3. Verify dialout group membership
ssh pi@blueos.local 'docker exec q-sensor groups'
# Should include: dialout

# 4. Check container logs
ssh pi@blueos.local 'docker logs --tail 100 q-sensor'
# Look for permission errors or connection issues
```

### Webapp Not Loading

```bash
# 1. Verify container is running
ssh pi@blueos.local 'docker ps | grep q-sensor'

# 2. Check static files exist
ssh pi@blueos.local 'docker exec q-sensor ls -la /app/api/static/webapp/'
# Should show: index.html, assets/

# 3. Test direct API access
curl http://blueos.local:9150/health
# Should return: {"service": "Q-Sensor API", "version": "1.0.0", "status": "online"}

# 4. Check browser console (F12)
# Look for 404 errors on assets or API calls
```

### Data Not Appearing in UI

```bash
# 1. Check sensor connection status
curl http://blueos.local:9150/sensor/status
# "connected": true, "recording": true

# 2. Verify DataRecorder is running
curl http://blueos.local:9150/sensor/stats
# "row_count" should be > 0

# 3. Check latest reading
curl http://blueos.local:9150/sensor/latest
# Should return reading object (not empty {})

# 4. Review container logs for errors
ssh pi@blueos.local 'docker logs --tail 50 q-sensor | grep ERROR'
```

### BlueOS Not Detecting Service

```bash
# 1. Verify /register_service endpoint
curl http://blueos.local:9150/register_service
# Should return JSON with name, icon, webpage, etc.

# 2. Wait for BlueOS Helper scan (runs every 5-10 seconds)
# Refresh BlueOS page in browser

# 3. Check BlueOS Helper logs
ssh pi@blueos.local 'docker logs blueos-core | grep -i "q-sensor"'

# 4. Restart BlueOS Helper (if needed)
ssh pi@blueos.local 'docker restart blueos-core'
```

### Build Failures

```bash
# pandas/numpy compilation errors on ARMv7:
# - Ensure using Python 3.10 (not 3.11)
# - Verify pandas==1.3.5 (not 2.x)
# - Use full python:3.10 image (not slim)

# Cross-compilation issues:
docker buildx create --use
docker buildx inspect --bootstrap

# Cache issues:
docker buildx build --no-cache --platform linux/arm/v7 ...
```

## Repository Structure

```
Q_Sensor_API/
├── api/                        # FastAPI REST/WebSocket server
│   ├── main.py                # API entry point (888 lines)
│   └── static/                # Web UI assets
│       ├── webapp/            # React production build
│       └── blueos-ui/         # Simple Chart.js UI
├── q_sensor_lib/              # Core sensor library (1,848 LOC)
│   ├── controller.py          # Main SensorController class
│   ├── transport.py           # Serial port wrapper
│   ├── protocol.py            # Wire protocol constants
│   ├── parsing.py             # Response parsers
│   ├── models.py              # Data models
│   ├── errors.py              # Custom exceptions
│   └── ring_buffer.py         # Thread-safe buffer
├── data_store/                # DataFrame recording layer
│   ├── store.py               # DataStore/DataRecorder
│   └── schemas.py             # DataFrame schema
├── fakes/                     # Test infrastructure
│   └── fake_serial.py         # Device simulator (729 lines)
├── tests/                     # Unit tests (2,729 LOC)
│   ├── test_*_*.py            # pytest test files
│   └── __init__.py
├── pi_hw_tests/               # Hardware validation
│   ├── 00_env_check.py        # Environment check
│   ├── 01-09_*.py             # 10 validation scripts
│   ├── common.py              # Shared utilities
│   ├── out/                   # Test data output
│   └── logs/                  # Test logs
├── examples/                  # Demo scripts
│   ├── freerun_demo.py        # Freerun mode example
│   ├── polled_demo.py         # Polled mode example
│   └── pause_resume_demo.py   # Pause/resume example
├── tools/                     # Diagnostic utilities
│   ├── debug_serial.py        # Serial port debugger
│   └── diagnose_connection.py # Connection diagnostic
├── scripts/                   # Deployment scripts
│   ├── build_local.sh         # Build Docker image
│   ├── save_image.sh          # Export to tarball
│   ├── pi_load_and_run.sh     # Deploy to Pi
│   ├── cleanup_pi.sh          # Clean Pi artifacts
│   └── build_on_pi.sh         # Build on Pi directly
├── docs/                      # Documentation
│   ├── ARCHITECTURE.md        # System design
│   ├── API_REFERENCE.md       # Complete API docs
│   ├── WEBAPP_BUILD.md        # Web UI build guide
│   ├── DATAFRAME_RECORDING.md # DataFrame usage
│   └── *.md                   # Protocol specs, guides
├── Dockerfile                 # Docker image definition
├── pyproject.toml             # Package metadata
├── requirements.txt           # Python dependencies
├── metadata.json              # BlueOS extension metadata
├── manifest.json.template     # BlueOS Kraken template
├── CHANGELOG.md               # Version history
└── README.md                  # This file
```

## API Reference

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for complete endpoint documentation.

### Key Endpoints

**Sensor Control**:
- `POST /sensor/connect` - Connect to sensor
- `POST /sensor/disconnect` - Disconnect from sensor
- `POST /sensor/config` - Get/set configuration
- `POST /sensor/start` - Start acquisition (auto-starts DataRecorder)
- `POST /sensor/stop` - Stop acquisition
- `POST /sensor/pause` - Pause acquisition
- `POST /sensor/resume` - Resume acquisition
- `GET /sensor/status` - Get connection/acquisition status

**Data Access**:
- `GET /sensor/latest` - Get most recent reading
- `GET /sensor/recent?seconds=60` - Get recent readings
- `GET /sensor/stats` - Get DataFrame statistics
- `WS /stream` - WebSocket real-time stream

**Recording**:
- `POST /recording/start` - Start DataRecorder (manual)
- `POST /recording/stop` - Stop DataRecorder
- `GET /recording/export/csv` - Export as CSV
- `GET /recording/export/parquet` - Export as Parquet

**Service**:
- `GET /` - Web UI (React webapp)
- `GET /health` - Health check
- `GET /register_service` - BlueOS service discovery
- `GET /docs` - Swagger UI

## Features

- ✅ **BlueOS Integration** - Automatic sidebar registration, web UI
- ✅ **Docker Deployment** - Optimized for ARMv7 Raspberry Pi 4
- ✅ **Real-time Streaming** - WebSocket and REST polling
- ✅ **DataFrame Recording** - pandas integration with CSV/Parquet export
- ✅ **Dual Acquisition Modes** - Freerun (continuous) and polled (on-demand)
- ✅ **Thread-Safe Design** - Ring buffer, concurrent access
- ✅ **Comprehensive Testing** - Unit tests + hardware validation suite
- ✅ **Type Safety** - Full type hints with mypy strict mode
- ✅ **Production Ready** - Error handling, logging, state management
- ✅ **Firmware Aligned** - Verified against firmware v4.003 source code

## Technical Specifications

**Language**: Python 3.10+
**Framework**: FastAPI 0.104+
**Frontend**: React 18 + TypeScript (Vite)
**Data**: pandas 1.3.5 (ARMv7 compatible)
**Transport**: pyserial 3.5+
**Container**: Docker (linux/arm/v7)
**Platform**: BlueOS on Raspberry Pi 4
**Protocol**: Serial (9600/115200 baud) or Ethernet
**Sensor**: Q-Series firmware 4.003, 2150 mode

## License

MIT License - See LICENSE file for details

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make changes and add tests
4. Run test suite (`pytest`)
5. Commit changes (`git commit -m 'Add amazing feature'`)
6. Push to branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Support

- **Issues**: [GitHub Issues](https://github.com/biospherical/q-sensor-api/issues)
- **Email**: support@biospherical.com
- **Documentation**: [docs/](docs/)
- **Protocol Spec**: [docs/00_Serial_protocol_2150.md](docs/00_Serial_protocol_2150.md)

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and release notes.

## Authors

**Biospherical Instruments Inc.**
- Website: https://www.biospherical.com
- Email: support@biospherical.com

## Acknowledgments

- BlueOS team for extension framework
- FastAPI for async HTTP/WebSocket support
- pandas for DataFrame capabilities
- pytest for comprehensive testing framework
