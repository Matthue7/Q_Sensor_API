"""Tests for configuration get/set operations."""

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.errors import InvalidConfigValue
from q_sensor_lib.models import ConnectionState


def test_set_averaging_valid() -> None:
    """Test setting averaging to valid value."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    config = controller.set_averaging(250)

    assert config.averaging == 250
    controller.disconnect()


def test_set_averaging_invalid_rejects() -> None:
    """Test that invalid averaging value raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Too small
    with pytest.raises(InvalidConfigValue):
        controller.set_averaging(0)

    # Too large
    with pytest.raises(InvalidConfigValue):
        controller.set_averaging(100000)

    controller.disconnect()


def test_set_adc_rate_valid() -> None:
    """Test setting ADC rate to valid value."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    config = controller.set_adc_rate(125)

    assert config.adc_rate_hz == 125
    controller.disconnect()


def test_set_adc_rate_invalid() -> None:
    """Test that invalid ADC rate raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    with pytest.raises(InvalidConfigValue):
        controller.set_adc_rate(99)  # Not in valid set

    controller.disconnect()


def test_set_mode_freerun() -> None:
    """Test setting mode to freerun."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    config = controller.set_mode("freerun")

    assert config.mode == "freerun"
    assert fake_serial.operating_mode == "0"

    controller.disconnect()


def test_set_mode_polled_with_tag() -> None:
    """Test setting mode to polled with valid TAG."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    config = controller.set_mode("polled", tag="B")

    assert config.mode == "polled"
    assert config.tag == "B"
    assert fake_serial.operating_mode == "1"
    assert fake_serial.tag == "B"

    controller.disconnect()


def test_set_mode_polled_without_tag_raises() -> None:
    """Test that polled mode without TAG raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    with pytest.raises(InvalidConfigValue):
        controller.set_mode("polled", tag=None)

    controller.disconnect()


def test_set_mode_polled_invalid_tag() -> None:
    """Test that polled mode with invalid TAG raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Lowercase not allowed
    with pytest.raises(InvalidConfigValue):
        controller.set_mode("polled", tag="a")

    # Multi-character not allowed
    with pytest.raises(InvalidConfigValue):
        controller.set_mode("polled", tag="AB")

    controller.disconnect()


def test_set_config_outside_menu_raises() -> None:
    """Test that setting config outside menu state raises error."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Start acquisition
    controller.start_acquisition()
    assert controller.state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED)

    # Try to set averaging while acquiring
    with pytest.raises(Exception):  # SerialIOError
        controller.set_averaging(200)

    controller.disconnect()


def test_multiple_config_changes() -> None:
    """Test chaining multiple configuration changes."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_averaging(100)
    controller.set_adc_rate(62)
    config = controller.set_mode("polled", tag="C")

    assert config.averaging == 100
    assert config.adc_rate_hz == 62
    assert config.mode == "polled"
    assert config.tag == "C"

    controller.disconnect()
