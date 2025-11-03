#!/usr/bin/env python3
"""TempC/Vin presence test - verify optional field handling.

Tests firmware conditional fields and schema normalization:
- When firmware provides TempC/Vin: verify non-null in >=90% of rows
- When firmware omits TempC/Vin: verify NaN in >=90% of rows
- Validates schema.py fills missing fields with NaN

Expected behavior:
- FakeSerial: TempC=21.5 and Vin=12.0 always present
- Real hardware: depends on firmware flags (check config)
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_store import DataRecorder, DataStore
from pi_hw_tests.common import (
    TestResult,
    export_dataframe_csv,
    robust_teardown,
    setup_logging,
)
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="TempC/Vin presence test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--duration", type=float, default=5.0, help="Acquisition duration (seconds)")
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    out_dir = Path(__file__).parent / "out"
    logger = setup_logging("05_temp_vin_presence", logs_dir)

    controller = None
    recorder = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: TempC/Vin Presence (Optional Fields)")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Duration: {args.duration}s")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()
        store = DataStore(max_rows=100000)

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial (TempC=21.5, Vin=12.0 always present)")
            fake = FakeSerial(serial_number="TEMPVIN_TEST", quiet_mode=True)
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

        logger.info(f"Config: {config.averaging} avg, {config.adc_rate_hz} Hz, mode={config.mode}")

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Start recorder
        logger.info("Starting recorder...")
        recorder = DataRecorder(controller, store, poll_interval_s=0.2)
        recorder.start()

        # Wait for duration
        logger.info(f"Recording for {args.duration}s...")
        time.sleep(args.duration)

        # Stop recorder
        logger.info("Stopping recorder...")
        recorder.stop()

        # Analyze DataFrame
        df = store.get_dataframe()
        total_rows = len(df)

        logger.info(f"Total rows: {total_rows}")

        if total_rows == 0:
            raise ValueError("No rows collected")

        # Check TempC column
        tempc_present_count = df["TempC"].notna().sum()
        tempc_present_pct = (tempc_present_count / total_rows) * 100

        # Check Vin column
        vin_present_count = df["Vin"].notna().sum()
        vin_present_pct = (vin_present_count / total_rows) * 100

        logger.info(f"TempC present: {tempc_present_count}/{total_rows} ({tempc_present_pct:.1f}%)")
        logger.info(f"Vin present: {vin_present_count}/{total_rows} ({vin_present_pct:.1f}%)")

        metrics["total_rows"] = total_rows
        metrics["tempc_present_pct"] = round(tempc_present_pct, 1)
        metrics["vin_present_pct"] = round(vin_present_pct, 1)

        # Determine expected behavior
        if args.fake:
            # FakeSerial always provides TempC and Vin
            expected_present = True
        else:
            # Real hardware: heuristic - if >50% have data, firmware is providing it
            expected_present = (tempc_present_pct > 50) or (vin_present_pct > 50)

        logger.info(f"Expected behavior: {'TempC/Vin provided by firmware' if expected_present else 'TempC/Vin omitted by firmware'}")

        # Validate
        if expected_present:
            # Should be present in >=90% of rows
            tempc_ok = tempc_present_pct >= 90
            vin_ok = vin_present_pct >= 90

            if not tempc_ok:
                logger.error(f"✗ TempC present in only {tempc_present_pct:.1f}% of rows (expected >=90%)")
                metrics["tempc_test"] = "FAIL"
            else:
                logger.info(f"✓ TempC present in {tempc_present_pct:.1f}% of rows")
                metrics["tempc_test"] = "PASS"

            if not vin_ok:
                logger.error(f"✗ Vin present in only {vin_present_pct:.1f}% of rows (expected >=90%)")
                metrics["vin_test"] = "FAIL"
            else:
                logger.info(f"✓ Vin present in {vin_present_pct:.1f}% of rows")
                metrics["vin_test"] = "PASS"

            passed = tempc_ok and vin_ok
            message = "TempC/Vin present as expected" if passed else "TempC/Vin missing in some rows"

        else:
            # Should be NaN in >=90% of rows
            tempc_nan_pct = 100 - tempc_present_pct
            vin_nan_pct = 100 - vin_present_pct

            tempc_ok = tempc_nan_pct >= 90
            vin_ok = vin_nan_pct >= 90

            if not tempc_ok:
                logger.warning(f"✗ TempC is NaN in only {tempc_nan_pct:.1f}% of rows (expected >=90%)")
                metrics["tempc_test"] = "FAIL"
            else:
                logger.info(f"✓ TempC is NaN in {tempc_nan_pct:.1f}% of rows")
                metrics["tempc_test"] = "PASS"

            if not vin_ok:
                logger.warning(f"✗ Vin is NaN in only {vin_nan_pct:.1f}% of rows (expected >=90%)")
                metrics["vin_test"] = "FAIL"
            else:
                logger.info(f"✓ Vin is NaN in {vin_nan_pct:.1f}% of rows")
                metrics["vin_test"] = "PASS"

            passed = tempc_ok and vin_ok
            message = "TempC/Vin correctly filled with NaN" if passed else "TempC/Vin unexpectedly present"

        # Export
        csv_path = export_dataframe_csv(store, out_dir, "05_temp_vin_presence")
        logger.info(f"Data exported to {csv_path}")

        # Sample values
        if expected_present and total_rows > 0:
            sample_row = df.iloc[0]
            logger.info(f"Sample: TempC={sample_row['TempC']}, Vin={sample_row['Vin']}")
            metrics["sample_tempc"] = float(sample_row["TempC"]) if pd.notna(sample_row["TempC"]) else None
            metrics["sample_vin"] = float(sample_row["Vin"]) if pd.notna(sample_row["Vin"]) else None

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"TempC/Vin test failed: {str(e)}"
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
