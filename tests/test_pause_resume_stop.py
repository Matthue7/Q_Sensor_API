"""Tests for pause/resume/stop operations."""

import time

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import ConnectionState


def test_pause_from_freerun() -> None:
    """Test pausing freerun acquisition."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    assert controller.state == ConnectionState.ACQ_FREERUN

    time.sleep(1.0)

    # Pause
    controller.pause()

    assert controller.state == ConnectionState.PAUSED

    # No new readings should arrive
    count_before = len(controller.read_buffer_snapshot())
    time.sleep(1.0)
    count_after = len(controller.read_buffer_snapshot())

    assert count_after == count_before, "Readings should stop after pause"

    controller.disconnect()


def test_resume_from_paused() -> None:
    """Test resuming acquisition from paused state."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(1.0)

    # Pause
    controller.pause()
    assert controller.state == ConnectionState.PAUSED

    controller.clear_buffer()

    # Resume
    controller.resume()

    assert controller.state == ConnectionState.ACQ_FREERUN

    # Should get new readings
    time.sleep(1.5)
    readings = controller.read_buffer_snapshot()

    assert len(readings) > 0, "Should receive readings after resume"

    controller.disconnect()


def test_stop_from_freerun() -> None:
    """Test stopping acquisition and returning to menu."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    assert controller.state == ConnectionState.ACQ_FREERUN

    time.sleep(1.0)

    # Stop
    controller.stop()

    assert controller.state == ConnectionState.CONFIG_MENU

    # Should be able to reconfigure
    config = controller.set_averaging(200)
    assert config.averaging == 200

    controller.disconnect()


def test_stop_from_polled() -> None:
    """Test stopping polled acquisition."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")
    controller.start_acquisition(poll_hz=2.0)

    assert controller.state == ConnectionState.ACQ_POLLED

    time.sleep(1.5)

    # Stop
    controller.stop()

    assert controller.state == ConnectionState.CONFIG_MENU

    controller.disconnect()


def test_pause_resume_preserves_mode() -> None:
    """Test that pause/resume preserves operating mode."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Set polled mode
    controller.set_mode("polled", tag="B")
    controller.start_acquisition(poll_hz=2.0)

    assert controller.state == ConnectionState.ACQ_POLLED

    time.sleep(1.0)

    # Pause
    controller.pause()

    # Resume should restore polled mode
    controller.resume()

    assert controller.state == ConnectionState.ACQ_POLLED

    # Should get polled readings
    time.sleep(1.5)
    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0
    assert readings[0].mode == "polled"

    controller.disconnect()


def test_cannot_pause_from_menu() -> None:
    """Test that pause() raises error when not acquiring."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # In menu state
    assert controller.state == ConnectionState.CONFIG_MENU

    with pytest.raises(Exception):  # SerialIOError
        controller.pause()

    controller.disconnect()


def test_cannot_resume_from_menu() -> None:
    """Test that resume() raises error when not paused."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # In menu state
    assert controller.state == ConnectionState.CONFIG_MENU

    with pytest.raises(Exception):  # SerialIOError
        controller.resume()

    controller.disconnect()


def test_stop_from_paused() -> None:
    """Test stopping from paused state."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(1.0)

    controller.pause()
    assert controller.state == ConnectionState.PAUSED

    # Stop from paused
    controller.stop()

    assert controller.state == ConnectionState.CONFIG_MENU

    controller.disconnect()


def test_multiple_pause_resume_cycles() -> None:
    """Test multiple pause/resume cycles."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")

    for _ in range(3):
        controller.start_acquisition()
        time.sleep(0.5)

        controller.pause()
        time.sleep(0.3)

        controller.resume()
        time.sleep(0.5)

    # Should still be running
    assert controller.state == ConnectionState.ACQ_FREERUN

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.disconnect()
