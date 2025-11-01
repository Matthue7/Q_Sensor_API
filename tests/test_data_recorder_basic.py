"""Tests for DataFrame recording layer (DataStore and DataRecorder).

These tests use synthetic data and mock controllers - no hardware required.
Tests verify:
- Schema consistency (all columns present, NaN handling for optional fields)
- Thread safety
- Freerun and polled mode handling
- Timing and sample rate estimation
- Export functionality
"""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from typing import List

import pandas as pd
import pytest

from data_store import SCHEMA, DataRecorder, DataStore, reading_to_row
from q_sensor_lib.models import Reading


class FakeController:
    """Mock SensorController that generates synthetic readings for testing.

    Simulates both freerun (~15 Hz) and polled modes by generating readings
    with incrementing timestamps.
    """

    def __init__(self, mode: str = "freerun", include_temp: bool = False, include_vin: bool = False):
        """Initialize fake controller.

        Args:
            mode: "freerun" or "polled"
            include_temp: Include TempC in synthetic data
            include_vin: Include Vin in synthetic data
        """
        self.mode = mode
        self.include_temp = include_temp
        self.include_vin = include_vin
        self._buffer: List[Reading] = []
        self._next_value = 100.0
        self._generating = False
        self._gen_thread = None

    def read_buffer_snapshot(self) -> List[Reading]:
        """Return copy of current buffer (thread-safe mock)."""
        return list(self._buffer)

    def start_generating(self, rate_hz: float = 15.0) -> None:
        """Start background thread that generates readings at specified rate.

        Simulates freerun mode continuous streaming.

        Args:
            rate_hz: Generation rate in Hz (default 15 Hz, typical for 125/125 config)
        """
        if self._generating:
            return

        self._generating = True
        self._gen_thread = Thread(target=self._generation_loop, args=(rate_hz,), daemon=True)
        self._gen_thread.start()

    def stop_generating(self) -> None:
        """Stop background generation thread."""
        self._generating = False
        if self._gen_thread:
            self._gen_thread.join(timeout=2.0)
            self._gen_thread = None

    def add_reading(self, ts: datetime, value: float) -> None:
        """Manually add a reading to buffer (for polled mode simulation)."""
        data = {"value": value}
        if self.include_temp:
            data["TempC"] = 21.5 + (value % 5)  # Synthetic temperature
        if self.include_vin:
            data["Vin"] = 12.3 + (value % 0.5)  # Synthetic voltage

        reading = Reading(
            ts=ts,
            sensor_id="TEST_SENSOR",
            mode=self.mode,
            data=data,
        )
        self._buffer.append(reading)

    def clear_buffer(self) -> None:
        """Clear buffer (simulates controller.clear_buffer())."""
        self._buffer.clear()

    def _generation_loop(self, rate_hz: float) -> None:
        """Background thread that generates readings at specified rate."""
        period = 1.0 / rate_hz
        while self._generating:
            ts = datetime.now(timezone.utc)
            self.add_reading(ts, self._next_value)
            self._next_value += 0.1
            time.sleep(period)


# =============================================================================
# Schema and Conversion Tests
# =============================================================================


def test_schema_structure():
    """Verify SCHEMA dict has expected keys and types."""
    assert "timestamp" in SCHEMA
    assert "sensor_id" in SCHEMA
    assert "mode" in SCHEMA
    assert "value" in SCHEMA
    assert "TempC" in SCHEMA
    assert "Vin" in SCHEMA

    assert SCHEMA["timestamp"] == str
    assert SCHEMA["value"] == float
    assert SCHEMA["TempC"] == float
    assert SCHEMA["Vin"] == float


def test_reading_to_row_complete():
    """Test reading_to_row with all optional fields present."""
    reading = Reading(
        ts=datetime(2025, 1, 15, 12, 30, 45, tzinfo=timezone.utc),
        sensor_id="Q12345",
        mode="freerun",
        data={"value": 123.456, "TempC": 21.5, "Vin": 12.3},
    )

    row = reading_to_row(reading)

    assert row["timestamp"] == "2025-01-15T12:30:45+00:00"
    assert row["sensor_id"] == "Q12345"
    assert row["mode"] == "freerun"
    assert row["value"] == 123.456
    assert row["TempC"] == 21.5
    assert row["Vin"] == 12.3


def test_reading_to_row_missing_optional_fields():
    """Test reading_to_row with missing TempC and Vin (should become None)."""
    reading = Reading(
        ts=datetime(2025, 1, 15, 12, 30, 45, tzinfo=timezone.utc),
        sensor_id="Q12345",
        mode="polled",
        data={"value": 123.456},
    )

    row = reading_to_row(reading)

    assert row["value"] == 123.456
    assert row["TempC"] is None
    assert row["Vin"] is None


def test_reading_to_row_naive_timestamp():
    """Test that naive timestamps are assumed to be UTC."""
    naive_ts = datetime(2025, 1, 15, 12, 30, 45)  # No timezone
    reading = Reading(
        ts=naive_ts,
        sensor_id="Q12345",
        mode="freerun",
        data={"value": 100.0},
    )

    row = reading_to_row(reading)

    # Should be converted to UTC
    assert row["timestamp"] == "2025-01-15T12:30:45+00:00"


def test_reading_to_row_missing_value_raises():
    """Test that Reading construction without 'value' key raises ValueError."""
    # Reading model validates in __post_init__, so this should fail at construction
    with pytest.raises(ValueError, match="Reading data must contain 'value' key"):
        Reading(
            ts=datetime.now(timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"TempC": 21.5},  # Missing 'value'
        )


# =============================================================================
# DataStore Tests
# =============================================================================


def test_datastore_init_empty():
    """Test DataStore initializes with empty DataFrame."""
    store = DataStore()
    df = store.get_dataframe()

    assert df.empty
    assert list(df.columns) == list(SCHEMA.keys())


def test_datastore_append_single_batch():
    """Test appending a batch of readings."""
    store = DataStore()

    readings = [
        Reading(
            ts=datetime(2025, 1, 15, 12, 0, i, tzinfo=timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 100.0 + i},
        )
        for i in range(5)
    ]

    store.append_readings(readings)
    df = store.get_dataframe()

    assert len(df) == 5
    assert df["value"].tolist() == [100.0, 101.0, 102.0, 103.0, 104.0]
    assert df["sensor_id"].iloc[0] == "Q12345"


def test_datastore_append_handles_nan():
    """Test that missing optional fields become NaN in DataFrame."""
    store = DataStore()

    readings = [
        Reading(
            ts=datetime.now(timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 100.0},  # No TempC or Vin
        )
    ]

    store.append_readings(readings)
    df = store.get_dataframe()

    assert pd.isna(df["TempC"].iloc[0])
    assert pd.isna(df["Vin"].iloc[0])


def test_datastore_max_rows_trimming():
    """Test that DataStore trims to max_rows, keeping most recent."""
    store = DataStore(max_rows=10)

    # Add 15 readings
    readings = [
        Reading(
            ts=datetime(2025, 1, 15, 12, 0, i, tzinfo=timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": float(i)},
        )
        for i in range(15)
    ]

    store.append_readings(readings)
    df = store.get_dataframe()

    # Should keep only last 10
    assert len(df) == 10
    assert df["value"].iloc[0] == 5.0  # Oldest kept
    assert df["value"].iloc[-1] == 14.0  # Newest


def test_datastore_get_recent():
    """Test get_recent() filters by time window."""
    store = DataStore()

    now = datetime.now(timezone.utc)

    readings = [
        Reading(
            ts=now - timedelta(seconds=120),  # 2 minutes ago
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 1.0},
        ),
        Reading(
            ts=now - timedelta(seconds=30),  # 30 seconds ago
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 2.0},
        ),
        Reading(
            ts=now,  # Now
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 3.0},
        ),
    ]

    store.append_readings(readings)

    # Get last 60 seconds
    recent = store.get_recent(seconds=60)

    assert len(recent) == 2  # Only last two readings
    assert recent["value"].tolist() == [2.0, 3.0]


def test_datastore_get_latest():
    """Test get_latest() returns most recent reading."""
    store = DataStore()

    readings = [
        Reading(
            ts=datetime(2025, 1, 15, 12, 0, i, tzinfo=timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": float(i)},
        )
        for i in range(3)
    ]

    store.append_readings(readings)

    latest = store.get_latest()
    assert latest is not None
    assert latest["value"] == 2.0
    assert latest["sensor_id"] == "Q12345"


def test_datastore_get_latest_empty():
    """Test get_latest() returns None when empty."""
    store = DataStore()
    assert store.get_latest() is None


def test_datastore_get_stats():
    """Test get_stats() calculates correct statistics."""
    store = DataStore()

    # Add 10 readings over 1 second (10 Hz)
    base_ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    readings = [
        Reading(
            ts=base_ts + timedelta(seconds=i * 0.1),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": float(i)},
        )
        for i in range(10)
    ]

    store.append_readings(readings)
    stats = store.get_stats()

    assert stats["row_count"] == 10
    assert stats["start_time"] == "2025-01-15T12:00:00+00:00"
    assert stats["end_time"] == "2025-01-15T12:00:00.900000+00:00"
    assert stats["duration_s"] == pytest.approx(0.9, abs=0.01)
    assert stats["est_sample_rate_hz"] == pytest.approx(10.0, abs=0.1)


def test_datastore_export_csv(tmp_path):
    """Test CSV export functionality."""
    store = DataStore()

    readings = [
        Reading(
            ts=datetime(2025, 1, 15, 12, 0, i, tzinfo=timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": float(i)},
        )
        for i in range(5)
    ]

    store.append_readings(readings)

    # Export to temp directory
    csv_path = tmp_path / "test_export.csv"
    exported_path = store.export_csv(str(csv_path))

    # Verify file exists and contains data
    assert Path(exported_path).exists()
    df_loaded = pd.read_csv(exported_path)
    assert len(df_loaded) == 5
    assert "value" in df_loaded.columns


def test_datastore_clear():
    """Test clear() resets DataFrame to empty."""
    store = DataStore()

    readings = [
        Reading(
            ts=datetime.now(timezone.utc),
            sensor_id="Q12345",
            mode="freerun",
            data={"value": 100.0},
        )
    ]

    store.append_readings(readings)
    assert len(store.get_dataframe()) == 1

    store.clear()
    assert store.get_dataframe().empty


# =============================================================================
# DataRecorder Tests
# =============================================================================


def test_recorder_basic_freerun_simulation():
    """Test recorder with fake controller simulating freerun mode at ~15 Hz."""
    controller = FakeController(mode="freerun")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.2)

    # Start generating readings at 15 Hz
    controller.start_generating(rate_hz=15.0)

    # Start recorder
    recorder.start()
    assert recorder.is_running()

    # Run for ~1 second
    time.sleep(1.0)

    # Stop recorder and generation
    recorder.stop()
    controller.stop_generating()

    # Verify we recorded approximately 15 readings (±3 for timing variance)
    df = store.get_dataframe()
    assert 12 <= len(df) <= 18, f"Expected ~15 rows, got {len(df)}"

    # Verify all readings have correct mode
    assert (df["mode"] == "freerun").all()


def test_recorder_polled_mode_simulation():
    """Test recorder with fake controller simulating polled mode."""
    controller = FakeController(mode="polled")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    # Start recorder
    recorder.start()

    # Simulate polled readings arriving at 1 Hz (slower than recorder poll rate)
    base_ts = datetime.now(timezone.utc)
    for i in range(3):
        controller.add_reading(base_ts + timedelta(seconds=i), value=100.0 + i)
        time.sleep(0.3)  # Simulate 3 readings over ~1 second

    # Stop recorder
    recorder.stop()

    # Verify we recorded all 3 readings
    df = store.get_dataframe()
    assert len(df) == 3
    assert (df["mode"] == "polled").all()
    assert df["value"].tolist() == [100.0, 101.0, 102.0]


def test_recorder_filters_duplicate_timestamps():
    """Test that recorder filters already-seen readings by timestamp."""
    controller = FakeController(mode="freerun")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    # Add initial reading
    ts1 = datetime.now(timezone.utc)
    controller.add_reading(ts1, value=100.0)

    # Start recorder
    recorder.start()
    time.sleep(0.15)  # Let it poll once

    # Add new reading with later timestamp
    ts2 = ts1 + timedelta(seconds=1)
    controller.add_reading(ts2, value=200.0)

    time.sleep(0.15)  # Let it poll again

    recorder.stop()

    # Should have both readings (no duplicates)
    df = store.get_dataframe()
    assert len(df) == 2
    assert df["value"].tolist() == [100.0, 200.0]


def test_recorder_handles_empty_buffer():
    """Test that recorder doesn't crash when controller buffer is empty."""
    controller = FakeController(mode="freerun")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    # Start recorder with empty controller buffer
    recorder.start()
    time.sleep(0.3)
    recorder.stop()

    # Should have no errors, store remains empty
    assert store.get_dataframe().empty


def test_recorder_stop_with_flush_csv(tmp_path):
    """Test that stop(flush_format='csv') exports data."""
    controller = FakeController(mode="freerun")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    # Add some readings
    controller.add_reading(datetime.now(timezone.utc), value=100.0)
    controller.add_reading(datetime.now(timezone.utc), value=200.0)

    recorder.start()
    time.sleep(0.2)

    # Stop with flush
    # Change to temp directory for flush
    import os
    old_cwd = os.getcwd()
    os.chdir(tmp_path)

    try:
        flush_path = recorder.stop(flush_format="csv")
        assert flush_path is not None
        assert Path(flush_path).exists()

        # Verify exported data
        df_loaded = pd.read_csv(flush_path)
        assert len(df_loaded) >= 2
    finally:
        os.chdir(old_cwd)


def test_recorder_with_optional_fields():
    """Test recorder handles readings with optional TempC and Vin fields."""
    controller = FakeController(mode="freerun", include_temp=True, include_vin=True)
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    # Add readings with optional fields
    controller.add_reading(datetime.now(timezone.utc), value=100.0)

    recorder.start()
    time.sleep(0.2)
    recorder.stop()

    df = store.get_dataframe()
    assert len(df) == 1
    assert not pd.isna(df["TempC"].iloc[0])
    assert not pd.isna(df["Vin"].iloc[0])


def test_recorder_stop_order_warning():
    """Test that recorder can be stopped even if controller is stopped first.

    This documents the CRITICAL requirement that recorder should stop BEFORE
    controller in production, but gracefully handles reverse order.
    """
    controller = FakeController(mode="freerun")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    controller.add_reading(datetime.now(timezone.utc), value=100.0)

    recorder.start()
    time.sleep(0.2)

    # Simulate controller stopping first (buffer cleared)
    controller.clear_buffer()

    # Recorder should still stop cleanly
    recorder.stop()
    assert not recorder.is_running()


# =============================================================================
# Integration: End-to-End Workflow Tests
# =============================================================================


def test_full_workflow_freerun():
    """End-to-end test: freerun acquisition, recording, stats, export."""
    controller = FakeController(mode="freerun", include_temp=True)
    store = DataStore(max_rows=1000)
    recorder = DataRecorder(controller, store, poll_interval_s=0.15)

    # Simulate freerun acquisition at 15 Hz
    controller.start_generating(rate_hz=15.0)
    recorder.start()

    # Run for 2 seconds
    time.sleep(2.0)

    # Stop in correct order: recorder first, then controller
    recorder.stop()
    controller.stop_generating()

    # Verify data captured
    df = store.get_dataframe()
    assert len(df) >= 25  # At least ~30 readings expected (15 Hz * 2s)

    # Check stats
    stats = store.get_stats()
    assert stats["row_count"] >= 25
    assert stats["est_sample_rate_hz"] > 10.0  # Should be close to 15 Hz

    # Verify all readings have temperature
    assert not df["TempC"].isna().any()


def test_full_workflow_polled():
    """End-to-end test: polled mode, manual queries, recording."""
    controller = FakeController(mode="polled")
    store = DataStore()
    recorder = DataRecorder(controller, store, poll_interval_s=0.1)

    recorder.start()

    # Simulate polled queries at ~2 Hz
    base_ts = datetime.now(timezone.utc)
    for i in range(10):
        controller.add_reading(base_ts + timedelta(seconds=i * 0.5), value=100.0 + i)
        time.sleep(0.5)

    recorder.stop(flush_format="csv")

    # Verify all 10 readings captured
    df = store.get_dataframe()
    assert len(df) == 10
    assert (df["mode"] == "polled").all()

    # Check stats
    stats = store.get_stats()
    assert stats["row_count"] == 10
    assert 1.8 <= stats["est_sample_rate_hz"] <= 2.2  # ~2 Hz
