# Q-Sensor Library

Production-quality Python library for Biospherical Q-Series sensors running firmware 4.003 in Digital Output, Linear (2150 mode).

## Features

- **Clean Hardware Abstraction**: GUI-free HAL for serial communication
- **Full Protocol Support**: Menu navigation, freerun streaming, polled queries
- **DataFrame Recording Layer**: Real-time pandas DataFrame recording with CSV/Parquet export
- **REST API (FastAPI)**: HTTP endpoints and WebSocket streaming for remote access
- **Type-Safe API**: Complete type hints with Python 3.11+
- **Thread-Safe Buffering**: Ring buffer for sensor readings + DataFrame storage
- **Comprehensive Testing**: Unit tests with device simulator (no hardware required)
- **Hardware Validation Suite**: 10 standalone scripts for operational testing on Raspberry Pi
- **Production Ready**: Error handling, logging, state management
- **Firmware-Aligned**: Verified against firmware source code (v4.003)

## Installation

```bash
pip install -e .
```

For development with testing tools:

```bash
pip install -e ".[dev]"
```

## Quick Start

### Freerun Mode (Continuous Streaming)

```python
from q_sensor_lib import SensorController

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
import time
while True:
    readings = controller.read_buffer_snapshot()
    if readings:
        latest = readings[-1]
        print(f"Value: {latest.data['value']:.6f}")
    time.sleep(1.0)

# Stop and disconnect
controller.stop()
controller.disconnect()
```

### Polled Mode (On-Demand Queries)

```python
from q_sensor_lib import SensorController

controller = SensorController()
controller.connect(port="/dev/ttyUSB0")

# Configure for polled mode with TAG 'A'
controller.set_mode("polled", tag="A")
controller.set_averaging(100)
controller.set_adc_rate(125)

# Start acquisition with 2 Hz polling
controller.start_acquisition(poll_hz=2.0)

# Readings are buffered automatically
import time
time.sleep(5.0)

readings = controller.read_buffer_snapshot()
print(f"Collected {len(readings)} readings")

for reading in readings[-10:]:  # Last 10 readings
    print(f"{reading.ts.isoformat()}: {reading.data['value']:.6f}")

controller.disconnect()
```

### DataFrame Recording (Pandas Integration)

The `data_store` module provides real-time recording of sensor readings into pandas DataFrames:

```python
from q_sensor_lib import SensorController
from data_store import DataStore, DataRecorder

# Setup controller
controller = SensorController()
controller.connect("/dev/ttyUSB0")
controller.set_mode("freerun")
controller.set_averaging(125)
controller.set_adc_rate(125)  # 1 Hz sample rate

# Create DataFrame store and recorder
store = DataStore(max_rows=10000)
recorder = DataRecorder(controller, store, poll_interval_s=0.2)

# Start acquisition and recording
controller.start_acquisition()
recorder.start()

# ... let it run for a while ...

# CRITICAL: Stop recorder BEFORE controller to avoid data loss
recorder.stop(flush_format="csv")  # Exports to CSV automatically
controller.stop()
controller.disconnect()

# Access recorded data
df = store.get_dataframe()
print(f"Recorded {len(df)} readings")
print(df.head())

# Get statistics
stats = store.get_stats()
print(f"Sample rate: {stats['est_sample_rate_hz']:.2f} Hz")
print(f"Duration: {stats['duration_s']:.1f} seconds")
```

**Key features:**
- Thread-safe DataFrame storage with configurable max rows
- Automatic NaN handling for optional fields (TempC, Vin)
- Export to CSV or Parquet
- Real-time statistics (sample rate, duration, row count)
- Works identically for freerun and polled modes

See [docs/DATAFRAME_RECORDING.md](docs/DATAFRAME_RECORDING.md) for complete documentation.

### REST API (FastAPI)

The `api` module provides a REST and WebSocket interface:

```bash
# Start API server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Interactive docs at http://localhost:8000/docs
```

**Example API workflow:**

```bash
# Connect
curl -X POST "http://localhost:8000/connect?port=/dev/ttyUSB0&baud=9600"

# Configure
curl -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"averaging": 125, "adc_rate_hz": 125, "mode": "freerun"}'

# Start acquisition & recording
curl -X POST http://localhost:8000/start
curl -X POST http://localhost:8000/recording/start

# Get data
curl http://localhost:8000/latest
curl "http://localhost:8000/recent?seconds=60"

# WebSocket streaming (JavaScript)
const ws = new WebSocket("ws://localhost:8000/stream");
ws.onmessage = (event) => {
    const reading = JSON.parse(event.data);
    console.log(reading.value, reading.timestamp);
};

# Stop & disconnect
curl -X POST "http://localhost:8000/recording/stop?flush=csv"
curl -X POST http://localhost:8000/stop
curl -X POST http://localhost:8000/disconnect
```

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for complete API documentation.

### BlueOS Extension Deployment

The Q-Sensor API can be deployed as a BlueOS extension on Raspberry Pi for integrated marine robotics applications.

#### Features
- **Service Discovery**: Automatically appears in BlueOS sidebar
- **Integrated Web UI**: React webapp accessible via BlueOS interface
- **Docker Deployment**: Optimized for ARMv7 (Raspberry Pi 4)
- **USB Serial Access**: Automatic `/dev/ttyUSB0` device mapping
- **Persistent Storage**: Data stored in `/usr/blueos/userdata/q_sensor/`
- **Auto-start Recording**: DataRecorder starts automatically with acquisition

#### Quick Deployment

```bash
# Build Docker image for Pi (from Mac/Linux)
cd /Users/matthuewalsh/qseries-noise/Q_Sensor_API
docker buildx build --platform linux/arm/v7 -t q-sensor-api:pi --load .

# Transfer to Pi
docker save q-sensor-api:pi -o ~/q-sensor-api-pi.tar
scp ~/q-sensor-api-pi.tar pi@blueos.local:/tmp/

# Deploy on Pi
ssh pi@blueos.local << 'EOF'
  docker load -i /tmp/q-sensor-api-pi.tar
  docker stop q-sensor 2>/dev/null || true
  docker rm q-sensor 2>/dev/null || true
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
EOF
```

#### Access Points

Once deployed, the Q-Sensor API is accessible at:

- **BlueOS Sidebar**: Click "Q-Sensor API" to open integrated webapp
- **Direct Access**: `http://blueos.local:9150/`
- **API Documentation**: `http://blueos.local:9150/docs`
- **Service Metadata**: `http://blueos.local:9150/register_service`

#### Configuration

Environment variables (set in docker run command):

- `SERIAL_PORT` - Serial device path (default: `/dev/ttyUSB0`)
- `SERIAL_BAUD` - Baud rate (default: `9600`)
- `LOG_LEVEL` - Logging level: DEBUG, INFO, WARNING, ERROR (default: `INFO`)
- `CORS_ORIGINS` - Comma-separated CORS origins (default includes `blueos.local`)

#### Web UI Build

The integrated webapp is built from the Q_Web repository. To update:

```bash
# Build React webapp
cd /Users/matthuewalsh/qseries-noise/Q_Web
npm run build

# Copy to API static directory
cp -r dist/* ../Q_Sensor_API/api/static/webapp/

# Rebuild Docker image (see above)
```

See [docs/WEBAPP_BUILD.md](docs/WEBAPP_BUILD.md) for detailed build documentation.

#### Deployment Scripts

The `scripts/` directory contains helper scripts:

- `build_local.sh` - Build Docker image locally
- `save_image.sh` - Export image to tarball
- `pi_load_and_run.sh` - Deploy to Pi
- `cleanup_pi.sh` - Remove old containers/images
- `build_on_pi.sh` - Build directly on Pi (slow, ~100 min)

#### Troubleshooting

**Container not seeing sensor:**
```bash
# Verify USB device exists
ssh pi@blueos.local 'ls -la /dev/ttyUSB*'

# Check container device mapping
ssh pi@blueos.local 'docker exec q-sensor ls -la /dev/ttyUSB*'

# Verify dialout group membership
ssh pi@blueos.local 'docker exec q-sensor groups'
```

**Webapp not loading:**
```bash
# Check container logs
ssh pi@blueos.local 'docker logs --tail 50 q-sensor'

# Verify static files exist
ssh pi@blueos.local 'docker exec q-sensor ls -la /app/api/static/webapp/'

# Test direct API access
curl http://blueos.local:9150/health
```

**BlueOS not detecting service:**
- Wait 5-10 seconds for BlueOS Helper scan
- Refresh BlueOS page
- Verify `/register_service` endpoint returns valid JSON:
  ```bash
  curl http://blueos.local:9150/register_service
  ```

See [docs/API_RUNBOOK_PI.md](docs/API_RUNBOOK_PI.md) for detailed deployment procedures.

### Pause and Resume

```python
controller = SensorController()
controller.connect(port="/dev/ttyUSB0")
controller.set_mode("freerun")
controller.start_acquisition()

# Collect data for 10 seconds
time.sleep(10.0)

# Pause acquisition (enters menu)
controller.pause()

# Analyze data
readings = controller.read_buffer_snapshot()
print(f"Collected {len(readings)} readings")

# Resume acquisition
controller.resume()

# Continue collecting
time.sleep(10.0)

controller.disconnect()
```

## API Reference

### SensorController

Main class for controlling the sensor.

#### Connection

```python
controller.connect(port: str, baud: int = 9600) -> None
```
Opens serial port, enters config menu, reads current configuration.

```python
controller.disconnect() -> None
```
Stops acquisition, closes port, cleans up resources.

#### Configuration

```python
controller.get_config() -> SensorConfig
```
Returns current configuration (must be in CONFIG_MENU state).

```python
controller.set_averaging(n: int) -> SensorConfig
```
Set number of readings to average (1-65535).

```python
controller.set_adc_rate(rate_hz: int) -> SensorConfig
```
Set ADC sample rate. Valid: {4, 8, 16, 33, 62, 125, 250, 500}.

```python
controller.set_mode(
    mode: Literal["freerun", "polled"],
    tag: Optional[str] = None
) -> SensorConfig
```
Set operating mode. TAG required for polled (single uppercase A-Z).

#### Acquisition Control

```python
controller.start_acquisition(poll_hz: float = 1.0) -> None
```
Exit menu and start data acquisition. `poll_hz` used only in polled mode.

```python
controller.pause() -> None
```
Pause acquisition and enter menu (from freerun/polled).

```python
controller.resume() -> None
```
Resume acquisition from paused state.

```python
controller.stop() -> None
```
Stop acquisition and return to CONFIG_MENU.

#### Data Access

```python
controller.read_buffer_snapshot() -> List[Reading]
```
Get copy of all buffered readings (thread-safe).

```python
controller.clear_buffer() -> None
```
Clear all buffered readings.

#### Properties

```python
controller.state -> ConnectionState
```
Current state: DISCONNECTED, CONFIG_MENU, ACQ_FREERUN, ACQ_POLLED, PAUSED.

```python
controller.sensor_id -> str
```
Sensor serial number.

### Data Models

#### SensorConfig

```python
@dataclass
class SensorConfig:
    averaging: int              # 1-65535
    adc_rate_hz: int           # {4,8,16,33,62,125,250,500}
    mode: Literal["freerun", "polled"]
    tag: Optional[str]         # A-Z for polled
    include_temp: bool
    include_vin: bool
    preamble: str
    calfactor: float
    serial_number: str
    firmware_version: str
```

#### Reading

```python
@dataclass
class Reading:
    ts: datetime               # UTC timestamp
    sensor_id: str            # Sensor serial number
    mode: Literal["freerun", "polled"]
    data: Dict[str, float]    # Keys: "value", "TempC"?, "Vin"?
```

Data dictionary always contains `"value"`. Optional keys:
- `"TempC"`: Temperature in Celsius (if enabled in config)
- `"Vin"`: Line voltage in volts (if enabled in config)

## Advanced Usage

### Custom Buffer Size

```python
# Create controller with larger buffer (default is 1000)
controller = SensorController(buffer_size=5000)
```

### High-Speed Polled Acquisition

```python
controller.set_averaging(10)    # Fast averaging
controller.set_adc_rate(125)    # 125 Hz ADC
controller.set_mode("polled", tag="A")

# Poll at 10 Hz (every 100ms)
controller.start_acquisition(poll_hz=10.0)
```

### Error Handling

```python
from q_sensor_lib.errors import (
    MenuTimeout,
    InvalidConfigValue,
    SerialIOError,
)

try:
    controller.connect(port="/dev/ttyUSB0")
except SerialIOError as e:
    print(f"Failed to connect: {e}")

try:
    controller.set_averaging(100000)  # Too large
except InvalidConfigValue as e:
    print(f"Invalid config: {e}")

try:
    controller.start_acquisition()
except MenuTimeout as e:
    print(f"Device not responding: {e}")
```

### Logging

Enable debug logging to see protocol details:

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

controller = SensorController()
controller.connect(port="/dev/ttyUSB0")
# Detailed logs of menu navigation, commands, responses
```

## Testing

### Unit Tests (No Hardware Required)

Run all unit tests with FakeSerial simulator:

```bash
pytest
```

Run with coverage:

```bash
pytest --cov=q_sensor_lib --cov-report=html
```

Run specific test file:

```bash
pytest tests/test_freerun_stream.py -v
```

### Hardware Validation Tests (Raspberry Pi)

For operational validation on real hardware, use the standalone test scripts in `pi_hw_tests/`:

```bash
# Environment check
python3 pi_hw_tests/00_env_check.py

# Connection test
python3 pi_hw_tests/01_connect_and_identify.py --port /dev/ttyUSB0

# Freerun smoke test (10s acquisition)
python3 pi_hw_tests/02_freerun_smoke.py --port /dev/ttyUSB0

# Run all tests
for script in pi_hw_tests/0*.py; do
    python3 "$script" --port /dev/ttyUSB0 || break
done
```

All scripts support `--fake` mode for dry-run testing without hardware:

```bash
python3 pi_hw_tests/02_freerun_smoke.py --fake
```

See [pi_hw_tests/README.md](pi_hw_tests/README.md) for complete documentation on:
- Test coverage and expected metrics
- Troubleshooting (permissions, ports, menu timeouts)
- Advanced usage (custom durations, burn-in tests, export validation)
- Output locations (`./pi_hw_tests/out/` for data, `./pi_hw_tests/logs/` for logs)

## Protocol Reference

This library implements the complete serial protocol for firmware 4.003 as documented in `docs/00_Serial_protocol_2150.md`.

Key protocol details:
- **Input terminator**: CR (0x0D)
- **Output terminator**: CRLF (0x0D 0x0A)
- **Menu entry**: ESC (0x1B) or '?'
- **Exit menu**: 'X' (triggers device reset)
- **Polled init**: `*<TAG>Q000!`
- **Polled query**: `><TAG>`

## Architecture

```
q_sensor_lib/
   __init__.py          # Public API
   models.py            # Data classes (SensorConfig, Reading)
   errors.py            # Custom exceptions
   protocol.py          # Wire protocol constants and patterns
   parsing.py           # Pure functions for parsing responses
   transport.py         # Serial port wrapper
   ring_buffer.py       # Thread-safe FIFO buffer
   controller.py        # Main controller with state machine

fakes/
   fake_serial.py       # Device simulator for testing

tests/
   test_connect_and_menu.py
   test_config_set_get.py
   test_freerun_stream.py
   test_polled_sequence.py
   test_pause_resume_stop.py
   test_error_paths.py
```

## Thread Safety

- **Ring buffer**: Fully thread-safe (uses Lock)
- **Reader threads**: Single background thread per acquisition mode
- **Controller state**: Protected by state lock for transitions
- **Data access**: `read_buffer_snapshot()` returns a copy (safe to iterate)

## Performance Considerations

### Freerun Mode
- Achieves native sensor sample rate (e.g., 125Hz/125avg = 1 Hz)
- Reader thread parses lines as fast as they arrive
- Ring buffer automatically discards oldest when full

### Polled Mode
- Poll rate configurable 1-15 Hz (hardware limit ~125 Hz / averaging)
- Each query blocks briefly for response (~10-50ms typical)
- Slower rates (<1 Hz) may miss occasional samples due to timing variance

### Buffer Sizing
- Default 1000 readings = ~16 minutes at 1 Hz
- Increase for longer unattended runs
- Decrease for memory-constrained systems

## Troubleshooting

### Device Not Responding

```python
# Check serial port permissions
# Linux: sudo usermod -a -G dialout $USER

# Verify baud rate matches device config
controller.connect(port="/dev/ttyUSB0", baud=9600)

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
```

### No Readings in Polled Mode

```python
# Ensure TAG matches device config
config = controller.get_config()
print(f"Device TAG: {config.tag}")

# Verify polled init sent (check logs)
# Should see: "Sent polled init command: *AQ000!"

# Try longer wait for averaging to fill
import time
time.sleep(config.sample_period_s + 1.0)
```

### Readings Stop Arriving

```python
# Check connection state
print(controller.state)

# Verify device didn't reset unexpectedly
# (Look for power-on banner in logs)

# Try pause/resume to recover
controller.pause()
controller.resume()
```

## Changelog

### 0.1.0 (2025-01-XX)
- Initial release
- Freerun and polled mode support
- Complete menu navigation
- Comprehensive test suite with FakeSerial
- Type-safe API with full annotations
