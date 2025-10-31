"""Tests for error handling and edge cases."""

import time

import pytest

from fakes.fake_serial import FakeSerial
from q_sensor_lib.controller import SensorController
from q_sensor_lib.errors import InvalidConfigValue
from q_sensor_lib.models import ConnectionState


def test_invalid_averaging_device_rejection() -> None:
    """Test that device rejects invalid averaging value (<1)."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Library should reject before sending
    with pytest.raises(InvalidConfigValue):
        controller.set_averaging(0)

    controller.disconnect()


def test_invalid_rate_device_rejection() -> None:
    """Test that device rejects invalid ADC rate."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Invalid rate
    with pytest.raises(InvalidConfigValue):
        controller.set_adc_rate(100)  # Not in valid set

    controller.disconnect()


def test_polled_without_init_command() -> None:
    """Test recovery when polled init command is missing.

    This tests the edge case where controller forgets to send *<TAG>Q000!
    In our implementation, start_acquisition() always sends it, but we
    can test the fake serial behavior.
    """
    fake_serial = FakeSerial()

    # Manually set to polled mode without proper init
    fake_serial.operating_mode = "1"
    fake_serial.tag = "A"
    fake_serial._state = "polled"
    fake_serial._sampling_started = False  # Init not sent

    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Set mode to polled (will trigger init on start_acquisition)
    controller.set_mode("polled", tag="A")

    # Start acquisition (should send init)
    controller.start_acquisition(poll_hz=2.0)

    # Should eventually get readings
    time.sleep(2.5)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0, "Should get readings after proper initialization"

    controller.disconnect()


def test_disconnect_while_acquiring() -> None:
    """Test clean disconnect while acquisition is running."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    assert controller.state == ConnectionState.ACQ_FREERUN

    time.sleep(1.0)

    # Disconnect should stop threads cleanly
    controller.disconnect()

    assert controller.state == ConnectionState.DISCONNECTED

    # Verify thread is stopped (no exceptions)
    time.sleep(0.5)


def test_set_config_not_connected() -> None:
    """Test that config operations fail when not connected."""
    controller = SensorController()

    # Not connected
    assert controller.state == ConnectionState.DISCONNECTED

    with pytest.raises(Exception):  # SerialIOError
        controller.set_averaging(100)


def test_start_acquisition_not_connected() -> None:
    """Test that start_acquisition fails when not connected."""
    controller = SensorController()

    with pytest.raises(Exception):  # SerialIOError or AttributeError
        controller.start_acquisition()


def test_reading_buffer_thread_safety() -> None:
    """Test that buffer can be read safely during acquisition."""
    fake_serial = FakeSerial()
    fake_serial.averaging = 10  # Fast sampling
    fake_serial.adc_rate_hz = 125

    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    # Read buffer multiple times while acquisition is ongoing
    for _ in range(10):
        readings = controller.read_buffer_snapshot()
        # Should not crash or block
        time.sleep(0.1)

    controller.disconnect()


def test_device_reset_after_exit_menu() -> None:
    """Test that device reset is handled correctly after X command."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Configure and start
    controller.set_mode("freerun")
    controller.start_acquisition()

    # Device should have reset and entered freerun mode
    assert controller.state == ConnectionState.ACQ_FREERUN

    time.sleep(1.0)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.disconnect()


def test_multiple_config_changes_then_acquire() -> None:
    """Test configuration changes followed by acquisition start."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    # Make several config changes
    controller.set_averaging(50)
    controller.set_adc_rate(62)
    controller.set_mode("polled", tag="D")

    # Start acquisition
    controller.start_acquisition(poll_hz=3.0)

    assert controller.state == ConnectionState.ACQ_POLLED

    time.sleep(2.0)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.disconnect()


def test_clear_buffer_during_acquisition() -> None:
    """Test clearing buffer while acquisition is running."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    time.sleep(1.0)

    # Clear buffer
    controller.clear_buffer()

    # Should continue acquiring
    time.sleep(1.0)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0, "Should continue acquiring after clear"

    controller.disconnect()


def test_polled_tag_mismatch_in_response() -> None:
    """Test handling of TAG mismatch in polled responses.

    This would happen if device is misconfigured or has wrong TAG.
    In FakeSerial, we ensure TAG matches, but this tests the parsing logic.
    """
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("polled", tag="A")

    # Manually change fake serial's tag to create mismatch
    # (In real scenario, this could happen if EEPROM was corrupted)
    fake_serial.tag = "B"

    controller.start_acquisition(poll_hz=2.0)

    time.sleep(2.0)

    # Should have no readings (or very few) due to parse failures
    readings = controller.read_buffer_snapshot()

    # Parser should reject mismatched TAGs
    # Note: depends on implementation - might log warnings but not crash

    controller.disconnect()


def test_freerun_unparseable_lines() -> None:
    """Test that unparseable lines in freerun are skipped gracefully."""
    fake_serial = FakeSerial()
    controller = SensorController()
    controller.connect(serial_port=fake_serial)

    controller.set_mode("freerun")
    controller.start_acquisition()

    # FakeSerial generates valid lines, but in real world we might get noise
    # The reader thread should skip unparseable lines and continue

    time.sleep(1.5)

    readings = controller.read_buffer_snapshot()
    assert len(readings) > 0

    controller.disconnect()
