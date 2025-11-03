#!/usr/bin/env python3
"""30-minute burn-in test - long-duration stability validation.

Tests extended acquisition stability:
- Run freerun acquisition for 30 minutes (default)
- Monitor sample rate over time
- Check for gaps, timeouts, or buffer issues
- Verify consistent data quality throughout

Expected behavior:
- Sample rate should remain stable (~15 Hz for 125/125 config)
- No timeouts or exceptions
- DataFrame should grow continuously
- Export large dataset successfully

This is a long-running test. Use --duration to customize (e.g., --duration=60 for 1 minute dry-run).
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (
    RollingRateEstimator,
    TestResult,
    export_dataframe_csv,
    robust_teardown,
    setup_logging,
    wait_for_samples,
)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="30-minute burn-in test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--duration", type=float, default=1800.0, help="Burn-in duration (seconds, default 1800s = 30min)")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    out_dir = Path(__file__).parent / "out"
    logger = setup_logging("08_burn_in_30min", logs_dir)

    controller = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Burn-In ({args.duration}s)")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Duration: {args.duration:.1f}s ({args.duration/60:.1f} minutes)")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()
        store = DataStore(max_rows=500000)  # Large buffer for long test

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="BURNIN_TEST", quiet_mode=True)
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

        logger.info(f"Config: {config.averaging} avg, {config.adc_rate_hz} Hz")
        logger.info(f"Expected sample period: {config.sample_period_s:.3f}s (~{1/config.sample_period_s:.1f} Hz)")

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Wait for initial samples
        logger.info("Waiting for initial samples...")
        if not wait_for_samples(controller, min_samples=5, timeout=3.0):
            raise TimeoutError("No samples received after start")

        logger.info("✓ Acquisition running")

        # Start recorder
        logger.info("Starting recorder...")
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)
        recorder.start()

        # Monitor progress
        logger.info(f"\nStarting burn-in for {args.duration:.1f}s...")
        logger.info("Monitoring sample rate every 60s...\n")

        start_time = time.time()
        last_report_time = start_time
        report_interval = 60.0  # Report every 60s

        rate_estimator = RollingRateEstimator(window_size=50)
        sample_rates = []

        while True:
            elapsed = time.time() - start_time

            if elapsed >= args.duration:
                break

            # Check recorder is still running
            if not recorder.is_running():
                raise RuntimeError("Recorder stopped unexpectedly")

            # Report progress
            if time.time() - last_report_time >= report_interval:
                df = store.get_dataframe()
                row_count = len(df)

                # Calculate current rate from recent samples
                if row_count > 0:
                    recent_df = store.get_recent(seconds=30)
                    if len(recent_df) > 1:
                        # Estimate rate from recent data
                        import pandas as pd
                        timestamps = pd.to_datetime(recent_df["timestamp"], format="ISO8601", utc=True)
                        time_span = (timestamps.max() - timestamps.min()).total_seconds()
                        if time_span > 0:
                            current_rate = (len(recent_df) - 1) / time_span
                            sample_rates.append(current_rate)
                        else:
                            current_rate = 0.0
                    else:
                        current_rate = 0.0
                else:
                    current_rate = 0.0

                logger.info(f"[{elapsed/60:.1f}m] Rows: {row_count}, Rate: {current_rate:.1f} Hz")
                last_report_time = time.time()

            time.sleep(5.0)  # Check every 5s

        # Final report
        logger.info("\n=== Burn-In Complete ===")

        # Stop recorder
        logger.info("Stopping recorder...")
        recorder.stop()

        # Analyze results
        df = store.get_dataframe()
        total_rows = len(df)

        logger.info(f"Total rows collected: {total_rows}")
        metrics["total_rows"] = total_rows

        # Expected rows
        expected_rate_hz = 1.0 / config.sample_period_s
        expected_rows = args.duration * expected_rate_hz

        logger.info(f"Expected rows (~{expected_rate_hz:.1f} Hz): {expected_rows:.0f}")
        metrics["expected_rows"] = int(expected_rows)

        # Check within 20% tolerance
        min_expected = expected_rows * 0.8
        max_expected = expected_rows * 1.2

        if total_rows < min_expected:
            logger.warning(f"⚠ Row count below expected range ({min_expected:.0f}-{max_expected:.0f})")
            metrics["row_count_status"] = "LOW"
        elif total_rows > max_expected:
            logger.warning(f"⚠ Row count above expected range ({min_expected:.0f}-{max_expected:.0f})")
            metrics["row_count_status"] = "HIGH"
        else:
            logger.info(f"✓ Row count within expected range ({min_expected:.0f}-{max_expected:.0f})")
            metrics["row_count_status"] = "OK"

        # Average sample rate
        if len(sample_rates) > 0:
            avg_rate = sum(sample_rates) / len(sample_rates)
            logger.info(f"Average sample rate: {avg_rate:.2f} Hz")
            metrics["avg_sample_rate_hz"] = round(avg_rate, 2)
        else:
            avg_rate = 0.0

        # Data quality check - verify no large gaps
        if total_rows > 1:
            import pandas as pd
            timestamps = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
            time_diffs = timestamps.diff().dt.total_seconds().dropna()

            max_gap_s = time_diffs.max()
            median_gap_s = time_diffs.median()

            logger.info(f"Timestamp gaps - Median: {median_gap_s:.3f}s, Max: {max_gap_s:.3f}s")
            metrics["median_gap_s"] = round(median_gap_s, 3)
            metrics["max_gap_s"] = round(max_gap_s, 3)

            # Check for excessive gaps (>5x expected period)
            expected_period = config.sample_period_s
            if max_gap_s > expected_period * 5:
                logger.warning(f"⚠ Large gap detected: {max_gap_s:.3f}s (>5x expected period)")
                metrics["gap_status"] = "LARGE_GAPS"
            else:
                logger.info("✓ No excessive gaps detected")
                metrics["gap_status"] = "OK"

        # Export
        logger.info("\nExporting data...")
        csv_path = export_dataframe_csv(store, out_dir, "08_burn_in_30min")
        logger.info(f"Data exported to {csv_path}")

        # Success criteria
        success_criteria = [
            total_rows >= min_expected,
            total_rows <= max_expected,
            metrics.get("gap_status") == "OK" if "gap_status" in metrics else True
        ]

        passed = all(success_criteria)
        message = f"Burn-in completed: {total_rows} rows over {args.duration/60:.1f} minutes" if passed else "Burn-in failed: row count or gap issues"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Burn-in test failed: {str(e)}"
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
