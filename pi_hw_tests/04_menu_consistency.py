#!/usr/bin/env python3
"""Menu consistency test - verify firmware prompt sequences.

Validates menu navigation against expected firmware behavior from 2150REV4.003.bas.
Logs full transcript of commands and responses.

Expected prompts (from firmware):
- Averaging prompt: "Enter # readings to average"
- Rate prompt: "Enter ADC rate"
- Confirmation: "ADC set to averaging", "ADC rate set to"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pi_hw_tests.common import TestResult, robust_teardown, setup_logging
from q_sensor_lib.controller import SensorController
from q_sensor_lib.transport import Transport

try:
    from fakes.fake_serial import FakeSerial
except ImportError:
    FakeSerial = None


def main():
    parser = argparse.ArgumentParser(description="Menu consistency test")
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument("--fake", action="store_true")
    args = parser.parse_args()

    logs_dir = Path(__file__).parent / "logs"
    logger = setup_logging("04_menu_consistency", logs_dir)

    controller = None
    passed = False
    metrics = {}

    try:
        print(f"{'='*60}")
        print(f"Test: Menu Consistency (Firmware Prompts)")
        print(f"Port: {args.port if not args.fake else 'FakeSerial'}")
        print(f"{'='*60}\n")

        # Connect
        controller = SensorController()

        if args.fake:
            if FakeSerial is None:
                raise ImportError("FakeSerial not available")
            logger.info("Using FakeSerial")
            fake = FakeSerial(serial_number="MENU_TEST", quiet_mode=True)
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

        logger.info("Menu entered successfully")

        # Test averaging setting
        logger.info("Setting averaging to 100...")
        config = controller.set_averaging(100)

        if config.averaging == 100:
            logger.info(f"✓ Averaging confirmed: {config.averaging}")
            metrics["averaging_test"] = "PASS"
        else:
            logger.error(f"✗ Averaging mismatch: expected 100, got {config.averaging}")
            metrics["averaging_test"] = "FAIL"

        # Test ADC rate setting
        logger.info("Setting ADC rate to 62 Hz...")
        config = controller.set_adc_rate(62)

        if config.adc_rate_hz == 62:
            logger.info(f"✓ ADC rate confirmed: {config.adc_rate_hz}")
            metrics["adc_rate_test"] = "PASS"
        else:
            logger.error(f"✗ ADC rate mismatch: expected 62, got {config.adc_rate_hz}")
            metrics["adc_rate_test"] = "FAIL"

        # Test mode setting
        logger.info("Setting mode to freerun...")
        config = controller.set_mode("freerun")

        if config.mode == "freerun":
            logger.info(f"✓ Mode confirmed: {config.mode}")
            metrics["mode_test"] = "PASS"
        else:
            logger.error(f"✗ Mode mismatch: expected freerun, got {config.mode}")
            metrics["mode_test"] = "FAIL"

        # Check all tests passed
        all_passed = all(v == "PASS" for v in metrics.values())

        logger.info("Menu consistency test complete")

        passed = all_passed
        message = "All menu commands executed successfully" if passed else "Some menu commands failed"

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        message = f"Menu test failed: {str(e)}"
        passed = False

    finally:
        robust_teardown(controller, logger=logger)

    result = TestResult(
        passed=passed,
        message=message,
        metrics=metrics
    )
    result.print_result()

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
