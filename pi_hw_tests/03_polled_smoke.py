#!/usr/bin/env python3
"""Polled mode smoke test.

Tests:
- Polled configuration with TAG
- Polling at specified rate (default 2 Hz)
- DataFrame recording
- Sample count verification

Expected:
- Duration: 10s at 2 Hz = 18-22 rows (accounting for timing variance)
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (TestResult, check_expected_range,
                                 export_dataframe_csv, robust_teardown,
                                 setup_logging)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Polled mode smoke test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument("--tag", default="A", help="TAG character (A-Z)")
    parser.add_argument("--poll-rate", type=float, default=2.0,
                       help="Poll rate in Hz (default: 2.0)")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    logger = setup_logging("03_polled_smoke", logs_dir)

    controller = None
    store = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Polled Mode Smoke Test")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"TAG: {args.tag}, Poll Rate: {args.poll_rate} Hz")
        print(f"Duration: {args.duration}s")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="POLLED_TEST", quiet_mode=True)
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

        # Configure polled mode
        logger.info(f"Configuring polled mode (TAG={args.tag})...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        controller.set_mode("polled", tag=args.tag)

        # Create DataStore and DataRecorder
        logger.info("Creating DataStore and DataRecorder...")
        store = DataStore(max_rows=10000)
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)

        # Start acquisition with specified poll rate
        logger.info(f"Starting acquisition at {args.poll_rate} Hz...")
        controller.start_acquisition(poll_hz=args.poll_rate)
        time.sleep(0.5)

        # Start recording
        logger.info("Starting recorder...")
        recorder.start()

        # Monitor
        logger.info(f"Recording for {args.duration} seconds...")
        start_time = time.time()
        last_report = start_time

        while time.time() - start_time < args.duration:
            stats = store.get_stats()
            current_count = stats["row_count"]

            if time.time() - last_report >= 2.0:
                logger.info(f"  Rows: {current_count}")
                last_report = time.time()

            time.sleep(0.5)

        # Stop recorder BEFORE controller
        logger.info("Stopping recorder...")
        recorder.stop()

        logger.info("Stopping acquisition...")
        controller.stop()

        # Get final stats
        final_stats = store.get_stats()
        final_count = final_stats["row_count"]
        duration = final_stats["duration_s"]

        logger.info(f"Final count: {final_count} rows")
        logger.info(f"Duration: {duration:.2f}s")

        metrics["rows"] = final_count
        metrics["duration_s"] = duration
        metrics["poll_rate_hz"] = args.poll_rate

        # Verify all rows are polled mode
        df = store.get_dataframe()
        if not df.empty:
            mode_counts = df["mode"].value_counts()
            polled_count = mode_counts.get("polled", 0)
            logger.info(f"Polled mode rows: {polled_count}/{final_count}")
            metrics["polled_rows"] = polled_count

            if polled_count < final_count:
                logger.warning(f"{final_count - polled_count} non-polled rows found!")

        # Export to CSV
        out_dir = Path(__file__).parent / "out"
        csv_path = export_dataframe_csv(store, out_dir, "polled_smoke")
        logger.info(f"Exported to {csv_path}")
        metrics["csv_path"] = str(csv_path)

        # Validate: expected rows = duration * poll_rate ± 20%
        expected = args.duration * args.poll_rate
        min_rows = int(expected * 0.9)  # 90% of expected
        max_rows = int(expected * 1.1) + 2  # 110% + buffer

        ok_count, msg_count = check_expected_range(
            final_count, min_rows, max_rows, "row_count"
        )
        logger.info(msg_count)

        passed = ok_count
        message = "Polled smoke test passed" if passed else "Polled smoke test failed"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Polled test failed: {str(e)}"
        passed = False

    finally:
        robust_teardown(controller, recorder, logger=logger)

    result = TestResult(
        passed=passed,
        message=message,
        metrics=metrics
    )
    result.print_result()

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
