"""Thread-safe ring buffer for storing sensor readings."""

import logging
import threading
from collections import deque
from typing import List

from q_sensor_lib.models import Reading

logger = logging.getLogger(__name__)


class RingBuffer:
    """Thread-safe fixed-size FIFO buffer for sensor readings.

    Once the buffer reaches maxlen, oldest readings are automatically
    discarded when new readings are appended.
    """

    def __init__(self, maxlen: int = 1000) -> None:
        """Initialize ring buffer.

        Args:
            maxlen: Maximum number of readings to store. Defaults to 1000.
        """
        if maxlen <= 0:
            raise ValueError(f"maxlen must be positive, got {maxlen}")

        self._buffer: deque[Reading] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._maxlen = maxlen

    def append(self, reading: Reading) -> None:
        """Append a reading to the buffer (thread-safe).

        If buffer is full, oldest reading is automatically discarded.

        Args:
            reading: Reading instance to append
        """
        with self._lock:
            self._buffer.append(reading)
            logger.debug(
                f"Appended reading at {reading.ts.isoformat()}, "
                f"buffer size: {len(self._buffer)}/{self._maxlen}"
            )

    def snapshot(self) -> List[Reading]:
        """Get a copy of all current readings (thread-safe).

        Returns:
            List of Reading instances, ordered oldest to newest
        """
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        """Remove all readings from buffer (thread-safe)."""
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            logger.debug(f"Cleared {count} readings from buffer")

    def __len__(self) -> int:
        """Get current number of readings in buffer (thread-safe).

        Returns:
            Number of readings currently stored
        """
        with self._lock:
            return len(self._buffer)

    @property
    def maxlen(self) -> int:
        """Maximum capacity of buffer."""
        return self._maxlen
