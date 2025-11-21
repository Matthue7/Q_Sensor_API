"""
RecordingSession abstraction for managing the lifecycle of a single Q-Sensor recording session.

This module provides:
- SessionState enum for tracking recording lifecycle
- RecordingSession class encapsulating store, recorder, and metadata
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Optional

from data_store.store import ChunkedDataStore, DataRecorder
from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import Reading

logger = logging.getLogger(__name__)


class SessionState(Enum):
    """Recording session lifecycle states."""

    STARTING = "starting"      # Session created, initializing resources
    RECORDING = "recording"    # Actively recording data
    STOPPING = "stopping"      # Stop requested, finalizing chunks
    STOPPED = "stopped"        # Recording complete, resources released
    ERROR = "error"            # Error occurred during recording


@dataclass
class SessionStats:
    """Final statistics from a completed recording session."""

    session_id: str
    started_at: datetime
    stopped_at: datetime
    chunk_count: int
    total_rows: int
    total_bytes: int
    duration_s: float


@dataclass
class SessionStatus:
    """Current status of an active or stopped recording session."""

    session_id: str
    state: SessionState
    rows: int
    bytes: int
    last_chunk_index: int
    backlog: int  # Number of finalized chunks


class RecordingSession:
    """
    Encapsulates the lifecycle of a single Q-Sensor recording session.

    Manages:
    - ChunkedDataStore for atomic chunk writing
    - DataRecorder thread for polling controller buffer
    - Session metadata and state tracking
    - Resource cleanup and error handling

    Thread-safe: All state-changing operations use internal lock.
    """

    def __init__(
        self,
        session_id: str,
        controller: SensorController,
        base_path: Path,
        mission: str,
        rate_hz: int,
        schema_version: int,
        roll_interval_s: float,
        target_chunk_mb: float = 2.0,
    ):
        """
        Initialize a recording session.

        Args:
            session_id: Unique session identifier (UUID)
            controller: SensorController instance (shared, not owned)
            base_path: Base directory for recordings (e.g., /data/qsensor_recordings)
            mission: Mission name for metadata
            rate_hz: Target recording rate in Hz
            schema_version: Data schema version
            roll_interval_s: Chunk roll interval in seconds (15-300)
            target_chunk_mb: Target chunk size in MB (default: 2.0)
        """
        self._lock = RLock()

        # Identifiers and metadata
        self.session_id = session_id
        self.mission = mission
        self.rate_hz = rate_hz
        self.schema_version = schema_version
        self.started_at = datetime.now(timezone.utc)
        self.stopped_at: Optional[datetime] = None

        # Resources (not owned, references only)
        self._controller = controller

        # Resources (owned by this session)
        self._store: Optional[ChunkedDataStore] = None
        self._recorder: Optional[DataRecorder] = None

        # State tracking
        self._state = SessionState.STARTING
        self._error: Optional[str] = None

        # Storage configuration
        self._base_path = base_path
        self._roll_interval_s = roll_interval_s
        self._target_chunk_mb = target_chunk_mb

        logger.info(
            f"[SESSION {session_id[:8]}] Created recording session: "
            f"mission={mission}, rate_hz={rate_hz}, roll_interval_s={roll_interval_s}"
        )

    def start(self) -> None:
        """
        Start the recording session.

        Creates ChunkedDataStore, starts DataRecorder thread.

        Raises:
            RuntimeError: If session is not in STARTING state
            Exception: If store or recorder initialization fails
        """
        with self._lock:
            if self._state != SessionState.STARTING:
                raise RuntimeError(
                    f"Cannot start session in state {self._state.value}"
                )

            try:
                # Initialize ChunkedDataStore
                logger.info(
                    f"[SESSION {self.session_id[:8]}] Initializing ChunkedDataStore: "
                    f"path={self._base_path}/{self.session_id}"
                )
                self._store = ChunkedDataStore(
                    session_id=self.session_id,
                    base_path=self._base_path,
                    roll_interval_s=self._roll_interval_s,
                    target_chunk_mb=self._target_chunk_mb,
                )

                # Start DataRecorder thread
                logger.info(
                    f"[SESSION {self.session_id[:8]}] Starting DataRecorder thread"
                )
                self._recorder = DataRecorder(
                    self._controller,
                    self._store,
                    poll_interval_s=0.2,
                )
                self._recorder.start()

                # Transition to RECORDING state
                self._state = SessionState.RECORDING
                logger.info(
                    f"[SESSION {self.session_id[:8]}] Recording session started successfully"
                )

            except Exception as e:
                self._state = SessionState.ERROR
                self._error = str(e)
                logger.error(
                    f"[SESSION {self.session_id[:8]}] Failed to start session: {e}",
                    exc_info=True,
                )
                raise

    def stop(self) -> SessionStats:
        """
        Stop the recording session and finalize all chunks.

        Stops DataRecorder thread, flushes remaining data, updates stopped_at timestamp.

        Returns:
            SessionStats with final counts and metadata

        Raises:
            RuntimeError: If session is not in RECORDING state
            Exception: If stop/flush operations fail
        """
        with self._lock:
            if self._state != SessionState.RECORDING:
                raise RuntimeError(
                    f"Cannot stop session in state {self._state.value}"
                )

            self._state = SessionState.STOPPING

            try:
                logger.info(
                    f"[SESSION {self.session_id[:8]}] Stopping DataRecorder thread..."
                )

                # Stop recorder thread
                if self._recorder and self._recorder.is_running():
                    self._recorder.stop()
                    logger.info(f"[SESSION {self.session_id[:8]}] DataRecorder stopped")

                # Flush final chunk
                if self._store:
                    logger.info(
                        f"[SESSION {self.session_id[:8]}] Flushing final chunk..."
                    )
                    self._store.flush()

                    # Get final statistics
                    stats_dict = self._store.get_stats()
                    chunks = self._store.snapshot_list()

                    self.stopped_at = datetime.now(timezone.utc)
                    duration_s = (self.stopped_at - self.started_at).total_seconds()

                    stats = SessionStats(
                        session_id=self.session_id,
                        started_at=self.started_at,
                        stopped_at=self.stopped_at,
                        chunk_count=len(chunks),
                        total_rows=stats_dict.get("total_rows", 0),
                        total_bytes=stats_dict.get("total_bytes", 0),
                        duration_s=duration_s,
                    )

                    logger.info(
                        f"[SESSION {self.session_id[:8]}] Recording stopped: "
                        f"chunks={stats.chunk_count}, rows={stats.total_rows}, "
                        f"bytes={stats.total_bytes}, duration={stats.duration_s:.1f}s"
                    )
                else:
                    # Store was never initialized (edge case)
                    self.stopped_at = datetime.now(timezone.utc)
                    stats = SessionStats(
                        session_id=self.session_id,
                        started_at=self.started_at,
                        stopped_at=self.stopped_at,
                        chunk_count=0,
                        total_rows=0,
                        total_bytes=0,
                        duration_s=0.0,
                    )

                # Transition to STOPPED state
                self._state = SessionState.STOPPED
                return stats

            except Exception as e:
                self._state = SessionState.ERROR
                self._error = str(e)
                logger.error(
                    f"[SESSION {self.session_id[:8]}] Failed to stop session: {e}",
                    exc_info=True,
                )
                raise

    def get_status(self) -> SessionStatus:
        """
        Get current session status.

        Returns:
            SessionStatus with current row/byte counts and state
        """
        with self._lock:
            if self._store:
                stats = self._store.get_stats()
                chunks = self._store.snapshot_list()

                return SessionStatus(
                    session_id=self.session_id,
                    state=self._state,
                    rows=stats.get("total_rows", 0),
                    bytes=stats.get("total_bytes", 0),
                    last_chunk_index=len(chunks) - 1 if chunks else -1,
                    backlog=len(chunks),
                )
            else:
                # Store not initialized yet
                return SessionStatus(
                    session_id=self.session_id,
                    state=self._state,
                    rows=0,
                    bytes=0,
                    last_chunk_index=-1,
                    backlog=0,
                )

    def inject_sync_marker(self, reading: Reading) -> str:
        """Inject a sync marker directly into the active recording store.

        Args:
            reading: Reading instance representing the sync marker

        Returns:
            ISO timestamp string corresponding to the appended marker

        Raises:
            RuntimeError: If store is not ready or session not recording
        """
        with self._lock:
            if self._state != SessionState.RECORDING:
                raise RuntimeError("Session is not recording")

            if not self._store:
                raise RuntimeError("Store not initialized")

            return self._store.append_marker(reading)

    def get_snapshots(self) -> list[dict]:
        """
        Get list of finalized chunks with metadata.

        Returns:
            List of chunk metadata dicts (index, name, size_bytes, sha256, rows)

        Raises:
            RuntimeError: If store is not initialized
        """
        with self._lock:
            if not self._store:
                raise RuntimeError("Store not initialized")

            return self._store.snapshot_list()

    def get_chunk_path(self, filename: str) -> Path:
        """
        Get path to a finalized chunk file.

        Args:
            filename: Chunk filename (e.g., "chunk_00000.csv")

        Returns:
            Absolute path to chunk file

        Raises:
            RuntimeError: If store is not initialized
            FileNotFoundError: If chunk file does not exist
        """
        with self._lock:
            if not self._store:
                raise RuntimeError("Store not initialized")

            return self._store.open_chunk(filename)

    def cleanup(self) -> None:
        """
        Release resources without finalizing (for error recovery).

        Stops recorder if running, does NOT flush chunks.
        Use stop() for normal shutdown.
        """
        with self._lock:
            logger.info(f"[SESSION {self.session_id[:8]}] Cleaning up resources...")

            if self._recorder and self._recorder.is_running():
                try:
                    self._recorder.stop()
                except Exception as e:
                    logger.error(
                        f"[SESSION {self.session_id[:8]}] Error stopping recorder: {e}"
                    )

            self._recorder = None
            self._store = None

            if self._state != SessionState.STOPPED:
                self._state = SessionState.ERROR

    @property
    def state(self) -> SessionState:
        """Get current session state (thread-safe)."""
        with self._lock:
            return self._state

    @property
    def is_recording(self) -> bool:
        """Check if session is actively recording."""
        with self._lock:
            return self._state == SessionState.RECORDING

    @property
    def error(self) -> Optional[str]:
        """Get error message if session is in ERROR state."""
        with self._lock:
            return self._error

    @property
    def sensor_id(self) -> str:
        """Get sensor identifier associated with this session."""
        with self._lock:
            return self._controller.sensor_id if self._controller else "unknown"
