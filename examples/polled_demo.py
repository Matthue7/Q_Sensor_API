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
