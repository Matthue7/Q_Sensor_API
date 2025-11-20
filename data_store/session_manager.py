"""
SessionManager for managing Q-Sensor recording sessions.

This module provides:
- SessionManager class for creating, tracking, and stopping recording sessions
- Support for single active session (with foundation for multi-session future)
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Dict, Optional

from data_store.session import RecordingSession, SessionState, SessionStats
from q_sensor_lib.controller import SensorController

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Summary information about a recording session."""

    session_id: str
    mission: str
    rate_hz: int
    schema_version: int
    state: SessionState
    started_at: str  # ISO 8601
    stopped_at: Optional[str]  # ISO 8601 if stopped


class SessionManager:
    """
    Manages recording sessions for Q-Sensor.

    Currently enforces single active session limit for backward compatibility.
    Provides foundation for multi-session support in the future.

    Thread-safe: All operations use internal lock.
    """

    def __init__(self, controller: SensorController, base_path: Path):
        """
        Initialize SessionManager.

        Args:
            controller: SensorController instance (shared across sessions)
            base_path: Base directory for recordings (e.g., /data/qsensor_recordings)
        """
        self._lock = RLock()
        self._controller = controller
        self._base_path = Path(base_path)

        # Session registry: session_id -> RecordingSession
        self._sessions: Dict[str, RecordingSession] = {}

        # Track active session for single-session enforcement
        self._active_session_id: Optional[str] = None

        logger.info(
            f"[SessionManager] Initialized with base_path={base_path}"
        )

    def create_session(
        self,
        mission: str,
        rate_hz: int,
        schema_version: int,
        roll_interval_s: float,
        target_chunk_mb: float = 2.0,
    ) -> RecordingSession:
        """
        Create and start a new recording session.

        Args:
            mission: Mission name for metadata
            rate_hz: Target recording rate in Hz
            schema_version: Data schema version
            roll_interval_s: Chunk roll interval in seconds (15-300)
            target_chunk_mb: Target chunk size in MB (default: 2.0)

        Returns:
            Started RecordingSession instance

        Raises:
            RuntimeError: If another session is already active
            Exception: If session creation/start fails
        """
        with self._lock:
            # Enforce single active session limit
            if self._active_session_id is not None:
                active_session = self._sessions.get(self._active_session_id)
                if active_session and active_session.is_recording:
                    raise RuntimeError(
                        f"Recording already active: session_id={self._active_session_id}"
                    )

            # Generate unique session ID
            session_id = str(uuid.uuid4())

            logger.info(
                f"[SessionManager] Creating session {session_id[:8]}: "
                f"mission={mission}, rate_hz={rate_hz}, roll_interval_s={roll_interval_s}"
            )

            # Create session
            session = RecordingSession(
                session_id=session_id,
                controller=self._controller,
                base_path=self._base_path,
                mission=mission,
                rate_hz=rate_hz,
                schema_version=schema_version,
                roll_interval_s=roll_interval_s,
                target_chunk_mb=target_chunk_mb,
            )

            # Start recording
            try:
                session.start()
            except Exception as e:
                logger.error(
                    f"[SessionManager] Failed to start session {session_id[:8]}: {e}"
                )
                # Clean up failed session
                session.cleanup()
                raise

            # Register session
            self._sessions[session_id] = session
            self._active_session_id = session_id

            logger.info(
                f"[SessionManager] Session {session_id[:8]} started successfully"
            )

            return session

    def get_session(self, session_id: str) -> Optional[RecordingSession]:
        """
        Get session by ID.

        Args:
            session_id: Session identifier

        Returns:
            RecordingSession if found, None otherwise
        """
        with self._lock:
            return self._sessions.get(session_id)

    def stop_session(self, session_id: str) -> SessionStats:
        """
        Stop a recording session and finalize all chunks.

        Args:
            session_id: Session identifier

        Returns:
            SessionStats with final counts and metadata

        Raises:
            KeyError: If session not found
            RuntimeError: If session is not in RECORDING state
            Exception: If stop/flush operations fail
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise KeyError(f"Session not found: {session_id}")

            logger.info(f"[SessionManager] Stopping session {session_id[:8]}...")

            try:
                stats = session.stop()

                # Clear active session if this was it
                if self._active_session_id == session_id:
                    self._active_session_id = None

                logger.info(
                    f"[SessionManager] Session {session_id[:8]} stopped: "
                    f"chunks={stats.chunk_count}, rows={stats.total_rows}"
                )

                return stats

            except Exception as e:
                logger.error(
                    f"[SessionManager] Failed to stop session {session_id[:8]}: {e}",
                    exc_info=True,
                )
                raise

    def list_sessions(self) -> list[SessionInfo]:
        """
        List all sessions (active and stopped).

        Returns:
            List of SessionInfo objects
        """
        with self._lock:
            sessions_info = []

            for session in self._sessions.values():
                info = SessionInfo(
                    session_id=session.session_id,
                    mission=session.mission,
                    rate_hz=session.rate_hz,
                    schema_version=session.schema_version,
                    state=session.state,
                    started_at=session.started_at.isoformat(),
                    stopped_at=(
                        session.stopped_at.isoformat()
                        if session.stopped_at
                        else None
                    ),
                )
                sessions_info.append(info)

            return sessions_info

    def get_active_session(self) -> Optional[RecordingSession]:
        """
        Get the currently active recording session.

        Returns:
            RecordingSession if one is active, None otherwise
        """
        with self._lock:
            if self._active_session_id is None:
                return None

            session = self._sessions.get(self._active_session_id)

            # Verify session is still recording
            if session and session.is_recording:
                return session
            else:
                # Session stopped, clear active flag
                self._active_session_id = None
                return None

    def cleanup_stopped_sessions(self, keep_recent: int = 10) -> None:
        """
        Remove stopped sessions from registry to free memory.

        Args:
            keep_recent: Number of most recent stopped sessions to keep (default: 10)
        """
        with self._lock:
            stopped_sessions = [
                (session_id, session)
                for session_id, session in self._sessions.items()
                if session.state == SessionState.STOPPED
            ]

            # Sort by stopped_at timestamp (newest first)
            stopped_sessions.sort(
                key=lambda x: x[1].stopped_at or x[1].started_at,
                reverse=True,
            )

            # Remove oldest stopped sessions
            if len(stopped_sessions) > keep_recent:
                for session_id, _ in stopped_sessions[keep_recent:]:
                    logger.info(
                        f"[SessionManager] Removing stopped session {session_id[:8]} from registry"
                    )
                    del self._sessions[session_id]

    def shutdown_all(self) -> None:
        """
        Stop all active sessions and clean up resources.

        Called during application shutdown to ensure clean termination.
        """
        with self._lock:
            logger.info("[SessionManager] Shutting down all sessions...")

            active_sessions = [
                (session_id, session)
                for session_id, session in self._sessions.items()
                if session.is_recording
            ]

            for session_id, session in active_sessions:
                logger.info(
                    f"[SessionManager] Stopping session {session_id[:8]} for shutdown..."
                )
                try:
                    session.stop()
                except Exception as e:
                    logger.error(
                        f"[SessionManager] Error stopping session {session_id[:8]}: {e}",
                        exc_info=True,
                    )
                    # Try cleanup as fallback
                    session.cleanup()

            self._active_session_id = None
            logger.info("[SessionManager] All sessions shut down")

    @property
    def session_count(self) -> int:
        """Get total number of sessions (active and stopped)."""
        with self._lock:
            return len(self._sessions)

    @property
    def active_session_count(self) -> int:
        """Get number of active recording sessions."""
        with self._lock:
            return sum(
                1 for session in self._sessions.values() if session.is_recording
            )
