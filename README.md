# Q-Sensor Library

Production-quality Python library for Biospherical Q-Series sensors running firmware 4.003 in Digital Output, Linear (2150 mode).

## Features

- **Clean Hardware Abstraction**: GUI-free HAL for serial communication
- **Full Protocol Support**: Menu navigation, freerun streaming, polled queries
- **Type-Safe API**: Complete type hints with Python 3.11+
- **Thread-Safe Buffering**: Ring buffer for sensor readings
- **Comprehensive Testing**: Unit tests with device simulator (no hardware required)
- **Production Ready**: Error handling, logging, state management

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

Run all tests (uses FakeSerial, no hardware required):

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
