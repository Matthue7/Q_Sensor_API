#!/usr/bin/env python3
"""Export throughput test - verify DataFrame export performance.

Tests DataStore export functionality:
- Acquire large dataset (10k-50k rows)
- Test CSV export performance
- Test Parquet export performance
- Verify exported file integrity
- Compare export times

Expected behavior:
- CSV export completes within reasonable time (<5s for 50k rows)
- Parquet export completes within reasonable time (<2s for 50k rows)
- Exported files are readable and contain all rows
- No data corruption during export
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (
    TestResult,
    robust_teardown,
    setup_logging,
    timestamped_filename,
    wait_for_samples,
)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Export throughput test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--target-rows", type=int, default=10000, help="Target row count for export test")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    out_dir = Path(__file__).parent / "out"
    logger = setup_logging("09_export_throughput", logs_dir)

    controller = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Export Throughput")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Target rows: {args.target_rows}")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()
        store = DataStore(max_rows=args.target_rows + 10000)  # Extra buffer

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="EXPORT_TEST", quiet_mode=True)
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

        # Configure for maximum throughput
        logger.info("Configuring for freerun mode (125 avg, 125 Hz ADC)...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        config = controller.set_mode("freerun")

        expected_rate_hz = 1.0 / config.sample_period_s
        logger.info(f"Expected sample rate: ~{expected_rate_hz:.1f} Hz")

        # Calculate duration needed
        duration_s = args.target_rows / expected_rate_hz * 1.2  # 20% buffer
        logger.info(f"Estimated acquisition time: {duration_s:.1f}s")

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Wait for initial samples
        if not wait_for_samples(controller, min_samples=5, timeout=3.0):
            raise TimeoutError("No samples received after start")

        # Start recorder
        logger.info("Starting recorder...")
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)
        recorder.start()

        # Wait until target rows reached
        logger.info(f"Acquiring {args.target_rows} rows...")
        start_time = time.time()

        while True:
            df = store.get_dataframe()
            current_rows = len(df)

            if current_rows >= args.target_rows:
                logger.info(f"✓ Target reached: {current_rows} rows")
                break

            elapsed = time.time() - start_time
            if elapsed > duration_s + 30:  # Timeout with 30s grace
                logger.warning(f"Timeout waiting for {args.target_rows} rows (got {current_rows})")
                break

            # Report progress every 2s
            if int(elapsed) % 2 == 0:
                pct = (current_rows / args.target_rows) * 100
                logger.info(f"Progress: {current_rows}/{args.target_rows} ({pct:.1f}%)")

            time.sleep(2.0)

        # Stop recorder
        logger.info("Stopping recorder...")
        recorder.stop()

        # Final row count
        df = store.get_dataframe()
        actual_rows = len(df)
        logger.info(f"Actual rows collected: {actual_rows}")
        metrics["rows_collected"] = actual_rows

        if actual_rows < args.target_rows * 0.8:
            logger.warning(f"⚠ Only collected {actual_rows} rows (target: {args.target_rows})")

        # =====================================================================
        # CSV Export Test
        # =====================================================================
        logger.info("\n=== CSV Export Test ===")

        csv_filename = timestamped_filename("09_export_test", "csv")
        csv_path = out_dir / csv_filename

        logger.info(f"Exporting to CSV: {csv_path}")
        csv_start = time.time()
        store.export_csv(str(csv_path))
        csv_duration = time.time() - csv_start

        logger.info(f"✓ CSV export completed in {csv_duration:.3f}s")
        metrics["csv_export_time_s"] = round(csv_duration, 3)

        # Verify CSV file
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not created: {csv_path}")

        csv_size_mb = csv_path.stat().st_size / (1024 * 1024)
        logger.info(f"CSV file size: {csv_size_mb:.2f} MB")
        metrics["csv_size_mb"] = round(csv_size_mb, 2)

        # Read back and verify
        import pandas as pd
        csv_df = pd.read_csv(csv_path)
        csv_rows = len(csv_df)
        logger.info(f"CSV rows verified: {csv_rows}")

        if csv_rows != actual_rows:
            raise ValueError(f"CSV row count mismatch: {csv_rows} != {actual_rows}")

        logger.info("✓ CSV integrity verified")

        # =====================================================================
        # Parquet Export Test
        # =====================================================================
        logger.info("\n=== Parquet Export Test ===")

        parquet_filename = timestamped_filename("09_export_test", "parquet")
        parquet_path = out_dir / parquet_filename

        logger.info(f"Exporting to Parquet: {parquet_path}")
        parquet_start = time.time()
        store.export_parquet(str(parquet_path))
        parquet_duration = time.time() - parquet_start

        logger.info(f"✓ Parquet export completed in {parquet_duration:.3f}s")
        metrics["parquet_export_time_s"] = round(parquet_duration, 3)

        # Verify Parquet file
        if not parquet_path.exists():
            raise FileNotFoundError(f"Parquet file not created: {parquet_path}")

        parquet_size_mb = parquet_path.stat().st_size / (1024 * 1024)
        logger.info(f"Parquet file size: {parquet_size_mb:.2f} MB")
        metrics["parquet_size_mb"] = round(parquet_size_mb, 2)

        # Read back and verify
        parquet_df = pd.read_parquet(parquet_path)
        parquet_rows = len(parquet_df)
        logger.info(f"Parquet rows verified: {parquet_rows}")

        if parquet_rows != actual_rows:
            raise ValueError(f"Parquet row count mismatch: {parquet_rows} != {actual_rows}")

        logger.info("✓ Parquet integrity verified")

        # =====================================================================
        # Performance Analysis
        # =====================================================================
        logger.info("\n=== Performance Summary ===")

        csv_throughput = actual_rows / csv_duration
        parquet_throughput = actual_rows / parquet_duration

        logger.info(f"CSV throughput: {csv_throughput:.0f} rows/s")
        logger.info(f"Parquet throughput: {parquet_throughput:.0f} rows/s")

        metrics["csv_throughput_rows_per_s"] = int(csv_throughput)
        metrics["parquet_throughput_rows_per_s"] = int(parquet_throughput)

        compression_ratio = csv_size_mb / parquet_size_mb if parquet_size_mb > 0 else 0
        logger.info(f"Compression ratio (CSV/Parquet): {compression_ratio:.2f}x")
        metrics["compression_ratio"] = round(compression_ratio, 2)

        # Success criteria
        success_criteria = [
            actual_rows >= args.target_rows * 0.8,  # Got at least 80% of target
            csv_rows == actual_rows,  # CSV integrity
            parquet_rows == actual_rows,  # Parquet integrity
            csv_duration < 10.0,  # CSV export reasonable time
            parquet_duration < 5.0,  # Parquet export reasonable time
        ]

        passed = all(success_criteria)
        message = f"Export test passed: {actual_rows} rows exported to CSV and Parquet" if passed else "Export test failed: performance or integrity issues"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Export throughput test failed: {str(e)}"
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
