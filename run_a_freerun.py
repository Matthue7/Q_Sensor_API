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
