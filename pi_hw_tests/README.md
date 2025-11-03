# Hardware Validation Tests

Operational validation scripts for Q-Sensor library on real hardware (Raspberry Pi + Biospherical Q-Series sensor).

## Overview

This directory contains 10 standalone Python scripts that test the Q-Sensor library against real hardware. Each script can run with physical hardware via `/dev/ttyUSB0` or with `--fake` mode using `FakeSerial` for dry-run testing.

## Quick Start (Raspberry Pi)

### 1. Environment Check

Before running any tests, verify your environment:

```bash
python3 pi_hw_tests/00_env_check.py
```

This will check:
- Python version (≥3.11)
- Required packages installed
- Serial port availability
- Port permissions

If you see permission errors, add your user to the `dialout` group:

```bash
sudo usermod -a -G dialout $USER
# Log out and back in for this to take effect
```

### 2. Verify Port

List available serial ports:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
```

Update `--port` argument if your sensor is on a different port (e.g., `/dev/ttyACM0`).

### 3. Run Tests

Each script accepts common arguments:
- `--port`: Serial port path (default `/dev/ttyUSB0`)
- `--baud`: Baud rate (default `9600`)
- `--fake`: Use FakeSerial instead of real hardware

## Test Scripts

### 00_env_check.py - Environment Validation

**Purpose:** Verify Python environment, packages, and serial port permissions.

**Run:**
```bash
python3 pi_hw_tests/00_env_check.py
```

**Expected output:**
```
============================================================
Environment Check
============================================================

✓ Python 3.11.x detected
✓ Package 'pandas' available
✓ Package 'pyserial' available
...
✓ Found serial ports: /dev/ttyUSB0
✓ Port /dev/ttyUSB0 is readable/writable

[PASS] Environment check passed
```

**Failure indicators:**
- Python version < 3.11
- Missing packages
- No serial ports found
- Permission denied on ports

---

### 01_connect_and_identify.py - Connection Test

**Purpose:** Connect to sensor, retrieve ID, verify menu access, disconnect.

**Run:**
```bash
python3 pi_hw_tests/01_connect_and_identify.py --port /dev/ttyUSB0

# Or with FakeSerial:
python3 pi_hw_tests/01_connect_and_identify.py --fake
```

**Expected metrics:**
- `sensor_id`: e.g., `"2150REV4.003"`
- `mode`: e.g., `"freerun"` or `"polled"`
- `averaging`: e.g., `125`
- `adc_rate_hz`: e.g., `125`

**Failure indicators:**
- Cannot open serial port (permission or hardware issue)
- Menu timeout (sensor not responding)
- Sensor ID not retrieved

---

### 02_freerun_smoke.py - Freerun Acquisition Test

**Purpose:** Configure freerun mode, acquire data for 10s, verify row count and sample rate.

**Run:**
```bash
python3 pi_hw_tests/02_freerun_smoke.py --port /dev/ttyUSB0 --duration 10

# Or with FakeSerial:
python3 pi_hw_tests/02_freerun_smoke.py --fake --duration 10
```

**Expected metrics:**
- `total_rows`: 120-180 for real hardware (10s × ~15 Hz), 80-120 for FakeSerial
- `p95_interval_ms`: 40-90 ms (real hardware at 125/125 config)
- `csv_export_path`: Path to exported CSV in `./out/`

**Failure indicators:**
- Row count < 120 (real) or < 80 (fake)
- P95 interval > 150 ms (excessive jitter)
- No CSV exported

---

### 03_polled_smoke.py - Polled Mode Test

**Purpose:** Configure polled mode, poll at 2 Hz for 10s, verify row count.

**Run:**
```bash
python3 pi_hw_tests/03_polled_smoke.py --port /dev/ttyUSB0 --duration 10 --poll-hz 2 --tag A

# Or with FakeSerial:
python3 pi_hw_tests/03_polled_smoke.py --fake --duration 10 --poll-hz 2 --tag A
```

**Expected metrics:**
- `total_rows`: 18-22 (10s × 2 Hz ± 10%)
- `mode_test`: `"PASS"` (all rows have `mode="polled"`)
- `csv_export_path`: Path to exported CSV

**Failure indicators:**
- Row count outside 18-22 range
- Some rows have `mode!="polled"`
- No CSV exported

---

### 04_menu_consistency.py - Menu Command Validation

**Purpose:** Test menu commands (set_averaging, set_adc_rate, set_mode) and verify responses.

**Run:**
```bash
python3 pi_hw_tests/04_menu_consistency.py --port /dev/ttyUSB0

# Or with FakeSerial:
python3 pi_hw_tests/04_menu_consistency.py --fake
```

**Expected metrics:**
- `averaging_test`: `"PASS"` (averaging set to 100)
- `adc_rate_test`: `"PASS"` (ADC rate set to 62 Hz)
- `mode_test`: `"PASS"` (mode set to freerun)

**Failure indicators:**
- Config values don't match requested values
- Menu timeout
- Unexpected firmware responses (check logs in `./logs/`)

---

### 05_temp_vin_presence.py - Optional Field Validation

**Purpose:** Verify TempC/Vin fields are handled correctly (present or NaN based on firmware flags).

**Run:**
```bash
python3 pi_hw_tests/05_temp_vin_presence.py --port /dev/ttyUSB0 --duration 5

# Or with FakeSerial (TempC/Vin always present):
python3 pi_hw_tests/05_temp_vin_presence.py --fake --duration 5
```

**Expected metrics (FakeSerial):**
- `tempc_present_pct`: 100.0 (FakeSerial always provides TempC)
- `vin_present_pct`: 100.0 (FakeSerial always provides Vin)
- `tempc_test`: `"PASS"`
- `vin_test`: `"PASS"`
- `sample_tempc`: 21.5
- `sample_vin`: 12.0

**Expected metrics (Real hardware, depends on firmware):**
- If firmware provides TempC/Vin: both present in ≥90% of rows
- If firmware omits TempC/Vin: both NaN in ≥90% of rows

**Failure indicators:**
- Inconsistent TempC/Vin presence (some rows have it, others don't)
- Schema not normalized (missing columns)

---

### 06_pause_resume_durability.py - State Transition Test

**Purpose:** Test pause/resume cycles (ESC to menu, resume to acquisition).

**Run:**
```bash
python3 pi_hw_tests/06_pause_resume_durability.py --port /dev/ttyUSB0 --cycles 3

# Or with FakeSerial:
python3 pi_hw_tests/06_pause_resume_durability.py --fake --cycles 3
```

**Expected metrics:**
- `cycles_passed`: 3 (all cycles successful)
- `cycles_total`: 3
- `initial_samples`: ≥5
- `final_rows`: ≥10

**Failure indicators:**
- Not all cycles pass
- No samples after resume
- State transitions fail (not CONFIG_MENU after pause, not ACQ_FREERUN after resume)

---

### 07_disconnect_reconnect.py - Connection Lifecycle Test

**Purpose:** Connect, acquire, disconnect, reconnect, verify sensor ID persists.

**Run:**
```bash
python3 pi_hw_tests/07_disconnect_reconnect.py --port /dev/ttyUSB0

# Or with FakeSerial:
python3 pi_hw_tests/07_disconnect_reconnect.py --fake
```

**Expected metrics:**
- `sensor_id_first`: e.g., `"2150REV4.003"`
- `sensor_id_second`: Same as `sensor_id_first`
- `sensor_id_match`: `"MATCH"`
- `samples_first_connection`: ≥5
- `samples_second_connection`: ≥5
- `final_rows`: ≥10

**Failure indicators:**
- Sensor ID mismatch (unless you physically swapped sensors)
- No samples after reconnect
- Disconnection or reconnection fails

---

### 08_burn_in_30min.py - Long-Duration Stability Test

**Purpose:** Run freerun acquisition for 30 minutes (or custom duration), monitor stability.

**Run (30 minutes - default):**
```bash
python3 pi_hw_tests/08_burn_in_30min.py --port /dev/ttyUSB0
```

**Run (1 minute dry-run with FakeSerial):**
```bash
python3 pi_hw_tests/08_burn_in_30min.py --fake --duration 60
```

**Expected metrics (30 min at ~15 Hz):**
- `total_rows`: ~27000 (30 min × 60 s/min × 15 Hz)
- `expected_rows`: Calculated based on sample period
- `row_count_status`: `"OK"` (within ±20%)
- `avg_sample_rate_hz`: ~15.0
- `median_gap_s`: ~0.067 (1/15 Hz)
- `max_gap_s`: <0.5s
- `gap_status`: `"OK"`

**Failure indicators:**
- Row count < 80% of expected
- Large gaps (>5× expected period)
- Recorder stops unexpectedly
- Sample rate drifts significantly over time

**Note:** This is a long-running test. For quick validation, use `--duration 60` (1 minute).

---

### 09_export_throughput.py - Export Performance Test

**Purpose:** Acquire large dataset (10k-50k rows), test CSV/Parquet export performance.

**Run (10k rows - default):**
```bash
python3 pi_hw_tests/09_export_throughput.py --port /dev/ttyUSB0 --target-rows 10000
```

**Run (1k rows with FakeSerial):**
```bash
python3 pi_hw_tests/09_export_throughput.py --fake --target-rows 1000
```

**Expected metrics:**
- `rows_collected`: ≥ target_rows × 0.8
- `csv_export_time_s`: <10s
- `parquet_export_time_s`: <5s
- `csv_size_mb`: Size of CSV file
- `parquet_size_mb`: Size of Parquet file
- `csv_throughput_rows_per_s`: Rows exported per second
- `parquet_throughput_rows_per_s`: Rows exported per second
- `compression_ratio`: CSV size / Parquet size (typically 3-5x)

**Failure indicators:**
- Export times exceed limits
- Exported file row count doesn't match DataFrame
- Files not created or corrupted

---

## Output Locations

All test scripts write outputs to:

- **CSV exports:** `./pi_hw_tests/out/`
- **Logs:** `./pi_hw_tests/logs/`

Each run creates timestamped files (e.g., `02_freerun_smoke_20231118_143052.csv`).

## Common Patterns

### Test Result Format

All scripts output results in this format:

```
============================================================
Test Result
============================================================

Status: PASS
Message: All menu commands executed successfully

Metrics:
  averaging_test: PASS
  adc_rate_test: PASS
  mode_test: PASS

============================================================
```

### Exit Codes

- `0`: Test passed
- `1`: Test failed

You can chain tests in a shell script:

```bash
#!/bin/bash
set -e  # Exit on first failure

python3 pi_hw_tests/00_env_check.py
python3 pi_hw_tests/01_connect_and_identify.py --port /dev/ttyUSB0
python3 pi_hw_tests/02_freerun_smoke.py --port /dev/ttyUSB0
python3 pi_hw_tests/03_polled_smoke.py --port /dev/ttyUSB0

echo "All tests passed!"
```

## Troubleshooting

### Permission Denied

```
Error: Could not open port /dev/ttyUSB0: [Errno 13] Permission denied
```

**Solution:**
```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

### No Serial Ports Found

```
⚠ No serial ports found
```

**Solution:**
- Check USB cable connection
- Verify sensor is powered on
- Check `dmesg | tail` for USB detection messages
- Try `ls -l /dev/tty*` to see all TTY devices

### Menu Timeout

```
MenuTimeout: Menu prompt not received within 5.0s
```

**Possible causes:**
- Sensor already in acquisition mode (power cycle it)
- Wrong baud rate (try `--baud 9600` or `--baud 19200`)
- Serial cable issue
- Firmware not responding (check sensor LED indicators)

### Low Row Count in Freerun

```
⚠ Only 50 rows in 10s (expected 120-180)
```

**Possible causes:**
- Sensor streaming slower than expected (check firmware config)
- Serial buffer overflow (try reducing `averaging` or `adc_rate_hz`)
- USB hub issue (connect directly to Pi)

### FakeSerial Import Error

```
ImportError: FakeSerial not available
```

**Solution:**
Ensure `fakes/` directory is in your project:
```bash
ls -l fakes/fake_serial.py
```

If missing, check your project structure or run tests without `--fake` flag.

## Advanced Usage

### Running All Tests Sequentially

```bash
# Real hardware
for script in pi_hw_tests/0*.py; do
    echo "Running $script..."
    python3 "$script" --port /dev/ttyUSB0 || exit 1
done

echo "✓ All tests passed!"
```

### Running with pytest (FakeSerial only)

Create `test_pi_hardware.py`:

```python
import subprocess

def test_env_check():
    result = subprocess.run(["python3", "pi_hw_tests/00_env_check.py"], capture_output=True)
    assert result.returncode == 0

def test_connect_fake():
    result = subprocess.run(["python3", "pi_hw_tests/01_connect_and_identify.py", "--fake"], capture_output=True)
    assert result.returncode == 0

# Add more tests...
```

Run with:
```bash
pytest test_pi_hardware.py -v
```

### Custom Configuration

Most scripts accept additional arguments. Check help:

```bash
python3 pi_hw_tests/02_freerun_smoke.py --help
```

Example custom run:
```bash
python3 pi_hw_tests/02_freerun_smoke.py \
    --port /dev/ttyACM0 \
    --baud 19200 \
    --duration 30
```

## Test Coverage

| Test | Coverage |
|------|----------|
| 00_env_check | Environment, packages, ports |
| 01_connect_and_identify | Connection, identification, menu |
| 02_freerun_smoke | Freerun mode, sample rate, export |
| 03_polled_smoke | Polled mode, TAG handling |
| 04_menu_consistency | Menu commands, firmware responses |
| 05_temp_vin_presence | Optional fields, schema normalization |
| 06_pause_resume_durability | State transitions, pause/resume |
| 07_disconnect_reconnect | Connection lifecycle |
| 08_burn_in_30min | Long-duration stability |
| 09_export_throughput | Export performance, file integrity |

## Expected Ranges (Real Hardware)

Based on firmware 2150REV4.003:

| Parameter | Expected Value | Notes |
|-----------|----------------|-------|
| Sample rate (125/125) | ~15 Hz | ADC_rate / averaging |
| P95 interval | 40-90 ms | At 15 Hz |
| Polled rate | User-specified | Controlled by poll_hz |
| TempC | Varies | If firmware flag enabled |
| Vin | ~12V typical | If firmware flag enabled |
| Max gap | <5× period | No large stalls |

## Support

For issues or questions:
1. Check logs in `./pi_hw_tests/logs/`
2. Review firmware alignment doc: `docs/FIRMWARE_ALIGNMENT.md`
3. Check API reference: `docs/API_REFERENCE.md`
4. See main README: `README.md`
