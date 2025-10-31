"""Tests for freerun streaming mode."""

import time

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import ConnectionState


def test_start_freerun_acquisition() -> None:
    """Test starting acquisition in freerun mode."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Set to freerun mode
    controller.set_mode("freerun")

    # Start acquisition
    controller.start_acquisition()

    assert controller.state == ConnectionState.ACQ_FREERUN

    # Give time for thread to start and get some readings
    time.sleep(2.0)

    # Check that readings are being buffered
    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0, "Should have received some freerun readings"

    # Verify reading structure
    first_reading = readings[0]
    assert first_reading.mode == "freerun"
    assert "value" in first_reading.data
    assert isinstance(first_reading.data["value"], float)

    controller.disconnect()


def test_freerun_reading_rate() -> None:
    """Test that freerun readings arrive at expected rate."""
    fake_serial = FakeSerial()
    fake_serial.averaging = 125
    fake_serial.adc_rate_hz = 125  # Should get ~1 reading/second

    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    # Wait for approximately 3 samples
    time.sleep(3.5)

    readings = controller.read_buffer_snapshot()

    # Should have received 3-4 readings (allow some timing variance)
    assert 2 <= len(readings) <= 5, f"Expected 2-5 readings, got {len(readings)}"

    controller.disconnect()


def test_freerun_with_temp_and_vin() -> None:
    """Test freerun with temperature and voltage outputs enabled."""
    fake_serial = FakeSerial()
    fake_serial.include_temp = True
    fake_serial.include_vin = True

    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(2.0)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    # Check that temp and vin are present
    first_reading = readings[0]
    assert "TempC" in first_reading.data
    assert "Vin" in first_reading.data
    assert isinstance(first_reading.data["TempC"], float)
    assert isinstance(first_reading.data["Vin"], float)

    controller.disconnect()


def test_freerun_buffer_overflow() -> None:
    """Test that ring buffer handles overflow correctly."""
    fake_serial = FakeSerial()
    fake_serial.averaging = 10  # Fast sampling
    fake_serial.adc_rate_hz = 125

    # Small buffer
    controller = SensorController(buffer_size=5)
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    # Wait for buffer to overflow
    time.sleep(2.0)

    readings = controller.read_buffer_snapshot()

    # Buffer should be at max capacity
    assert len(readings) <= 5, "Buffer should not exceed maxlen"

    controller.disconnect()


def test_clear_buffer() -> None:
    """Test clearing reading buffer."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(1.5)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.clear_buffer()

    readings_after = controller.read_buffer_snapshot()
    assert len(readings_after) == 0

    controller.disconnect()


def test_freerun_sensor_id_in_readings() -> None:
    """Test that readings contain correct sensor ID."""
    fake_serial = FakeSerial(serial_number="SENSOR999")
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(1.5)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    for reading in readings:
        assert reading.sensor_id == "SENSOR999"

    controller.disconnect()
