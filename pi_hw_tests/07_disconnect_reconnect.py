#!/usr/bin/env python3
"""Disconnect/reconnect test - verify connection lifecycle.

Tests full connection lifecycle:
- Connect to sensor
- Configure and acquire data
- Disconnect cleanly
- Reconnect to same sensor
- Verify config persists (or can be re-applied)
- Acquire data again

Expected behavior:
- disconnect() closes serial port, clears state
- reconnect() succeeds with same sensor
- Config must be re-applied (not persisted between connections)
- Acquisition works after reconnect
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
    parser = argparse.ArgumentParser(description="Disconnect/reconnect test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    out_dir = Path(__file__).parent / "out"
    logger = setup_logging("07_disconnect_reconnect", logs_dir)

    controller = None
    recorder = None
    passed = False
    metrics = {}
    fake_serial = None  # Keep reference for reconnection

    try:
        print(f"{'='*60}")
        print(f"Test: Disconnect/Reconnect Lifecycle")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"{'='*60}\n")

        # =====================================================================
        # First Connection
        # =====================================================================
        logger.info("=== First Connection ===")

        controller = SensorController()
        store = DataStore(max_rows=100000)

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake_serial = FakeSerial(serial_number="RECONNECT_TEST", quiet_mode=True)
            transport = Transport(fake_serial)
            controller._transport = transport
            controller._state = controller._state.__class__.DISCONNECTED
            controller._enter_menu()
            controller._config = controller._read_config_snapshot()
            controller._sensor_id = controller._config.serial_number
            controller._state = controller._state.__class__.CONFIG_MENU
        else:
            logger.info(f"Connecting to {args.port}...")
            controller.connect(port=args.port, baud=args.baud)

        sensor_id_1 = controller.sensor_id
        logger.info(f"✓ Connected to sensor: {sensor_id_1}")
        metrics["sensor_id_first"] = sensor_id_1

        # Configure
        logger.info("Configuring sensor (avg=125, rate=125 Hz, freerun)...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        config_1 = controller.set_mode("freerun")

        logger.info(f"✓ Config: {config_1.averaging} avg, {config_1.adc_rate_hz} Hz")

        # Start acquisition
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Wait for samples
        logger.info("Waiting for samples...")
        if not wait_for_samples(controller, min_samples=5, timeout=3.0):
            raise TimeoutError("No samples received in first connection")

        samples_1 = controller.buffer.count()
        logger.info(f"✓ Samples received: {samples_1}")
        metrics["samples_first_connection"] = samples_1

        # =====================================================================
        # Disconnect
        # =====================================================================
        logger.info("\n=== Disconnecting ===")

        # Stop acquisition first
        if controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            logger.info("Stopping acquisition...")
            controller.stop()

        # Disconnect
        logger.info("Disconnecting from sensor...")
        controller.disconnect()

        if controller.is_connected():
            raise ValueError("Controller still connected after disconnect()")

        logger.info("✓ Disconnected successfully")

        # Brief pause
        time.sleep(1.0)

        # =====================================================================
        # Second Connection (Reconnect)
        # =====================================================================
        logger.info("\n=== Second Connection (Reconnect) ===")

        # Create new controller
        controller = SensorController()

        if args.fake:
            # Reuse same FakeSerial instance (simulates same device)
            logger.info("Reconnecting to FakeSerial...")
            fake_serial.reset()  # Reset state
            transport = Transport(fake_serial)
            controller._transport = transport
            controller._state = controller._state.__class__.DISCONNECTED
            controller._enter_menu()
            controller._config = controller._read_config_snapshot()
            controller._sensor_id = controller._config.serial_number
            controller._state = controller._state.__class__.CONFIG_MENU
        else:
            logger.info(f"Reconnecting to {args.port}...")
            controller.connect(port=args.port, baud=args.baud)

        sensor_id_2 = controller.sensor_id
        logger.info(f"✓ Reconnected to sensor: {sensor_id_2}")
        metrics["sensor_id_second"] = sensor_id_2

        # Verify same sensor
        if sensor_id_1 != sensor_id_2:
            logger.warning(f"⚠ Sensor ID changed: {sensor_id_1} → {sensor_id_2}")
            metrics["sensor_id_match"] = "MISMATCH"
        else:
            logger.info("✓ Sensor ID matches")
            metrics["sensor_id_match"] = "MATCH"

        # Re-apply config (config does not persist across disconnects)
        logger.info("Re-applying config...")
        controller.set_averaging(125)
        controller.set_adc_rate(125)
        config_2 = controller.set_mode("freerun")

        logger.info(f"✓ Config: {config_2.averaging} avg, {config_2.adc_rate_hz} Hz")

        # Start acquisition again
        logger.info("Starting acquisition...")
        controller.start_acquisition()

        # Wait for samples
        logger.info("Waiting for samples...")
        if not wait_for_samples(controller, min_samples=5, timeout=3.0):
            raise TimeoutError("No samples received after reconnect")

        samples_2 = controller.buffer.count()
        logger.info(f"✓ Samples received: {samples_2}")
        metrics["samples_second_connection"] = samples_2

        # =====================================================================
        # Record data from second connection
        # =====================================================================
        logger.info("\n=== Recording Data ===")

        recorder = DataRecorder(controller, store, poll_interval_s=0.2)
        recorder.start()

        time.sleep(2.0)

        recorder.stop()

        df = store.get_dataframe()
        final_rows = len(df)

        logger.info(f"Final DataFrame rows: {final_rows}")
        metrics["final_rows"] = final_rows

        if final_rows < 10:
            raise ValueError(f"Only {final_rows} rows collected (expected >=10)")

        logger.info("✓ Data recording successful after reconnect")

        # Export
        csv_path = export_dataframe_csv(store, out_dir, "07_disconnect_reconnect")
        logger.info(f"Data exported to {csv_path}")

        passed = True
        message = "Disconnect/reconnect lifecycle completed successfully"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Disconnect/reconnect test failed: {str(e)}"
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
