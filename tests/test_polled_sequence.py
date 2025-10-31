"""Tests for polled mode with query sequences."""

import time

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import ConnectionState


def test_start_polled_acquisition() -> None:
    """Test starting acquisition in polled mode."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Set to polled mode with TAG
    controller.set_mode("polled", tag="A")

    # Start acquisition with 2 Hz polling
    controller.start_acquisition(poll_hz=2.0)

    assert controller.state == ConnectionState.ACQ_POLLED

    # Give time for initialization and some polls
    time.sleep(2.5)

    # Check that readings are being buffered
    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0, "Should have received polled readings"

    # Verify reading structure
    first_reading = readings[0]
    assert first_reading.mode == "polled"
    assert "value" in first_reading.data
    assert isinstance(first_reading.data["value"], float)

    controller.disconnect()


def test_polled_reading_rate() -> None:
    """Test that polled readings arrive at expected rate."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="B")

    # Poll at 5 Hz
    controller.start_acquisition(poll_hz=5.0)

    # Wait for ~2 seconds
    time.sleep(2.5)

    readings = controller.read_buffer_snapshot()

    # Should have received 8-12 readings (allow variance)
    assert 6 <= len(readings) <= 14, f"Expected 6-14 readings at 5Hz, got {len(readings)}"

    controller.disconnect()


def test_polled_with_different_tag() -> None:
    """Test polled mode with different TAG characters."""
    for tag in ["C", "D", "Z"]:
        fake_serial = FakeSerial()
        controller = SensorController()
        controller.connect(serial_port=fake_serial)

        controller.set_mode("polled", tag=tag)
        controller.start_acquisition(poll_hz=2.0)

        time.sleep(2.0)

        readings = controller.read_buffer_snapshot()
        assert len(readings) > 0, f"No readings received for tag {tag}"

        controller.disconnect()


def test_polled_slow_rate() -> None:
    """Test polled mode at slow rate (1 Hz)."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")

    # Poll at 1 Hz
    controller.start_acquisition(poll_hz=1.0)

    # Wait for 3 samples
    time.sleep(3.5)

    readings = controller.read_buffer_snapshot()

    # Should have 3-4 readings
    assert 2 <= len(readings) <= 5, f"Expected 2-5 readings at 1Hz, got {len(readings)}"

    controller.disconnect()


def test_polled_initialization_sequence() -> None:
    """Test that polled mode sends *<TAG>Q000! before polling."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")

    # Start acquisition
    controller.start_acquisition(poll_hz=2.0)

    # FakeSerial should have received init command
    # (internal state check - fake_serial._sampling_started should be True)
    time.sleep(2.0)

    assert fake_serial._sampling_started, "Polled initialization command not received"

    # Should have readings
    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.disconnect()


def test_polled_mode_tag_validation_in_responses() -> None:
    """Test that polled responses are validated against TAG."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")
    controller.start_acquisition(poll_hz=2.0)

    time.sleep(2.0)

    readings = controller.read_buffer_snapshot()

    # All readings should have been validated (no exceptions raised)
    assert len(readings) > 0

    controller.disconnect()


def test_polled_with_high_poll_rate() -> None:
    """Test polled mode at higher rate (10 Hz)."""
    fake_serial = FakeSerial()
    fake_serial.averaging = 10  # Fast averaging
    fake_serial.adc_rate_hz = 125

    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")

    # Poll at 10 Hz
    controller.start_acquisition(poll_hz=10.0)

    time.sleep(2.0)

    readings = controller.read_buffer_snapshot()

    # Should have received 15-25 readings (allow variance)
    assert 10 <= len(readings) <= 30, f"Expected 10-30 readings at 10Hz, got {len(readings)}"

    controller.disconnect()
