# DataFrame Recording Layer for Q-Sensor Data Acquisition

## Overview

This document describes the DataFrame recording layer for the Q-Sensor Python library. This layer provides real-time recording of sensor readings into a pandas DataFrame while maintaining firmware-level correctness for both **freerun** and **polled** acquisition modes.

The recording layer consists of three main components:

1. **Schema Normalization** (`data_store/schemas.py`) - Ensures consistent DataFrame structure
2. **DataStore** (`data_store/store.py`) - Thread-safe in-memory DataFrame storage
3. **DataRecorder** (`data_store/store.py`) - Background polling thread that captures readings

---

## Acquisition Modes and Firmware Behavior

Understanding the firmware-level differences between freerun and polled modes is critical for correct usage.

### Freerun Mode

**Firmware behavior** (from [2150REV4.003.bas](../2150REV4.003.bas) lines 512-542):

- Device continuously streams data lines to serial port
- No query command needed
- Output format: `<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF
- Sample rate: `ADC_rate / averaging` (e.g., 125 Hz / 125 avg = **1 Hz**)
- Typical freerun rate with default config (125 Hz, 125 averaging): **~15 Hz**

**Python behavior:**

- `SensorController` continuously reads lines from serial port in background thread
- Each line is parsed and appended to controller's ring buffer as a `Reading` instance
- `DataRecorder` polls this buffer every 200ms and appends new readings to `DataStore`

**Example output rate:**
```
ADC rate: 125 Hz
Averaging: 125 samples
Effective sample rate: 125 / 125 = 1 Hz
```

For high-speed logging, use 125 Hz / 12 avg = ~10 Hz

### Polled Mode

**Firmware behavior** (from [2150REV4.003.bas](../2150REV4.003.bas) lines 547-645):

- Device is **silent** until queried
- **Two-step query process** (critical!):
  1. **Init command**: `*<TAG>Q000!` CR - Starts ADC averaging (send once after startup)
  2. **Query command**: `><TAG>` CR - Triggers data transmission (send for each sample)
- Response format: `<TAG>,<preamble><calibrated_value>[,<temp>][,<Vin>]` CR LF
- Only one line per query

**Python behavior:**

- `SensorController` sends init command on `start_acquisition()`
- Background thread sends query at `poll_hz` rate (default 1 Hz)
- Each response is parsed and buffered
- `DataRecorder` polls buffer every 200ms (same as freerun)

**Key difference from freerun:**
- In polled mode, data appears in controller buffer **only when controller queries** (user-controlled rate)
- In freerun mode, data appears in controller buffer **continuously** (firmware-controlled rate)

---

## Schema and Data Normalization

### DataFrame Schema

All recorded data conforms to a fixed schema defined in `data_store/schemas.py`:

```python
SCHEMA = {
    "timestamp": str,   # UTC ISO 8601 format
    "sensor_id": str,   # Sensor serial number
    "mode": str,        # "freerun" or "polled"
    "value": float,     # Main calibrated sensor reading (always present)
    "TempC": float,     # Optional temperature in Celsius
    "Vin": float,       # Optional line voltage
}
```

### Handling Optional Fields (TempC and Vin)

**Firmware behavior** (from [2150REV4.003.bas](../2150REV4.003.bas) lines 928-935):

- Temperature output controlled by `TempOutput` flag (menu option "O")
- Voltage output controlled by `LineVoutput` flag (menu option "O")
- If flag is 0, field is **omitted** from serial output
- If flag is 1, field is **appended** as `, <value>`

**Python normalization:**

- The `reading_to_row()` function ensures all schema keys are always present
- Missing optional fields are set to `None`, which becomes `NaN` in pandas DataFrame
- This ensures consistent DataFrame structure regardless of sensor configuration

**Example:**

```python
# Reading with TempC, without Vin
Reading(data={"value": 123.4, "TempC": 21.5})

# Becomes DataFrame row:
{"timestamp": "2025-01-15T12:00:00+00:00",
 "sensor_id": "Q12345",
 "mode": "freerun",
 "value": 123.4,
 "TempC": 21.5,
 "Vin": NaN}  # <-- NaN inserted for missing field
```

---

## Usage Guide

### Basic Workflow

#### 1. Freerun Mode

```python
from q_sensor_lib.controller import SensorController
from data_store import DataStore, DataRecorder

# Connect and configure sensor
ctl = SensorController()
ctl.connect("/dev/ttyUSB0", baud=9600)

ctl.set_mode("freerun")
ctl.set_averaging(125)
ctl.set_adc_rate(125)  # 125 Hz / 125 avg = 1 Hz sample rate

# Create data store and recorder
store = DataStore(max_rows=10000)
recorder = DataRecorder(ctl, store, poll_interval_s=0.2)

# Start acquisition then recorder
ctl.start_acquisition()  # Device starts streaming
recorder.start()          # Recorder starts polling buffer

# ... acquisition runs in background ...

# CRITICAL: Stop in correct order
recorder.stop(flush_format="csv")  # Stop recorder first, flush data
ctl.stop()                          # Stop controller second
ctl.disconnect()
```

#### 2. Polled Mode

```python
from q_sensor_lib.controller import SensorController
from data_store import DataStore, DataRecorder

# Connect and configure sensor
ctl = SensorController()
ctl.connect("/dev/ttyUSB0", baud=9600)

ctl.set_mode("polled", tag="A")
ctl.set_averaging(125)
ctl.set_adc_rate(125)

# Create data store and recorder
store = DataStore()
recorder = DataRecorder(ctl, store, poll_interval_s=0.2)

# Start acquisition with 2 Hz polling rate
ctl.start_acquisition(poll_hz=2.0)  # Controller queries at 2 Hz
recorder.start()                     # Recorder polls buffer every 200ms

# ... acquisition runs in background ...

# CRITICAL: Stop in correct order
recorder.stop(flush_format="parquet")  # Stop recorder first
ctl.stop()                              # Stop controller second
ctl.disconnect()
```

---

## DataStore API

### Initialization

```python
store = DataStore(
    max_rows=100000,              # Trim to most recent N rows
    auto_flush_interval_s=60.0,   # Optional: auto-flush every 60s
    auto_flush_format="csv",      # Format for auto-flush
    auto_flush_path="data.csv"    # Optional: custom flush path
)
```

### Data Access

```python
# Get entire DataFrame (copy)
df = store.get_dataframe()

# Get recent data (last 60 seconds)
recent = store.get_recent(seconds=60)

# Get latest reading as dict
latest = store.get_latest()

# Get statistics
stats = store.get_stats()
# Returns:
# {
#   "row_count": 1500,
#   "start_time": "2025-01-15T12:00:00+00:00",
#   "end_time": "2025-01-15T12:25:00+00:00",
#   "duration_s": 1500.0,
#   "est_sample_rate_hz": 1.0
# }
```

### Export

```python
# Export to CSV
path = store.export_csv("sensor_data.csv")

# Export to Parquet (requires pyarrow)
path = store.export_parquet("sensor_data.parquet")

# Generic flush
path = store.flush_to_disk(format="csv")
```

---

## DataRecorder API

### Initialization and Control

```python
recorder = DataRecorder(
    controller=ctl,
    store=store,
    poll_interval_s=0.2  # Poll controller buffer every 200ms
)

# Start recording
recorder.start()

# Check if running
if recorder.is_running():
    print("Recording in progress...")

# Stop and optionally flush
flush_path = recorder.stop(flush_format="csv")
```

### How It Works

The recorder operates a background thread that:

1. Every 200ms, calls `controller.read_buffer_snapshot()`
2. Filters readings to those with `timestamp > last_seen_ts`
3. Appends new readings to `DataStore` via `store.append_readings()`
4. Updates `last_seen_ts` to the newest reading's timestamp

**This design works identically for both freerun and polled modes:**

- **Freerun**: Controller buffer continuously fills (~15 Hz typical)
- **Polled**: Controller buffer fills only when controller polls (user-controlled rate)

The recorder simply ingests whatever is available in the controller buffer, regardless of acquisition mode.

---

## Critical Shutdown Order

**⚠️ IMPORTANT:** The recorder **must** stop and flush data **BEFORE** the controller stops.

### Correct Order:

```python
recorder.stop(flush_format="csv")  # 1. Stop recorder, flush data
ctl.stop()                          # 2. Stop controller
ctl.disconnect()                    # 3. Disconnect
```

### Why This Matters:

From firmware analysis ([2150REV4.003.bas](../2150REV4.003.bas)):

- When controller sends ESC to enter menu (during `stop()`), firmware **disables interrupts** (lines 712-713)
- Any in-progress data stream is **interrupted**
- Controller's ring buffer is **cleared**

**If recorder stops after controller:**
- Buffered readings in controller are lost
- Recorder cannot retrieve them

**If recorder stops before controller:**
- Recorder retrieves all buffered readings
- Flush writes them to disk
- No data loss

---

## Timing and Sample Rates

### Freerun Mode Timing

**Firmware output rate:**
```
sample_rate_hz = ADC_rate / averaging
```

**Example configurations:**

| ADC Rate | Averaging | Output Rate | Use Case |
|----------|-----------|-------------|----------|
| 125 Hz   | 125       | 1 Hz        | Standard logging |
| 125 Hz   | 12        | ~10 Hz      | High-speed capture |
| 62 Hz    | 62        | 1 Hz        | Low-noise alternative |
| 4 Hz     | 1         | 4 Hz        | Fast response, high noise |

**Python recorder timing:**

- Recorder polls controller buffer every **200ms** (5 Hz)
- In freerun at 1 Hz, recorder polls 5× faster than data arrives → minimal latency
- In freerun at 10 Hz, recorder polls 2× slower than data arrives → buffering compensates

### Polled Mode Timing

**Controller query rate:**

```python
ctl.start_acquisition(poll_hz=2.0)  # Query 2 times per second
```

**Recommended rates:**

- **1 Hz**: Standard polled logging
- **2-5 Hz**: High-frequency polled
- **> 10 Hz**: Not recommended (use freerun instead)

**Python recorder timing:**

- Recorder polls controller buffer every **200ms** regardless of `poll_hz`
- For `poll_hz=1.0`, recorder polls 5× faster than data arrives
- For `poll_hz=5.0`, recorder polls at same rate as data arrives

---

## Firmware Alignment Notes

### Data Format Verification

The DataFrame recording layer has been **verified against firmware source code**:

1. **Field order** matches firmware output (value, TempC, Vin) - [lines 926-935](../2150REV4.003.bas)
2. **Temperature calculation** per firmware formula: `(V - 1.8639) / (-0.01177)` - [line 1045-1046](../2150REV4.003.bas)
3. **Voltage scaling** per firmware formula: `V / 0.23` - [line 1053](../2150REV4.003.bas)
4. **Decimal formatting** matches firmware `Dec2` (temp) and `Dec3` (voltage) - [lines 1047, 1054](../2150REV4.003.bas)

### Mode-Specific Behavior

**Freerun:**
- Firmware enters `StartFreerunSampling` loop - [line 512](../2150REV4.003.bas)
- Outputs occur at `ADC_rate / adcToAverage` - verified
- No query command needed - verified

**Polled:**
- Firmware waits in `While Queried = 0` loop - [line 626](../2150REV4.003.bas)
- Requires `*<TAG>Q000!` init before first query - [lines 592-596](../2150REV4.003.bas)
- Responds to `><TAG>` query - [lines 804-810](../2150REV4.003.bas)
- TAG validation is **strict** (must match EEPROM-stored TAG)

### Behavioral Corrections

No behavioral corrections were needed. The existing `SensorController` implementation already aligns with firmware logic:

- Controller sends init command in polled mode - [controller.py:736](../q_sensor_lib/controller.py)
- Controller waits for averaging period before first query - [controller.py:741-744](../q_sensor_lib/controller.py)
- Parser handles optional TempC/Vin fields correctly - [parsing.py](../q_sensor_lib/parsing.py)

---

## Testing

### Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/test_data_recorder_basic.py -v
```

### Test Coverage

The test suite (`tests/test_data_recorder_basic.py`) includes:

1. **Schema normalization tests:**
   - All schema keys present
   - NaN handling for missing optional fields
   - Timezone conversion (naive → UTC)

2. **DataStore tests:**
   - Thread-safe appends
   - Max rows trimming
   - Recent window filtering
   - Statistics calculation
   - CSV/Parquet export

3. **DataRecorder tests:**
   - Freerun simulation (~15 Hz)
   - Polled simulation (1-2 Hz)
   - Timestamp filtering (no duplicates)
   - Empty buffer handling
   - Stop-with-flush

4. **Integration tests:**
   - End-to-end freerun workflow (2 seconds @ 15 Hz)
   - End-to-end polled workflow (10 queries @ 2 Hz)

**No physical hardware required** - tests use synthetic data and mock controllers.

---

## Performance Considerations

### Memory Usage

**DataStore memory:**
```
DataFrame size ≈ row_count × 80 bytes/row
```

Example: 10,000 rows ≈ 800 KB

**Controller buffer size:**
- Default: 10,000 readings (configurable via `buffer_size` parameter)
- With freerun at 15 Hz, buffer holds ~11 minutes of data

### CPU Usage

**Recorder thread overhead:**
- Polls every 200ms → 5 Hz loop
- Minimal CPU: timestamp comparison + DataFrame append
- Typical CPU usage: < 1% on modern hardware

**DataFrame operations:**
- `append_readings()`: O(n) where n = number of new readings
- `get_recent()`: O(m) where m = total rows (timestamp filtering)
- `export_csv()`: O(m) write to disk

### Recommended Configuration

For continuous long-term logging:

```python
store = DataStore(
    max_rows=50000,              # ~1 hour at 15 Hz
    auto_flush_interval_s=300,   # Flush every 5 minutes
    auto_flush_format="parquet"  # Smaller files than CSV
)
```

For short-term high-speed capture:

```python
store = DataStore(
    max_rows=100000,   # Keep all in memory
)
# Manual flush at end
recorder.stop(flush_format="csv")
```

---

## Troubleshooting

### Issue: Recorder captures no data

**Cause:** Controller not in acquisition mode

**Solution:**
```python
# Ensure start_acquisition() called before recorder.start()
ctl.start_acquisition()  # Must come first
recorder.start()          # Then start recorder
```

### Issue: Missing TempC/Vin columns show NaN

**Cause:** Sensor configured to omit optional fields

**Verification:**
```python
config = ctl.get_config()
print(config.include_temp)  # False → TempC will be NaN
print(config.include_vin)   # False → Vin will be NaN
```

**Solution (if you want these fields):**
```python
# Enter menu and configure output fields via menu option "O"
# Or use FakeSerial to set TempOutput/LineVoutput flags
```

### Issue: Sample rate lower than expected

**Freerun mode:**

Check averaging and ADC rate:
```python
config = ctl.get_config()
expected_rate = config.adc_rate_hz / config.averaging
print(f"Expected: {expected_rate} Hz")

stats = store.get_stats()
print(f"Actual: {stats['est_sample_rate_hz']:.2f} Hz")
```

**Polled mode:**

Check poll_hz parameter:
```python
# Ensure poll_hz matches desired rate
ctl.start_acquisition(poll_hz=2.0)  # 2 Hz queries
```

### Issue: Data loss on stop

**Cause:** Stopped controller before recorder

**Solution:**
```python
# Always stop in this order:
recorder.stop(flush_format="csv")  # 1. Recorder
ctl.stop()                          # 2. Controller
```

---

## Future API Layer

This DataFrame recording layer is designed to integrate cleanly with a future REST API:

```python
# Future FastAPI endpoint (example)
@app.get("/api/readings/recent")
def get_recent_readings(seconds: int = 60):
    recent_df = store.get_recent(seconds=seconds)
    return recent_df.to_dict(orient="records")

@app.get("/api/stats")
def get_stats():
    return store.get_stats()
```

The normalized schema ensures consistent API responses regardless of sensor configuration.

---

## References

- **Firmware source:** [/Users/matthuewalsh/2150REV4.003.bas](../2150REV4.003.bas)
- **Serial protocol:** [docs/00_Serial_protocol_2150.md](./00_Serial_protocol_2150.md)
- **Controller implementation:** [q_sensor_lib/controller.py](../q_sensor_lib/controller.py)
- **Parser implementation:** [q_sensor_lib/parsing.py](../q_sensor_lib/parsing.py)
- **Data models:** [q_sensor_lib/models.py](../q_sensor_lib/models.py)

---

**Document Version:** 1.0
**Last Updated:** 2025-11-01
**Firmware Version:** 4.003 (2150 Mode - Digital Output, Linear)
