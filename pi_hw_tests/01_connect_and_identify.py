#!/usr/bin/env python3
"""Connect to sensor and verify identification.

Tests:
- Connection to real hardware or FakeSerial
- Sensor ID retrieval
- Menu prompt detection (firmware verification)
- Clean disconnection
"""

import argparse
import sys
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from pi_hw_tests.common import TestResult, robust_teardown, setup_logging
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

# Optional FakeSerial
try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Connect and identify sensor")
    parser.add_argument("--port", default="/dev/ttyUSB0",
                       help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=9600,
                       help="Baud rate (default: 9600)")
    parser.add_argument("--fake", action="store_true",
                       help="Use FakeSerial instead of real hardware")
    args = parser.parse_args()

    # Setup logging
    logs_dir = Path(__file__).parent / "logs"
    logger = setup_logging("01_connect_identify", logs_dir)

    controller = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Connect and Identify Sensor")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"Baud: {args.baud}")
        print(f"{'='*60}\n")

        # Create controller
        logger.info("Creating SensorController...")
        controller = SensorController()

        # Connect
        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available (install fakes package)")

            logger.info("Using FakeSerial (dry run mode)")
            fake = FakeSerial(serial_number="HWTEST_FAKE", quiet_mode=True)
            transport = Transport(fake)
            controller._transport = transport
            controller._state = controller._state.__class__.DISCONNECTED

            # Manually trigger connect logic
            controller._enter_menu()
            controller._config = controller._read_config_snapshot()
            controller._sensor_id = controller._config.serial_number
            controller._state = controller._state.__class__.CONFIG_MENU

        else:
            logger.info(f"Connecting to {args.port} at {args.baud} baud...")
            controller.connect(port=args.port, baud=args.baud)

        # Verify connected
        if not controller.is_connected():
            raise RuntimeError("Connection failed - controller reports not connected")

        logger.info("Connection successful")

        # Get sensor ID
        sensor_id = controller.sensor_id
        logger.info(f"Sensor ID: {sensor_id}")
        metrics["sensor_id"] = sensor_id

        # Get config to verify menu prompt was received
        config = controller.get_config()
        logger.info(f"Config read successfully: {config.mode} mode, "
                   f"{config.averaging} avg, {config.adc_rate_hz} Hz")

        metrics["mode"] = config.mode
        metrics["averaging"] = config.averaging
        metrics["adc_rate_hz"] = config.adc_rate_hz

        # Verify sensor ID is non-empty
        if not sensor_id or sensor_id == "unknown":
            raise ValueError(f"Invalid sensor ID: {sensor_id}")

        # Disconnect
        logger.info("Disconnecting...")
        controller.disconnect()

        if controller.is_connected():
            raise RuntimeError("Disconnect failed - controller still connected")

        logger.info("Disconnect successful")

        passed = True
        message = f"Successfully connected to sensor '{sensor_id}'"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Connection test failed: {str(e)}"
        passed = False

    finally:
        # Robust teardown
        robust_teardown(controller, logger=logger)

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
