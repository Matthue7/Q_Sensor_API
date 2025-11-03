#!/usr/bin/env python3
"""Pause/resume durability test - verify state transitions.

Tests acquisition pause/resume cycle:
- Start acquisition in freerun mode
- Pause acquisition (ESC to menu)
- Resume acquisition
- Verify readings resume correctly
- Test multiple pause/resume cycles

Expected behavior:
- pause() sends ESC, transitions to CONFIG_MENU
- resume() exits menu, returns to ACQ_FREERUN
- No data loss during pause (buffer cleared is expected)
- Readings continue after resume
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (
    TestResult,
    export_dataframe_csv,
    robust_teardown,
    setup_logging,
    wait_for_samples,
)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import ConnectionState
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Pause/resume durability test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--cycles", type=int, default=3, help="Number of pause/resume cycles")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    out_dir = Path(__file__).parent / "out"
    logger = setup_logging("06_pause_resume_durability", logs_dir)

    controller = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Pause/Resume Durability")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Cycles: {args.cycles}")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()
        store = DataStore(max_rows=100000)

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="PAUSE_TEST", quiet_mode=True)
            transport = Transport(fake)
            controller._transport = transport
            controller._state = controller._state.__class__.DISCONNECTED
            controller._enter_menu()
            controller._config = controller._read_config_snapshot()
            controller._sensor_id = controller._config.serial_number
            controller._state = controller._state.__class__.CONFIG_MENU
        else:
            logger.info(f"Connecting to {args.port}...")
            controller.connect(port=args.port, baud=args.baud)

        logger.info(f"Sensor ID: {controller.sensor_id}")

        # Configure freerun
        logger.info("Configuring for freerun mode...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        config = controller.set_mode("freerun")

        logger.info(f"Config: {config.averaging} avg, {config.adc_rate_hz} Hz")

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Verify in acquisition state
        if controller.state != ConnectionState.ACQ_FREERUN:
            raise ValueError(f"Expected ACQ_FREERUN, got {controller.state}")

        logger.info("✓ Acquisition started successfully")

        # Wait for initial samples
        logger.info("Waiting for initial samples...")
        if not wait_for_samples(controller, min_samples=5, timeout=3.0):
            raise TimeoutError("No samples received after start")

        initial_count = controller.buffer.count()
        logger.info(f"✓ Initial buffer count: {initial_count}")
        metrics["initial_samples"] = initial_count

        # Pause/resume cycles
        cycle_results = []

        for cycle in range(args.cycles):
            logger.info(f"\n--- Cycle {cycle + 1}/{args.cycles} ---")

            # Pause
            logger.info("Pausing acquisition...")
            controller.pause()

            if controller.state != ConnectionState.CONFIG_MENU:
                raise ValueError(f"Expected CONFIG_MENU after pause, got {controller.state}")

            logger.info("✓ Paused (state=CONFIG_MENU)")

            # Brief pause
            time.sleep(0.5)

            # Resume
            logger.info("Resuming acquisition...")
            controller.resume()

            if controller.state != ConnectionState.ACQ_FREERUN:
                raise ValueError(f"Expected ACQ_FREERUN after resume, got {controller.state}")

            logger.info("✓ Resumed (state=ACQ_FREERUN)")

            # Wait for samples after resume
            logger.info("Waiting for samples after resume...")
            if not wait_for_samples(controller, min_samples=5, timeout=3.0):
                logger.error(f"✗ No samples after resume in cycle {cycle + 1}")
                cycle_results.append(False)
            else:
                post_resume_count = controller.buffer.count()
                logger.info(f"✓ Samples received after resume: {post_resume_count}")
                cycle_results.append(True)

        # Check all cycles passed
        cycles_passed = sum(cycle_results)
        logger.info(f"\nCycles passed: {cycles_passed}/{args.cycles}")
        metrics["cycles_passed"] = cycles_passed
        metrics["cycles_total"] = args.cycles

        if cycles_passed != args.cycles:
            raise ValueError(f"Only {cycles_passed}/{args.cycles} cycles passed")

        logger.info("✓ All pause/resume cycles completed successfully")

        # Final data collection
        logger.info("\nStarting recorder for final data collection...")
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)
        recorder.start()

        time.sleep(2.0)

        recorder.stop()

        df = store.get_dataframe()
        final_rows = len(df)

        logger.info(f"Final DataFrame rows: {final_rows}")
        metrics["final_rows"] = final_rows

        if final_rows < 10:
            logger.warning(f"Only {final_rows} rows collected after all cycles (expected >10)")

        # Export
        csv_path = export_dataframe_csv(store, out_dir, "06_pause_resume_durability")
        logger.info(f"Data exported to {csv_path}")

        passed = True
        message = f"All {args.cycles} pause/resume cycles passed"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Pause/resume test failed: {str(e)}"
        passed = False

    finally:
        robust_teardown(controller, recorder=recorder, logger=logger)

    result = TestResult(
        passed=passed,
        message=message,
        metrics=metrics
    )
    result.print_result()

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
