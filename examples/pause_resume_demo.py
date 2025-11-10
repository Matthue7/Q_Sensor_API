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
