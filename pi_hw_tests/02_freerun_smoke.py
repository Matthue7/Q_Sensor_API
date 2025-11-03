#!/usr/bin/env python3
"""Freerun smoke test with DataFrame recording.

Tests:
- Freerun configuration (125 Hz / 125 avg = ~1 Hz, but FakeSerial runs ~10 Hz)
- Acquisition start
- DataRecorder integration
- Sample rate verification
- CSV export

Expected:
- Duration: 10s
- Rows: 120-180 for real hardware (~15 Hz), 80-120 for FakeSerial (~10 Hz)
- P95 interval: 40-90 ms
"""

import argparse
import sys
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (RollingRateEstimator, TestResult,
                                 calculate_percentile_interval,
                                 check_expected_range, export_dataframe_csv,
                                 robust_teardown, setup_logging)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

# Optional FakeSerial
try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Freerun smoke test")
    parser.add_argument("--port", default="/dev/ttyUSB0",
                       help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=9600,
                       help="Baud rate (default: 9600)")
    parser.add_argument("--duration", type=int, default=10,
                       help="Test duration in seconds (default: 10)")
    parser.add_argument("--fake", action="store_true",
                       help="Use FakeSerial")
    args = parser.parse_args()

    # Setup logging
    logs_dir = Path(__file__).parent / "logs"
    logger = setup_logging("02_freerun_smoke", logs_dir)

    controller = None
    store = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Freerun Smoke Test")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Duration: {args.duration}s")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="FREERUN_TEST", quiet_mode=True)
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

        # Configure freerun
        logger.info("Configuring freerun mode (125 avg, 125 Hz)...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        controller.set_mode("freerun")

        # Create DataStore and DataRecorder
        logger.info("Creating DataStore and DataRecorder...")
        store = DataStore(max_rows=10000)
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()
        time.sleep(0.5)  # Let stream stabilize

        # Start recording
        logger.info("Starting recorder...")
        recorder.start()

        # Monitor for specified duration
        logger.info(f"Recording for {args.duration} seconds...")
        estimator = RollingRateEstimator(window_seconds=5.0)

        start_time = time.time()
        last_report = start_time

        while time.time() - start_time < args.duration:
            # Get current stats
            stats = store.get_stats()
            current_count = stats["row_count"]

            # Update estimator
            if current_count > 0:
                estimator.add_sample()

            # Report every 2 seconds
            if time.time() - last_report >= 2.0:
                rate = estimator.get_rate_hz()
                logger.info(f"  Rows: {current_count}, Rate: {rate:.1f} Hz")
                last_report = time.time()

            time.sleep(0.2)

        # Stop recording BEFORE controller
        logger.info("Stopping recorder...")
        recorder.stop()

        logger.info("Stopping acquisition...")
        controller.stop()

        # Get final stats
        final_stats = store.get_stats()
        final_count = final_stats["row_count"]
        duration = final_stats["duration_s"]
        final_rate = final_stats["est_sample_rate_hz"]

        logger.info(f"Final count: {final_count} rows")
        logger.info(f"Duration: {duration:.2f}s")
        logger.info(f"Sample rate: {final_rate:.2f} Hz")

        metrics["rows"] = final_count
        metrics["duration_s"] = duration
        metrics["sample_rate_hz"] = final_rate

        # Calculate percentile interval
        df = store.get_dataframe()
        if len(df) >= 2:
            # Convert timestamps back to datetime for interval calculation
            import pandas as pd
            df["ts"] = pd.to_datetime(df["timestamp"], utc=True)

            # Create fake Reading objects with .ts attribute
            class FakeReading:
                def __init__(self, ts):
                    self.ts = ts

            readings = [FakeReading(ts) for ts in df["ts"]]
            p95_interval_ms = calculate_percentile_interval(readings, percentile=95)
            logger.info(f"P95 interval: {p95_interval_ms:.1f} ms")
            metrics["p95_interval_ms"] = p95_interval_ms

        # Export to CSV
        out_dir = Path(__file__).parent / "out"
        csv_path = export_dataframe_csv(store, out_dir, "freerun_smoke")
        logger.info(f"Exported to {csv_path}")
        metrics["csv_path"] = str(csv_path)

        # Validate results
        # Real hardware: ~15 Hz * 10s = 120-180 rows
        # FakeSerial: ~10 Hz * 10s = 80-120 rows
        min_rows = 80 if args.fake else 120
        max_rows = 120 if args.fake else 180

        ok_count, msg_count = check_expected_range(
            final_count, min_rows, max_rows, "row_count"
        )
        logger.info(msg_count)

        # P95 interval check (40-90 ms for ~10-25 Hz)
        if "p95_interval_ms" in metrics:
            ok_interval, msg_interval = check_expected_range(
                p95_interval_ms, 40, 90, "p95_interval_ms"
            )
            logger.info(msg_interval)
        else:
            ok_interval = True
            msg_interval = "Insufficient data for interval check"

        passed = ok_count and ok_interval
        message = "Freerun smoke test passed" if passed else "Freerun smoke test failed"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Freerun test failed: {str(e)}"
        passed = False

    finally:
        # Robust teardown
        robust_teardown(controller, recorder, logger=logger)

    # Print result
    result = TestResult(
        passed=passed,
        message=message,
        metrics=metrics
    )
    result.print_result()

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
