"""Thread-safe DataFrame store and background recorder for Q-Sensor data.

This module provides:
- DataStore: Thread-safe in-memory DataFrame with export capabilities
- DataRecorder: Background thread that polls SensorController buffer and records to DataStore

Design notes:
- Recorder operates at 200ms poll interval regardless of sensor mode (freerun/polled)
- In freerun: readings appear continuously in controller buffer (~15 Hz typical)
- In polled: readings appear only when controller polls (user-controlled rate)
- Recorder must stop and flush BEFORE controller stops to avoid losing buffered data
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Iterable, Optional

import pandas as pd

from data_store.schemas import SCHEMA, reading_to_row
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import Reading

logger = logging.getLogger(__name__)


class DataStore:
    """Thread-safe in-memory DataFrame store for sensor readings.

    Maintains a pandas DataFrame with normalized schema (timestamp, sensor_id, mode, value, TempC, Vin).
    Supports concurrent appends, queries, statistics, and export to CSV/Parquet.

    Optional auto-flush: If auto_flush_interval_s is set, a background thread periodically
    flushes accumulated data to disk.
    """

    def __init__(
        self,
        max_rows: int = 100000,
        auto_flush_interval_s: Optional[float] = None,
        auto_flush_format: str = "csv",
        auto_flush_path: Optional[str] = None,
    ) -> None:
        """Initialize empty DataFrame store.

        Args:
            max_rows: Maximum rows to keep in memory. Older rows are trimmed after appends.
            auto_flush_interval_s: If set, enable background auto-flush every N seconds.
            auto_flush_format: Format for auto-flush ("csv" or "parquet").
            auto_flush_path: Path for auto-flush output. If None, auto-generates timestamped name.
        """
        self._lock = RLock()
        self._df = pd.DataFrame(columns=list(SCHEMA.keys()))
        self._max_rows = max_rows

        # Auto-flush configuration
        self._auto_flush_interval = auto_flush_interval_s
        self._auto_flush_format = auto_flush_format
        self._auto_flush_path = auto_flush_path
        self._flush_thread: Optional[Thread] = None
        self._flush_stop_event = Event()

        # Start auto-flush thread if configured
        if auto_flush_interval_s is not None:
            self._start_auto_flush()

    def append_readings(self, readings: Iterable[Reading]) -> None:
        """Append multiple readings to DataFrame.

        Thread-safe. Converts readings to rows, appends to DataFrame, and trims to max_rows.

        Args:
            readings: Iterable of Reading instances (e.g., from controller.read_buffer_snapshot())
        """
        if not readings:
            return

        rows = [reading_to_row(r) for r in readings]

        with self._lock:
            # Append new rows
            new_df = pd.DataFrame(rows, columns=list(SCHEMA.keys()))
            self._df = pd.concat([self._df, new_df], ignore_index=True)

            # Trim to max_rows (keep most recent)
            if len(self._df) > self._max_rows:
                excess = len(self._df) - self._max_rows
                self._df = self._df.iloc[excess:].reset_index(drop=True)
                logger.debug(f"Trimmed {excess} oldest rows, now {len(self._df)} rows")

    def get_dataframe(self) -> pd.DataFrame:
        """Get copy of entire DataFrame.

        Thread-safe. Returns a defensive copy to prevent external modification.

        Returns:
            Copy of internal DataFrame
        """
        with self._lock:
            return self._df.copy()

    def get_recent(self, seconds: int = 60) -> pd.DataFrame:
        """Get readings from the last N seconds.

        Thread-safe. Filters by timestamp column (ISO 8601 strings converted to datetime).

        Args:
            seconds: Number of seconds of recent history to retrieve

        Returns:
            DataFrame containing only readings within the time window
        """
        with self._lock:
            if self._df.empty:
                return pd.DataFrame(columns=list(SCHEMA.keys()))

            # Convert timestamp strings to datetime for filtering
            df = self._df.copy()
            df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)

            # Filter to recent window
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
            recent = df[df["timestamp"] >= cutoff].copy()  # Explicit copy to avoid SettingWithCopyWarning

            # Convert timestamps back to ISO strings to match schema
            recent["timestamp"] = recent["timestamp"].apply(lambda x: x.isoformat())

            return recent.reset_index(drop=True)

    def get_latest(self) -> Optional[dict]:
        """Get the most recent reading as a dictionary.

        Thread-safe.

        Returns:
            Dictionary of latest row, or None if DataFrame is empty
        """
        with self._lock:
            if self._df.empty:
                return None
            return self._df.iloc[-1].to_dict()

    def get_stats(self) -> dict:
        """Get summary statistics about stored data.

        Thread-safe.

        Returns:
            Dictionary with keys:
                - row_count: Total number of readings
                - start_time: ISO timestamp of first reading (or None)
                - end_time: ISO timestamp of last reading (or None)
                - duration_s: Time span of data in seconds (or 0)
                - est_sample_rate_hz: Estimated sample rate (or 0)
        """
        with self._lock:
            if self._df.empty:
                return {
                    "row_count": 0,
                    "start_time": None,
                    "end_time": None,
                    "duration_s": 0.0,
                    "est_sample_rate_hz": 0.0,
                }

            # Parse timestamps
            timestamps = pd.to_datetime(self._df["timestamp"], format="ISO8601", utc=True)
            start = timestamps.iloc[0]
            end = timestamps.iloc[-1]
            duration_s = (end - start).total_seconds()

            # Estimate sample rate
            rate_hz = 0.0
            if duration_s > 0 and len(self._df) > 1:
                rate_hz = (len(self._df) - 1) / duration_s

            return {
                "row_count": len(self._df),
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "duration_s": duration_s,
                "est_sample_rate_hz": rate_hz,
            }

    def export_csv(self, path: Optional[str] = None) -> str:
        """Export DataFrame to CSV file.

        Thread-safe. Auto-generates filename if path not provided.

        Args:
            path: Output file path. If None, generates timestamped filename.

        Returns:
            Absolute path to exported file
        """
        with self._lock:
            if path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = f"qsensor_data_{timestamp}.csv"

            self._df.to_csv(path, index=False)
            abs_path = str(Path(path).resolve())
            logger.info(f"Exported {len(self._df)} rows to CSV: {abs_path}")
            return abs_path

    def export_parquet(self, path: Optional[str] = None) -> str:
        """Export DataFrame to Parquet file.

        Thread-safe. Auto-generates filename if path not provided.
        Requires pyarrow or fastparquet to be installed.

        Args:
            path: Output file path. If None, generates timestamped filename.

        Returns:
            Absolute path to exported file
        """
        with self._lock:
            if path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = f"qsensor_data_{timestamp}.parquet"

            self._df.to_parquet(path, index=False)
            abs_path = str(Path(path).resolve())
            logger.info(f"Exported {len(self._df)} rows to Parquet: {abs_path}")
            return abs_path

    def flush_to_disk(self, format: str = "csv", path: Optional[str] = None) -> str:
        """Flush current DataFrame to disk.

        Thread-safe. Convenience method that calls export_csv or export_parquet.

        Args:
            format: "csv" or "parquet"
            path: Output file path (optional, auto-generated if None)

        Returns:
            Absolute path to exported file

        Raises:
            ValueError: If format is not "csv" or "parquet"
        """
        if format == "csv":
            return self.export_csv(path)
        elif format == "parquet":
            return self.export_parquet(path)
        else:
            raise ValueError(f"Unknown format '{format}', expected 'csv' or 'parquet'")

    def clear(self) -> None:
        """Clear all stored data.

        Thread-safe. Resets DataFrame to empty with schema intact.
        """
        with self._lock:
            self._df = pd.DataFrame(columns=list(SCHEMA.keys()))
            logger.debug("DataStore cleared")

    def _start_auto_flush(self) -> None:
        """Start background auto-flush thread."""
        if self._auto_flush_interval is None:
            return

        self._flush_stop_event.clear()
        self._flush_thread = Thread(
            target=self._auto_flush_loop,
            name="DataStoreAutoFlush",
            daemon=True,
        )
        self._flush_thread.start()
        logger.info(
            f"Auto-flush started: every {self._auto_flush_interval}s, format={self._auto_flush_format}"
        )

    def _auto_flush_loop(self) -> None:
        """Background thread that periodically flushes to disk."""
        assert self._auto_flush_interval is not None

        while not self._flush_stop_event.wait(timeout=self._auto_flush_interval):
            try:
                with self._lock:
                    if not self._df.empty:
                        self.flush_to_disk(
                            format=self._auto_flush_format,
                            path=self._auto_flush_path,
                        )
            except Exception as e:
                logger.error(f"Auto-flush failed: {e}", exc_info=True)

        logger.info("Auto-flush loop stopped")

    def stop_auto_flush(self) -> None:
        """Stop auto-flush thread if running."""
        if self._flush_thread and self._flush_thread.is_alive():
            logger.debug("Stopping auto-flush thread...")
            self._flush_stop_event.set()
            self._flush_thread.join(timeout=5.0)
            self._flush_thread = None


class DataRecorder:
    """Background recorder that polls SensorController buffer and writes to DataStore.

    Runs a background thread that:
    1. Every 200ms (configurable), calls controller.read_buffer_snapshot()
    2. Filters to new readings (timestamp > last_seen_ts)
    3. Appends new readings to DataStore
    4. Updates last_seen_ts

    This works identically for both freerun and polled modes:
    - Freerun: Controller buffer continuously fills with streamed data (~15 Hz typical)
    - Polled: Controller buffer fills only when controller polls (user-controlled rate)

    CRITICAL SHUTDOWN ORDER: Recorder must stop and flush BEFORE controller stops,
    otherwise buffered readings are lost when firmware clears buffer on stop.
    """

    def __init__(
        self,
        controller: SensorController,
        store: DataStore,
        poll_interval_s: float = 0.2,
    ) -> None:
        """Initialize recorder (does not start automatically).

        Args:
            controller: SensorController instance (must be in acquisition mode before start())
            store: DataStore instance to write readings to
            poll_interval_s: Polling interval in seconds (default 200ms)
        """
        self._controller = controller
        self._store = store
        self._poll_interval = poll_interval_s

        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._last_seen_ts: Optional[datetime] = None

    def start(self) -> None:
        """Start background recording thread.

        Raises:
            RuntimeError: If recorder is already running
        """
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Recorder already running")

        logger.info(f"Starting DataRecorder (poll interval: {self._poll_interval}s)...")
        self._stop_event.clear()
        self._last_seen_ts = None  # Reset timestamp tracking

        self._thread = Thread(
            target=self._recorder_loop,
            name="DataRecorder",
            daemon=True,
        )
        self._thread.start()
        logger.info("DataRecorder started")

    def stop(self, flush_format: Optional[str] = None) -> Optional[str]:
        """Stop recording thread and optionally flush data to disk.

        CRITICAL: Call this BEFORE stopping the SensorController to ensure all buffered
        readings are captured.

        Args:
            flush_format: If "csv" or "parquet", flush DataStore to disk before stopping.
                         If None, no flush is performed.

        Returns:
            Path to flushed file if flush_format was specified, None otherwise
        """
        if not self._thread or not self._thread.is_alive():
            logger.warning("Recorder not running, nothing to stop")
            return None

        logger.info("Stopping DataRecorder...")
        self._stop_event.set()
        self._thread.join(timeout=5.0)

        if self._thread.is_alive():
            logger.warning("DataRecorder thread did not stop cleanly")

        self._thread = None

        # Flush if requested
        flush_path = None
        if flush_format:
            logger.info(f"Flushing data to {flush_format}...")
            flush_path = self._store.flush_to_disk(format=flush_format)

        logger.info("DataRecorder stopped")
        return flush_path

    def is_running(self) -> bool:
        """Check if recorder thread is active.

        Returns:
            True if recording, False otherwise
        """
        return self._thread is not None and self._thread.is_alive()

    def _recorder_loop(self) -> None:
        """Background thread loop that polls controller buffer and appends to store."""
        logger.info(f"Recorder loop started (thread {threading.get_ident()})")

        while not self._stop_event.wait(timeout=self._poll_interval):
            try:
                # Get snapshot of controller buffer
                snapshot = self._controller.read_buffer_snapshot()

                if not snapshot:
                    continue  # No new data

                # Filter to new readings (ts > last_seen_ts)
                if self._last_seen_ts is None:
                    # First batch: take all
                    new_readings = snapshot
                else:
                    # Filter to readings newer than last_seen_ts
                    new_readings = [r for r in snapshot if r.ts > self._last_seen_ts]

                if new_readings:
                    # Append to store
                    self._store.append_readings(new_readings)

                    # Update last_seen_ts to newest reading
                    self._last_seen_ts = max(r.ts for r in new_readings)

                    logger.debug(
                        f"Recorded {len(new_readings)} new readings "
                        f"(latest: {self._last_seen_ts.isoformat()})"
                    )

            except Exception as e:
                logger.error(f"Error in recorder loop: {e}", exc_info=True)
                # Don't crash thread on transient errors
                time.sleep(0.5)

        logger.info("Recorder loop stopped")
