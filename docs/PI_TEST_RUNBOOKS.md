# Raspberry Pi Hardware Test Runbooks

This document contains three ready-to-run Python scripts for validating `q_sensor_lib` on Raspberry Pi with real Q-Series hardware.

Each script is **verbatim copy-paste ready** - save to a `.py` file and run directly on your Pi.

## Prerequisites

1. **Hardware Setup:**
   - Raspberry Pi (tested on Pi 4, should work on Pi 3+)
   - Q-Series sensor running firmware 4.003, 2150 mode
   - USB-to-Serial adapter connected to sensor
   - Sensor powered on and responding

2. **Software Setup:**
   ```bash
   # Install library on Pi
   cd /path/to/Q_Sensor_API
   pip install -e .

   # Verify pyserial is installed
   python3 -c "import serial; print(serial.VERSION)"

   # Find your serial port
   python3 -m serial.tools.list_ports
   # Common: /dev/ttyUSB0, /dev/ttyAMA0, /dev/serial0
   ```

3. **Permissions:**
   ```bash
   # Add user to dialout group (then log out/in)
   sudo usermod -a -G dialout $USER

   # Or run scripts with sudo (not recommended)
   ```

---

## Runbook A: Freerun Mode (10 seconds)

**Purpose:** Validate continuous data streaming in freerun mode
**Expected Output:** ~10 readings at 1 Hz rate
**Runtime:** ~12 seconds

```python
#!/usr/bin/env python3
"""
Pi Runbook A: Freerun Mode Test
Expected: ~10 readings over 10 seconds at 1 Hz
"""

import time
from q_sensor_lib import SensorController

# ============================================================================
# CONFIGURATION - EDIT THIS
# ============================================================================
SERIAL_PORT = "/dev/ttyUSB0"  # Change to your port
BAUD_RATE = 9600
RUN_DURATION_S = 10.0

# ============================================================================
# TEST SCRIPT - DO NOT EDIT BELOW
# ============================================================================

print("=" * 70)
print("Pi Runbook A: Freerun Mode Validation")
print("=" * 70)
print(f"Port: {SERIAL_PORT}")
print(f"Baud: {BAUD_RATE}")
print(f"Duration: {RUN_DURATION_S}s")
print()

controller = SensorController()

try:
    # Step 1: Connect and configure
    print("[1/4] Connecting to sensor...")
    controller.connect(port=SERIAL_PORT, baud=BAUD_RATE)
    print(f"      Connected! Sensor ID: {controller.sensor_id}")
    print()

    # Step 2: Configure for 1 Hz freerun
    print("[2/4] Configuring for freerun mode (1 Hz)...")
    controller.set_averaging(125)
    controller.set_adc_rate(125)
    controller.set_mode("freerun")

    config = controller.get_config()
    print(f"      Averaging: {config.averaging}")
    print(f"      ADC Rate: {config.adc_rate_hz} Hz")
    print(f"      Mode: {config.mode}")
    print(f"      Sample period: {config.sample_period_s:.2f}s")
    print()

    # Step 3: Start acquisition
    print("[3/4] Starting acquisition...")
    controller.start_acquisition()
    print(f"      State: {controller.state.value}")
    print(f"      Collecting data for {RUN_DURATION_S}s...")
    print()

    # Collect data and print live updates
    start_time = time.time()
    last_count = 0

    while time.time() - start_time < RUN_DURATION_S:
        time.sleep(0.5)
        readings = controller.read_buffer_snapshot()
        if len(readings) > last_count:
            latest = readings[-1]
            elapsed = time.time() - start_time
            print(f"      [{elapsed:5.1f}s] Reading #{len(readings)}: "
                  f"value={latest.data['value']:.6f}")
            last_count = len(readings)

    # Step 4: Results
    print()
    print("[4/4] Test complete! Analyzing results...")
    final_readings = controller.read_buffer_snapshot()

    print(f"      Total readings: {len(final_readings)}")
    print(f"      Expected: ~{int(RUN_DURATION_S / config.sample_period_s)} "
          f"(±1-2 due to timing)")
    print()

    if final_readings:
        print("      First reading:")
        print(f"        Timestamp: {final_readings[0].ts.isoformat()}")
        print(f"        Value: {final_readings[0].data['value']:.6f}")
        print()
        print("      Last reading:")
        print(f"        Timestamp: {final_readings[-1].ts.isoformat()}")
        print(f"        Value: {final_readings[-1].data['value']:.6f}")
        print()

    # Calculate actual rate
    if len(final_readings) >= 2:
        time_span = (final_readings[-1].ts - final_readings[0].ts).total_seconds()
        actual_rate = (len(final_readings) - 1) / time_span if time_span > 0 else 0
        print(f"      Measured rate: {actual_rate:.2f} Hz")
        print(f"      Expected rate: {1.0 / config.sample_period_s:.2f} Hz")
        print()

    # Validation
    expected_count = int(RUN_DURATION_S / config.sample_period_s)
    if expected_count - 2 <= len(final_readings) <= expected_count + 2:
        print("✓ PASS: Reading count within expected range")
    else:
        print("✗ FAIL: Reading count outside expected range")
        print(f"  Expected {expected_count} ±2, got {len(final_readings)}")

finally:
    controller.disconnect()
    print()
    print("Disconnected.")
    print("=" * 70)
```

**Expected Console Output:**
```
======================================================================
Pi Runbook A: Freerun Mode Validation
======================================================================
Port: /dev/ttyUSB0
Baud: 9600
Duration: 10.0s

[1/4] Connecting to sensor...
      Connected! Sensor ID: Q12345

[2/4] Configuring for freerun mode (1 Hz)...
      Averaging: 125
      ADC Rate: 125 Hz
      Mode: freerun
      Sample period: 1.00s

[3/4] Starting acquisition...
      State: acq_freerun
      Collecting data for 10.0s...

      [  0.5s] Reading #1: value=102.345678
      [  1.5s] Reading #2: value=101.234567
      ...
      [  9.5s] Reading #10: value=99.876543

[4/4] Test complete! Analyzing results...
      Total readings: 10
      Expected: ~10 (±1-2 due to timing)

      First reading:
        Timestamp: 2025-01-15T14:23:01.123456+00:00
        Value: 102.345678

      Last reading:
        Timestamp: 2025-01-15T14:23:10.234567+00:00
        Value: 99.876543

      Measured rate: 1.00 Hz
      Expected rate: 1.00 Hz

✓ PASS: Reading count within expected range

Disconnected.
======================================================================
```

---

## Runbook B: Polled Mode (2 Hz, 10 seconds)

**Purpose:** Validate polled query/response protocol
**Expected Output:** ~20 readings at 2 Hz poll rate
**Runtime:** ~13 seconds (includes averaging fill time)

```python
#!/usr/bin/env python3
"""
Pi Runbook B: Polled Mode Test
Expected: ~20 readings over 10 seconds at 2 Hz poll rate
"""

import time
from q_sensor_lib import SensorController

# ============================================================================
# CONFIGURATION - EDIT THIS
# ============================================================================
SERIAL_PORT = "/dev/ttyUSB0"  # Change to your port
BAUD_RATE = 9600
POLL_RATE_HZ = 2.0
RUN_DURATION_S = 10.0
TAG = "A"  # Change if your device uses different TAG

# ============================================================================
# TEST SCRIPT - DO NOT EDIT BELOW
# ============================================================================

print("=" * 70)
print("Pi Runbook B: Polled Mode Validation")
print("=" * 70)
print(f"Port: {SERIAL_PORT}")
print(f"Baud: {BAUD_RATE}")
print(f"Poll rate: {POLL_RATE_HZ} Hz")
print(f"TAG: {TAG}")
print(f"Duration: {RUN_DURATION_S}s")
print()

controller = SensorController()

try:
    # Step 1: Connect and configure
    print("[1/4] Connecting to sensor...")
    controller.connect(port=SERIAL_PORT, baud=BAUD_RATE)
    print(f"      Connected! Sensor ID: {controller.sensor_id}")
    print()

    # Step 2: Configure for polled mode
    print("[2/4] Configuring for polled mode...")
    controller.set_averaging(100)
    controller.set_adc_rate(125)
    controller.set_mode("polled", tag=TAG)

    config = controller.get_config()
    print(f"      Averaging: {config.averaging}")
    print(f"      ADC Rate: {config.adc_rate_hz} Hz")
    print(f"      Mode: {config.mode}")
    print(f"      TAG: {config.tag}")
    print(f"      Sample period: {config.sample_period_s:.2f}s")
    print()

    # Step 3: Start acquisition
    print(f"[3/4] Starting polled acquisition at {POLL_RATE_HZ} Hz...")
    controller.start_acquisition(poll_hz=POLL_RATE_HZ)
    print(f"      State: {controller.state.value}")

    # Wait for averaging to fill (noted in logs)
    wait_time = config.sample_period_s + 0.5
    print(f"      Waiting {wait_time:.1f}s for averaging to fill...")
    time.sleep(wait_time)

    print(f"      Polling for {RUN_DURATION_S}s...")
    print()

    # Collect data and print live updates
    start_time = time.time()
    last_count = 0

    while time.time() - start_time < RUN_DURATION_S:
        time.sleep(0.5)
        readings = controller.read_buffer_snapshot()
        if len(readings) > last_count:
            latest = readings[-1]
            elapsed = time.time() - start_time
            print(f"      [{elapsed:5.1f}s] Reading #{len(readings)}: "
                  f"value={latest.data['value']:.6f}")
            last_count = len(readings)

    # Step 4: Results
    print()
    print("[4/4] Test complete! Analyzing results...")
    final_readings = controller.read_buffer_snapshot()

    expected_count = int(RUN_DURATION_S * POLL_RATE_HZ)
    print(f"      Total readings: {len(final_readings)}")
    print(f"      Expected: ~{expected_count} (±2-3 due to timing)")
    print()

    if final_readings:
        print("      First reading:")
        print(f"        Timestamp: {final_readings[0].ts.isoformat()}")
        print(f"        Mode: {final_readings[0].mode}")
        print(f"        Value: {final_readings[0].data['value']:.6f}")
        print()
        print("      Last reading:")
        print(f"        Timestamp: {final_readings[-1].ts.isoformat()}")
        print(f"        Mode: {final_readings[-1].mode}")
        print(f"        Value: {final_readings[-1].data['value']:.6f}")
        print()

    # Calculate actual poll rate
    if len(final_readings) >= 2:
        time_span = (final_readings[-1].ts - final_readings[0].ts).total_seconds()
        actual_rate = (len(final_readings) - 1) / time_span if time_span > 0 else 0
        print(f"      Measured poll rate: {actual_rate:.2f} Hz")
        print(f"      Expected poll rate: {POLL_RATE_HZ:.2f} Hz")
        print()

    # Validation
    if expected_count - 3 <= len(final_readings) <= expected_count + 3:
        print("✓ PASS: Reading count within expected range")
    else:
        print("✗ FAIL: Reading count outside expected range")
        print(f"  Expected {expected_count} ±3, got {len(final_readings)}")

    # Verify all readings are mode="polled"
    polled_count = sum(1 for r in final_readings if r.mode == "polled")
    if polled_count == len(final_readings):
        print("✓ PASS: All readings marked as mode='polled'")
    else:
        print(f"✗ FAIL: Some readings not mode='polled' ({polled_count}/{len(final_readings)})")

finally:
    controller.disconnect()
    print()
    print("Disconnected.")
    print("=" * 70)
```

**Expected Console Output:**
```
======================================================================
Pi Runbook B: Polled Mode Validation
======================================================================
Port: /dev/ttyUSB0
Baud: 9600
Poll rate: 2.0 Hz
TAG: A
Duration: 10.0s

[1/4] Connecting to sensor...
      Connected! Sensor ID: Q12345

[2/4] Configuring for polled mode...
      Averaging: 100
      ADC Rate: 125 Hz
      Mode: polled
      TAG: A
      Sample period: 0.80s

[3/4] Starting polled acquisition at 2.0 Hz...
      State: acq_polled
      Waiting 1.3s for averaging to fill...
      Polling for 10.0s...

      [  0.5s] Reading #1: value=102.345678
      [  1.0s] Reading #2: value=101.234567
      ...
      [  9.5s] Reading #20: value=99.876543

[4/4] Test complete! Analyzing results...
      Total readings: 20
      Expected: ~20 (±2-3 due to timing)

      First reading:
        Timestamp: 2025-01-15T14:25:01.123456+00:00
        Mode: polled
        Value: 102.345678

      Last reading:
        Timestamp: 2025-01-15T14:25:10.234567+00:00
        Mode: polled
        Value: 99.876543

      Measured poll rate: 2.01 Hz
      Expected poll rate: 2.00 Hz

✓ PASS: Reading count within expected range
✓ PASS: All readings marked as mode='polled'

Disconnected.
======================================================================
```

---

## Runbook C: Pause/Resume in Freerun Mode

**Purpose:** Validate pause/resume state machine
**Expected Output:** Data collection, pause (no new data), resume (data restarts)
**Runtime:** ~18 seconds

```python
#!/usr/bin/env python3
"""
Pi Runbook C: Pause/Resume Test
Expected: Freerun data, pause (silence), resume (data restarts)
"""

import time
from q_sensor_lib import SensorController
from q_sensor_lib.models import ConnectionState

# ============================================================================
# CONFIGURATION - EDIT THIS
# ============================================================================
SERIAL_PORT = "/dev/ttyUSB0"  # Change to your port
BAUD_RATE = 9600

# ============================================================================
# TEST SCRIPT - DO NOT EDIT BELOW
# ============================================================================

print("=" * 70)
print("Pi Runbook C: Pause/Resume Validation")
print("=" * 70)
print(f"Port: {SERIAL_PORT}")
print(f"Baud: {BAUD_RATE}")
print()

controller = SensorController()

try:
    # Step 1: Connect and configure
    print("[1/6] Connecting and configuring...")
    controller.connect(port=SERIAL_PORT, baud=BAUD_RATE)
    print(f"      Connected! Sensor ID: {controller.sensor_id}")

    controller.set_averaging(125)
    controller.set_adc_rate(125)
    controller.set_mode("freerun")
    print(f"      Configured for freerun at 1 Hz")
    print()

    # Step 2: Start acquisition
    print("[2/6] Starting acquisition...")
    controller.start_acquisition()
    print(f"      State: {controller.state.value}")
    print(f"      Collecting for 5 seconds...")

    time.sleep(5.0)

    readings_phase1 = controller.read_buffer_snapshot()
    print(f"      Phase 1: {len(readings_phase1)} readings collected")
    print()

    # Step 3: Pause
    print("[3/6] Pausing acquisition...")
    controller.pause()
    print(f"      State after pause: {controller.state.value}")

    if controller.state != ConnectionState.PAUSED:
        print("✗ FAIL: State should be PAUSED")
    else:
        print("✓ State is PAUSED")

    count_before_pause = len(controller.read_buffer_snapshot())
    print(f"      Readings at pause: {count_before_pause}")
    print(f"      Waiting 3 seconds (no new data should arrive)...")

    time.sleep(3.0)

    count_after_wait = len(controller.read_buffer_snapshot())
    print(f"      Readings after wait: {count_after_wait}")

    if count_after_wait == count_before_pause:
        print("✓ PASS: No new readings during pause")
    else:
        print(f"✗ FAIL: {count_after_wait - count_before_pause} readings arrived during pause")
    print()

    # Step 4: Clear buffer and resume
    print("[4/6] Clearing buffer and resuming...")
    controller.clear_buffer()
    print(f"      Buffer cleared (count={len(controller.read_buffer_snapshot())})")

    controller.resume()
    print(f"      State after resume: {controller.state.value}")

    if controller.state != ConnectionState.ACQ_FREERUN:
        print(f"✗ FAIL: State should be ACQ_FREERUN, got {controller.state.value}")
    else:
        print("✓ State is ACQ_FREERUN")
    print()

    # Step 5: Verify data resumes
    print("[5/6] Verifying data collection resumed...")
    print(f"      Collecting for 5 seconds...")

    time.sleep(5.0)

    readings_phase2 = controller.read_buffer_snapshot()
    print(f"      Phase 2: {len(readings_phase2)} readings collected after resume")

    if len(readings_phase2) >= 3:
        print("✓ PASS: Data collection resumed successfully")
        print()
        print("      Latest readings:")
        for i, r in enumerate(readings_phase2[-3:], 1):
            print(f"        {i}. {r.ts.isoformat()}: value={r.data['value']:.6f}")
    else:
        print(f"✗ FAIL: Expected ≥3 readings after resume, got {len(readings_phase2)}")
    print()

    # Step 6: Final stop
    print("[6/6] Stopping acquisition...")
    controller.stop()
    print(f"      State after stop: {controller.state.value}")

    if controller.state != ConnectionState.CONFIG_MENU:
        print(f"✗ FAIL: State should be CONFIG_MENU, got {controller.state.value}")
    else:
        print("✓ State is CONFIG_MENU")

    # Verify we can reconfigure after stop
    print("      Testing reconfiguration after stop...")
    new_config = controller.set_averaging(200)
    if new_config.averaging == 200:
        print("✓ PASS: Can reconfigure after stop")
    else:
        print("✗ FAIL: Cannot reconfigure after stop")
    print()

    # Summary
    print("=" * 70)
    print("SUMMARY:")
    print(f"  Phase 1 (initial): {len(readings_phase1)} readings")
    print(f"  Pause worked: {count_after_wait == count_before_pause}")
    print(f"  Phase 2 (resumed): {len(readings_phase2)} readings")
    print(f"  Stop worked: {controller.state == ConnectionState.CONFIG_MENU}")
    print("=" * 70)

finally:
    controller.disconnect()
    print()
    print("Disconnected.")
```

**Expected Console Output:**
```
======================================================================
Pi Runbook C: Pause/Resume Validation
======================================================================
Port: /dev/ttyUSB0
Baud: 9600

[1/6] Connecting and configuring...
      Connected! Sensor ID: Q12345
      Configured for freerun at 1 Hz

[2/6] Starting acquisition...
      State: acq_freerun
      Collecting for 5 seconds...
      Phase 1: 5 readings collected

[3/6] Pausing acquisition...
      State after pause: paused
✓ State is PAUSED
      Readings at pause: 5
      Waiting 3 seconds (no new data should arrive)...
      Readings after wait: 5
✓ PASS: No new readings during pause

[4/6] Clearing buffer and resuming...
      Buffer cleared (count=0)
      State after resume: acq_freerun
✓ State is ACQ_FREERUN

[5/6] Verifying data collection resumed...
      Collecting for 5 seconds...
      Phase 2: 5 readings collected after resume
✓ PASS: Data collection resumed successfully

      Latest readings:
        1. 2025-01-15T14:30:15.123456+00:00: value=102.345678
        2. 2025-01-15T14:30:16.234567+00:00: value=101.234567
        3. 2025-01-15T14:30:17.345678+00:00: value=100.123456

[6/6] Stopping acquisition...
      State after stop: config_menu
✓ State is CONFIG_MENU
      Testing reconfiguration after stop...
✓ PASS: Can reconfigure after stop

======================================================================
SUMMARY:
  Phase 1 (initial): 5 readings
  Pause worked: True
  Phase 2 (resumed): 5 readings
  Stop worked: True
======================================================================

Disconnected.
```

---

## Protocol Assumptions Requiring Hardware Verification

These are assumptions made by the library that should be verified with real hardware:

### 1. **Power-on Banner Timing** ✓ *Validated in previous session*
- **Assumption:** Device outputs ~11 lines of banner text over ~1.2 seconds after port open
- **Validation:** Wait 1.2s after port open, then flush input before sending ESC
- **Risk if wrong:** ESC sent during banner gets ignored, menu never appears
- **How to verify:** Logic analyzer should show ESC transmitted only after 1.2s delay

### 2. **Device Reset Timing**
- **Assumption:** After 'X' command, device reboots in ~1.5 seconds, then immediately starts freerun/polled
- **Validation:** Library waits DELAY_POST_RESET (1.5s) then flushes banner
- **Risk if wrong:** Banner lines mixed into data stream, parsing fails
- **How to verify:**
  - In runbooks, enable debug logging: `logging.basicConfig(level=logging.DEBUG)`
  - Check logs for "Flushed input buffer" message after 'X' command
  - No unparseable lines should appear after acquisition starts

### 3. **Menu Re-prompt After Config Changes**
- **Assumption:** After each config command (A/R/M) completes, device re-displays menu prompt within 3s
- **Validation:** Library waits for `RE_MENU_PROMPT` with 3s timeout
- **Risk if wrong:** MenuTimeout exceptions on config changes
- **How to verify:**
  - Run Runbook A or B
  - If `MenuTimeout` occurs, capture serial log to see if prompt is malformed
  - Check if prompt matches regex: `^Select the letter of the menu entry:\s*$`

### 4. **Polled Init Command Format**
- **Assumption:** Polled init command is `*<TAG>Q000!` + CR (e.g., `*AQ000!\r`)
- **Validation:** `write_cmd()` adds CR automatically; `make_polled_init_cmd()` builds string
- **Risk if wrong:** Sensor won't respond to polled queries
- **How to verify:**
  - Run Runbook B with debug logging
  - Check log line: "Sent polled init command: *AQ000! (with CR)"
  - Verify readings arrive; if not, check sensor firmware docs for init format

### 5. **Polled Query/Response Protocol**
- **Assumption:**
  - Query: `><TAG>` + CR (e.g., `>A\r`)
  - Response: `<TAG><value>[,TempC][,Vin]` + CRLF (e.g., `A123.456\r\n`)
  - TAG in response must match TAG in query (strict validation)
- **Validation:** `parse_polled_line()` validates TAG, raises `InvalidResponse` on mismatch
- **Risk if wrong:** All polled readings fail to parse
- **How to verify:**
  - Run Runbook B
  - If readings show up, protocol is correct
  - If "Failed to parse polled response" warnings appear, capture raw response with debug logging

### 6. **Readline Timeout Sufficiency**
- **Assumption:** Device responds to queries within 0.5s (TIMEOUT_READ_LINE)
- **Validation:** Transport uses 0.5s timeout for readline()
- **Risk if wrong:** Frequent "No response to polled query" warnings in polled mode
- **How to verify:**
  - Run Runbook B, watch for warnings
  - If warnings appear frequently, may need to increase timeout
  - Check if averaging is set too high (longer integration time)

### 7. **Freerun Data Line Format**
- **Assumption:** Freerun lines are `<value>[,TempC][,Vin]` + CRLF (e.g., `123.456,25.3,12.0\r\n`)
- **Validation:** `parse_freerun_line()` splits on commas, converts to float
- **Risk if wrong:** Readings fail to parse, logged as "Skipping unparseable line"
- **How to verify:**
  - Run Runbook A with debug logging
  - Check for "Freerun reading: {data}" log lines
  - If "Skipping unparseable line" appears, capture exact line format

### 8. **ESC Byte Menu Entry**
- **Assumption:** ESC (0x1B) byte enters menu from any state (freerun, polled, menu)
- **Validation:** pause() sends ESC after stopping threads
- **Risk if wrong:** pause() fails, device doesn't enter menu
- **How to verify:**
  - Run Runbook C
  - If pause fails, device may not support ESC interrupt
  - Alternative: Use '?' character if ESC doesn't work

### 9. **State Machine Transitions**
- **Assumption:**
  - DISCONNECTED → CONFIG_MENU (via connect)
  - CONFIG_MENU → ACQ_FREERUN/ACQ_POLLED (via start_acquisition)
  - ACQ_* → PAUSED (via pause/ESC)
  - PAUSED → ACQ_* (via resume/'X')
  - ACQ_* → CONFIG_MENU (via stop/ESC)
- **Validation:** Runbook C tests all transitions
- **Risk if wrong:** State mismatch errors, operations fail
- **How to verify:** Run Runbook C, all state checks should pass

### 10. **Thread-Safe Data Buffering**
- **Assumption:** RingBuffer with Lock provides thread-safe read/write
- **Validation:** Reader threads append, main thread calls read_buffer_snapshot()
- **Risk if wrong:** Data corruption, race conditions
- **How to verify:**
  - Run Runbooks A/B for extended periods (>1 hour)
  - No crashes, no missing/duplicate readings
  - All readings have sequential timestamps

---

## Troubleshooting

### Port Permission Denied
```
SerialIOError: Failed to open /dev/ttyUSB0: could not open port
```
**Fix:**
```bash
sudo usermod -a -G dialout $USER
# Log out and log back in
```

### No Menu Prompt
```
MenuTimeout: Did not receive menu prompt after ESC
```
**Possible causes:**
1. Wrong baud rate (verify sensor is 9600)
2. Wrong port (use `python3 -m serial.tools.list_ports`)
3. Device not powered on or crashed
4. Cable issue (swap USB-Serial adapter)

**Debug:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
# Then run test script
```

### No Readings in Polled Mode
```
WARNING: No response to polled query, will retry
```
**Possible causes:**
1. Polled init command not sent/acknowledged
2. Wrong TAG (check config.tag matches device)
3. Averaging too high (response slower than timeout)

**Debug:** Enable logging, check for "Sent polled init command" line

### Readings Stop After Start
**Possible causes:**
1. Device reset unexpectedly (power glitch)
2. ESC sent accidentally (keyboard input if running in terminal)
3. Reader thread crashed (check stderr for exceptions)

**Debug:** Check `controller.state` - should remain in ACQ_FREERUN/ACQ_POLLED

---

## Success Criteria

All three runbooks should:
1. ✓ Complete without exceptions
2. ✓ Receive expected number of readings (±20% tolerance)
3. ✓ Show stable data rates matching configuration
4. ✓ Pass all validation checks (marked with ✓)

If any runbook fails, capture:
1. Full console output (with debug logging enabled)
2. Sensor model and firmware version
3. USB-Serial adapter model
4. Operating system (e.g., Raspberry Pi OS 11)

Report issues at: https://github.com/yourusername/q_sensor_lib/issues
