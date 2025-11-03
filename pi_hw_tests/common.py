"""Common utilities for hardware validation scripts.

Provides:
- Timestamped filenames
- CSV export wrappers
- Rolling rate estimator
- Structured logging
- Robust teardown helpers
- Result reporting
"""

import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from q_sensor_lib.controller import SensorController
from q_sensor_lib.models import ConnectionState


@dataclass
class TestResult:
    """Uniform test result structure."""
    passed: bool
    message: str
    metrics: dict

    def print_result(self):
        """Print formatted result."""
        status = "✓ PASS" if self.passed else "✗ FAIL"
        print(f"\n{'='*60}")
        print(f"{status}: {self.message}")
        if self.metrics:
            print("\nMetrics:")
            for key, value in self.metrics.items():
                print(f"  {key}: {value}")
        print('='*60)


def setup_logging(script_name: str, logs_dir: Path) -> logging.Logger:
    """Setup structured logging to both console and file.

    Args:
        script_name: Name of the script (for log filename)
        logs_dir: Directory for log files

    Returns:
        Configured logger
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{script_name}_{timestamp}.log"

    logger = logging.getLogger(script_name)
    logger.setLevel(logging.DEBUG)

    # File handler - verbose
    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))

    # Console handler - less verbose
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        '%(levelname)s: %(message)s'
    ))

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info(f"Logging to {log_file}")
    return logger


def timestamped_filename(prefix: str, extension: str = "csv") -> str:
    """Generate timestamped filename.

    Args:
        prefix: Filename prefix
        extension: File extension (default "csv")

    Returns:
        Filename like "prefix_20251101_153454.ext"
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}.{extension}"


def export_dataframe_csv(store, out_dir: Path, prefix: str) -> Path:
    """Export DataFrame to CSV with timestamped filename.

    Args:
        store: DataStore instance
        out_dir: Output directory
        prefix: Filename prefix

    Returns:
        Path to exported CSV file
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = timestamped_filename(prefix, "csv")
    filepath = out_dir / filename

    store.export_csv(str(filepath))
    return filepath


class RollingRateEstimator:
    """Estimate sample rate over a rolling window."""

    def __init__(self, window_seconds: float = 5.0):
        """Initialize estimator.

        Args:
            window_seconds: Window size in seconds
        """
        self.window_s = window_seconds
        self.timestamps: List[float] = []

    def add_sample(self, timestamp: Optional[float] = None):
        """Add a sample timestamp.

        Args:
            timestamp: Unix timestamp (default: current time)
        """
        if timestamp is None:
            timestamp = time.time()
        self.timestamps.append(timestamp)

        # Trim to window
        cutoff = timestamp - self.window_s
        self.timestamps = [t for t in self.timestamps if t >= cutoff]

    def get_rate_hz(self) -> float:
        """Calculate current rate in Hz.

        Returns:
            Samples per second, or 0 if insufficient data
        """
        if len(self.timestamps) < 2:
            return 0.0

        duration = self.timestamps[-1] - self.timestamps[0]
        if duration <= 0:
            return 0.0

        return (len(self.timestamps) - 1) / duration

    def get_count(self) -> int:
        """Get number of samples in current window."""
        return len(self.timestamps)


def robust_teardown(controller: Optional[SensorController],
                    recorder=None,
                    logger: Optional[logging.Logger] = None):
    """Robust teardown ensuring correct stop order.

    CRITICAL: Stops recorder BEFORE controller to prevent buffer clears.

    Args:
        controller: SensorController instance (may be None)
        recorder: DataRecorder instance (may be None)
        logger: Optional logger for messages
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    # Step 1: Stop recorder first (if exists and running)
    if recorder is not None:
        try:
            if hasattr(recorder, 'is_running') and recorder.is_running():
                logger.info("Stopping recorder...")
                recorder.stop()
        except Exception as e:
            logger.error(f"Error stopping recorder: {e}")

    # Step 2: Stop controller (if exists and connected)
    if controller is not None:
        try:
            if controller.is_connected():
                # Stop acquisition if running
                if controller.state in (ConnectionState.ACQ_FREERUN,
                                       ConnectionState.ACQ_POLLED,
                                       ConnectionState.PAUSED):
                    logger.info("Stopping acquisition...")
                    controller.stop()

                # Disconnect
                logger.info("Disconnecting...")
                controller.disconnect()
        except Exception as e:
            logger.error(f"Error during controller teardown: {e}")


def wait_for_samples(controller: SensorController,
                     min_samples: int,
                     timeout: float = 30.0,
                     logger: Optional[logging.Logger] = None) -> bool:
    """Wait until controller buffer has at least min_samples readings.

    Args:
        controller: SensorController instance
        min_samples: Minimum number of samples to wait for
        timeout: Maximum wait time in seconds
        logger: Optional logger

    Returns:
        True if min_samples reached, False on timeout
    """
    if logger is None:
        logger = logging.getLogger(__name__)

    start = time.time()
    last_count = 0

    while time.time() - start < timeout:
        snapshot = controller.read_buffer_snapshot()
        current_count = len(snapshot)

        if current_count >= min_samples:
            logger.info(f"Reached {current_count} samples")
            return True

        if current_count != last_count:
            logger.debug(f"Buffer has {current_count} samples...")
            last_count = current_count

        time.sleep(0.5)

    logger.warning(f"Timeout after {timeout}s. Only {last_count} samples collected.")
    return False


def check_expected_range(actual: float,
                        min_val: float,
                        max_val: float,
                        name: str) -> tuple[bool, str]:
    """Check if actual value is within expected range.

    Args:
        actual: Actual measured value
        min_val: Minimum acceptable value
        max_val: Maximum acceptable value
        name: Name of the metric

    Returns:
        (passed, message) tuple
    """
    if min_val <= actual <= max_val:
        return True, f"{name}={actual:.2f} is within [{min_val}, {max_val}]"
    else:
        return False, f"{name}={actual:.2f} is OUTSIDE [{min_val}, {max_val}]"


def calculate_percentile_interval(readings, percentile: float = 95) -> float:
    """Calculate percentile inter-sample interval from readings.

    Args:
        readings: List of Reading instances with .ts attribute
        percentile: Percentile to calculate (default 95)

    Returns:
        Interval in milliseconds
    """
    if len(readings) < 2:
        return 0.0

    # Calculate intervals
    intervals = []
    for i in range(1, len(readings)):
        delta = (readings[i].ts - readings[i-1].ts).total_seconds()
        intervals.append(delta * 1000)  # Convert to ms

    # Sort and get percentile
    intervals.sort()
    idx = int(len(intervals) * (percentile / 100.0))
    idx = min(idx, len(intervals) - 1)

    return intervals[idx]
