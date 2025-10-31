"""Tests for connection and menu entry."""

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.errors import MenuTimeout
from q_sensor_lib.models import ConnectionState


def test_connect_enters_menu() -> None:
    """Test that connect() opens port, enters menu, and reads config."""
    fake_serial = FakeSerial(serial_number="TEST123", firmware_version="4.003")
    controller = SensorController()

    controller.connect(serial_port=fake_serial)

    assert controller.state == ConnectionState.CONFIG_MENU
    assert controller.sensor_id == "TEST123"

    config = controller.get_config()
    assert config.averaging == 125  # Default from FakeSerial
    assert config.adc_rate_hz == 125
    assert config.serial_number == "TEST123"
    assert config.firmware_version == "4.003"

    controller.disconnect()
    assert controller.state == ConnectionState.DISCONNECTED


def test_connect_with_quiet_mode() -> None:
    """Test connection with quiet mode enabled (no banner)."""
    fake_serial = FakeSerial(quiet_mode=True)
    controller = SensorController()

    controller.connect(serial_port=fake_serial)

    assert controller.state == ConnectionState.CONFIG_MENU
    controller.disconnect()


def test_double_connect_raises() -> None:
    """Test that connecting twice raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()

    controller.connect(serial_port=fake_serial)

    with pytest.raises(Exception):  # SerialIOError
        controller.connect(serial_port=fake_serial)

    controller.disconnect()


def test_get_config_snapshot() -> None:
    """Test reading config via '^' command."""
    fake_serial = FakeSerial()
    controller = SensorController()

    controller.connect(serial_port=fake_serial)

    config = controller.get_config()

    # Verify config fields parsed from CSV
    assert isinstance(config.averaging, int)
    assert isinstance(config.adc_rate_hz, int)
    assert config.mode in ("freerun", "polled")

    controller.disconnect()


def test_disconnect_from_menu() -> None:
    """Test clean disconnect from menu state."""
    fake_serial = FakeSerial()
    controller = SensorController()

    controller.connect(serial_port=fake_serial)
    assert controller.state == ConnectionState.CONFIG_MENU

    controller.disconnect()
    assert controller.state == ConnectionState.DISCONNECTED
    assert not fake_serial.is_open
