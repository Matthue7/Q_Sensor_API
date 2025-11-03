"""High-level controller for Q-Series sensor with state management."""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Literal, Optional

from q_sensor_lib import parsing, protocol
from q_sensor_lib.errors import (
    DeviceResetError,
    InvalidConfigValue,
    InvalidResponse,
    MenuTimeout,
    SerialIOError,
)
from q_sensor_lib.models import ConnectionState, Reading, SensorConfig
from q_sensor_lib.ring_buffer import RingBuffer
from q_sensor_lib.transport import SerialLike, Transport

logger = logging.getLogger(__name__)


class SensorController:
    """High-level controller orchestrating Q-Series sensor operations.

    Manages connection state, configuration menu interactions, and data
    acquisition in both freerun and polled modes.
    """

    def __init__(
        self,
        transport: Optional[Transport] = None,
        buffer_size: int = 10000,
    ) -> None:
        """Initialize controller.

        Args:
            transport: Optional pre-configured Transport instance.
                      If None, must call connect() to create one.
            buffer_size: Maximum number of readings to buffer. Default 10000.
        """
        self._transport = transport
        self._state = ConnectionState.DISCONNECTED
        self._config: Optional[SensorConfig] = None
        self._sensor_id: str = "unknown"

        # Thread-safe ring buffer for readings
        self._buffer = RingBuffer(maxlen=buffer_size)

        # Threading for acquisition
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Lock for state transitions
        self._state_lock = threading.Lock()

        # Store connection params for reconnection
        self._last_port: Optional[str] = None
        self._last_baud: int = 9600

        # Store state before pause for resume
        self._paused_from_state: Optional[ConnectionState] = None
        self._last_poll_hz: float = 1.0  # Default poll rate for resume

    # ========================================================================
    # Connection Management
    # ========================================================================

    def connect(
        self,
        port: Optional[str] = None,
        baud: int = 9600,
        serial_port: Optional[SerialLike] = None,
    ) -> None:
        """Connect to sensor and enter configuration menu.

        Opens serial port, forces entry into config menu by sending ESC,
        waits for menu prompt, and reads current configuration snapshot.

        Args:
            port: Serial port name (e.g., "/dev/ttyUSB0"). Required if serial_port not given.
            baud: Baud rate. Default 9600.
            serial_port: Pre-configured serial port object (for testing). If provided,
                        port and baud are ignored.

        Raises:
            SerialIOError: If port cannot be opened or is already open
            MenuTimeout: If menu prompt not received
            InvalidResponse: If configuration cannot be read
        """
        with self._state_lock:
            if self._state != ConnectionState.DISCONNECTED:
                raise SerialIOError(f"Already connected (state: {self._state.value})")

            # Create transport if not injected
            if self._transport is None:
                if serial_port is not None:
                    self._transport = Transport(serial_port)
                elif port is not None:
                    self._transport = Transport.open(port, baud)
                    # Store for reconnection
                    self._last_port = port
                    self._last_baud = baud
                else:
                    raise ValueError("Must provide either 'port' or 'serial_port'")

            # Force entry into menu
            logger.info("Connecting to sensor, entering config menu...")
            self._enter_menu()

            # Read configuration snapshot
            self._config = self._read_config_snapshot()
            logger.info(f"Connected. Config: {self._config}")

            self._state = ConnectionState.CONFIG_MENU

    def disconnect(self) -> None:
        """Disconnect from sensor and clean up resources.

        Stops any acquisition, closes serial port, and resets state.
        """
        with self._state_lock:
            if self._state == ConnectionState.DISCONNECTED:
                return

            logger.info("Disconnecting from sensor...")

            # Stop acquisition threads if running
            self._stop_acquisition_thread()

            # Close transport
            if self._transport:
                self._transport.close()
                self._transport = None

            self._state = ConnectionState.DISCONNECTED
            logger.info("Disconnected")

    # ========================================================================
    # Configuration
    # ========================================================================

    def get_config(self) -> SensorConfig:
        """Get current sensor configuration.

        Returns:
            Current SensorConfig

        Raises:
            SerialIOError: If not connected or not in menu state
        """
        if self._state != ConnectionState.CONFIG_MENU:
            raise SerialIOError(
                f"Cannot get config in state {self._state.value}. "
                "Must be in CONFIG_MENU (call stop() first if acquiring)."
            )

        if not self._config:
            # Re-read config if lost
            self._config = self._read_config_snapshot()

        return self._config

    def set_averaging(self, n: int) -> SensorConfig:
        """Set number of readings to average.

        Args:
            n: Averaging count (1-65535)

        Returns:
            Updated SensorConfig

        Raises:
            InvalidConfigValue: If n is out of range or device rejects it
            SerialIOError: If not in CONFIG_MENU state
        """
        self._ensure_in_menu()

        if not (protocol.AVERAGING_MIN <= n <= protocol.AVERAGING_MAX):
            raise InvalidConfigValue(
                f"Averaging must be {protocol.AVERAGING_MIN}-{protocol.AVERAGING_MAX}, got {n}"
            )

        logger.info(f"Setting averaging to {n}...")
        assert self._transport is not None

        # Send 'A' command
        self._transport.write_cmd(protocol.MENU_CMD_AVERAGING)

        # Wait for prompt
        if not self._wait_for_prompt(protocol.RE_AVERAGING_PROMPT, timeout=5.0):
            raise MenuTimeout("Did not receive averaging prompt")

        # Send value
        self._transport.write_cmd(str(n))

        # Wait for confirmation or error
        start = time.time()
        while time.time() - start < 10.0:
            line = self._transport.readline()
            if not line:
                continue

            # Check for error
            if protocol.RE_ERROR_INVALID_AVERAGING.search(line):
                raise InvalidConfigValue(f"Device rejected averaging value {n}: {line}")

            # Check for success
            match = protocol.RE_AVERAGING_SET.search(line)
            if match:
                actual = int(match.group(1))
                logger.info(f"Averaging set to {actual}")

                # Update cached config
                if self._config:
                    self._config.averaging = actual

                # Wait for menu prompt to return
                if not self._wait_for_menu_prompt(timeout=protocol.TIMEOUT_MENU_PROMPT):
                    raise MenuTimeout("Menu did not re-appear after averaging change")

                # Additional delay to let full menu redisplay complete
                time.sleep(protocol.MENU_REDISPLAY_DELAY)

                return self.get_config()

        raise MenuTimeout("Timeout waiting for averaging confirmation")

    def set_adc_rate(self, rate_hz: int) -> SensorConfig:
        """Set ADC sample rate.

        Args:
            rate_hz: Sample rate in Hz. Must be in {4, 8, 16, 33, 62, 125, 250, 500}.

        Returns:
            Updated SensorConfig

        Raises:
            InvalidConfigValue: If rate is invalid or device rejects it
            SerialIOError: If not in CONFIG_MENU state
        """
        self._ensure_in_menu()

        if rate_hz not in protocol.VALID_ADC_RATES:
            raise InvalidConfigValue(
                f"ADC rate must be one of {protocol.VALID_ADC_RATES}, got {rate_hz}"
            )

        logger.info(f"Setting ADC rate to {rate_hz} Hz...")
        assert self._transport is not None

        # Send 'R' command
        self._transport.write_cmd(protocol.MENU_CMD_RATE)

        # Wait for first line of prompt ("Enter ADC rate...")
        if not self._wait_for_prompt(protocol.RE_RATE_PROMPT, timeout=5.0):
            raise MenuTimeout("Did not receive rate prompt")

        # Firmware sends 2-line prompt. Wait for second line ("Enter selection:")
        # to ensure full prompt is transmitted before we send the value
        second_line = self._transport.readline(timeout=1.0)
        if not second_line or "Enter selection" not in second_line:
            logger.warning(f"Expected 'Enter selection' prompt, got: {second_line}")

        # Send value
        self._transport.write_cmd(str(rate_hz))

        # Wait for confirmation or error
        start = time.time()
        while time.time() - start < 15.0:  # Allow for ADC config delay
            line = self._transport.readline()
            if not line:
                continue

            # Check for error
            if protocol.RE_ERROR_INVALID_RATE.search(line):
                raise InvalidConfigValue(f"Device rejected rate {rate_hz}: {line}")

            # Check for success
            match = protocol.RE_RATE_SET.search(line)
            if match:
                actual = int(match.group(1))
                logger.info(f"ADC rate set to {actual} Hz")

                # Update cached config
                if self._config:
                    self._config.adc_rate_hz = actual

                # Wait for menu prompt
                if not self._wait_for_menu_prompt(timeout=protocol.TIMEOUT_MENU_PROMPT):
                    raise MenuTimeout("Menu did not re-appear after rate change")

                # Additional delay to let full menu redisplay complete
                time.sleep(protocol.MENU_REDISPLAY_DELAY)

                return self.get_config()

        raise MenuTimeout("Timeout waiting for rate confirmation")

    def set_mode(
        self, mode: Literal["freerun", "polled"], tag: Optional[str] = None
    ) -> SensorConfig:
        """Set operating mode (freerun or polled).

        Args:
            mode: "freerun" for continuous streaming, "polled" for on-demand queries
            tag: Single uppercase character A-Z (required if mode="polled")

        Returns:
            Updated SensorConfig

        Raises:
            InvalidConfigValue: If mode is invalid, tag is missing/invalid, or device rejects
            SerialIOError: If not in CONFIG_MENU state
        """
        self._ensure_in_menu()

        if mode not in ("freerun", "polled"):
            raise InvalidConfigValue(f"Mode must be 'freerun' or 'polled', got '{mode}'")

        if mode == "polled":
            if not tag or len(tag) != 1 or tag not in protocol.VALID_TAGS:
                raise InvalidConfigValue(
                    f"Tag must be single uppercase A-Z for polled mode, got '{tag}'"
                )

        logger.info(f"Setting mode to {mode}" + (f" with tag '{tag}'" if tag else ""))
        assert self._transport is not None

        # Send 'M' command (no CR for single character menu command)
        self._transport.write_bytes(protocol.MENU_CMD_MODE.encode("ascii"))

        # Wait for mode prompt (firmware sends 2-line prompt)
        if not self._wait_for_prompt(protocol.RE_MODE_PROMPT, timeout=5.0):
            raise MenuTimeout("Did not receive mode prompt")

        # Delay to ensure firmware completes full prompt transmission
        time.sleep(0.5)

        # Send mode choice character (single char input for HRSIn - no CR needed)
        mode_char = "0" if mode == "freerun" else "1"
        self._transport.write_bytes(mode_char.encode("ascii"))

        if mode == "polled":
            # Wait for TAG prompt (firmware sends 2-line prompt)
            if not self._wait_for_prompt(protocol.RE_TAG_PROMPT, timeout=5.0):
                raise MenuTimeout("Did not receive TAG prompt")

            # Delay to ensure firmware completes full prompt transmission
            time.sleep(0.5)

            # Send TAG character (single char input for HRSIn - no CR needed)
            assert tag is not None
            self._transport.write_bytes(tag.encode("ascii"))

            # Check for bad tag error
            start = time.time()
            while time.time() - start < 3.0:
                line = self._transport.readline()
                if not line:
                    continue
                if protocol.RE_ERROR_BAD_TAG.search(line):
                    raise InvalidConfigValue(f"Device rejected TAG '{tag}': {line}")

        # Update cached config
        if self._config:
            self._config.mode = mode  # type: ignore
            self._config.tag = tag

        # Wait for menu prompt
        if not self._wait_for_menu_prompt(timeout=protocol.TIMEOUT_MENU_PROMPT):
            raise MenuTimeout("Menu did not re-appear after mode change")

        logger.info(f"Mode set to {mode}" + (f" with tag {tag}" if tag else ""))
        return self.get_config()

    # ========================================================================
    # Acquisition Control
    # ========================================================================

    def start_acquisition(self, poll_hz: float = 1.0) -> None:
        """Exit menu and start data acquisition in configured mode.

        Sends 'X' command which triggers device reset. After reset, device
        enters run mode (freerun or polled) based on stored configuration.

        For freerun: Starts reader thread that continuously parses incoming lines.
        For polled: Sends *<TAG>Q000! init command, then starts poller thread
                    that queries at poll_hz rate.

        Args:
            poll_hz: Polling rate for polled mode (1-15 Hz recommended). Ignored in freerun.

        Raises:
            SerialIOError: If not in CONFIG_MENU state or already acquiring
            DeviceResetError: If device reset behavior is unexpected
        """
        self._ensure_in_menu()

        # Micro-fix #5: Use state instead of thread liveness for "already running" check
        if self._state in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
            raise SerialIOError("Acquisition already running")

        config = self.get_config()
        logger.info(f"Starting acquisition in {config.mode} mode...")

        assert self._transport is not None

        # Send 'X' to exit menu (triggers device reset)
        self._transport.write_cmd(protocol.MENU_CMD_EXIT)

        # Critical Fix #2: Wait for device to reboot before flushing
        logger.debug("Device resetting, waiting for reboot...")
        time.sleep(protocol.DELAY_POST_RESET)

        # Flush banner from input buffer
        self._transport.flush_input()

        # Start appropriate acquisition mode
        with self._state_lock:
            if config.mode == "freerun":
                self._state = ConnectionState.ACQ_FREERUN
                self._start_freerun_thread()
            elif config.mode == "polled":
                self._state = ConnectionState.ACQ_POLLED
                # Store poll_hz for resume
                self._last_poll_hz = poll_hz
                self._start_polled_thread(config.tag or "A", poll_hz)

        logger.info(f"Acquisition started in {config.mode} mode")

    def pause(self) -> None:
        """Pause acquisition and enter menu (from freerun or polled mode).

        Stops reader thread, sends ESC to enter menu, and transitions to PAUSED state.
        Call resume() to restart acquisition in the same mode.

        Raises:
            SerialIOError: If not in acquisition mode
        """
        with self._state_lock:
            if self._state not in (ConnectionState.ACQ_FREERUN, ConnectionState.ACQ_POLLED):
                raise SerialIOError(
                    f"Cannot pause from state {self._state.value}. Must be acquiring."
                )

            logger.info("Pausing acquisition...")

            # Critical Fix #4: Record state before pausing for resume
            self._paused_from_state = self._state

            # Stop acquisition thread
            self._stop_acquisition_thread()

            # Enter menu
            assert self._transport is not None
            self._enter_menu()

            self._state = ConnectionState.PAUSED
            logger.info(f"Acquisition paused (was {self._paused_from_state.value}), in menu")

    def resume(self) -> None:
        """Resume acquisition from paused state.

        Exits menu with 'X', handles reset, and restarts acquisition threads
        in the same mode (freerun or polled) that was active before pause.

        Raises:
            SerialIOError: If not in PAUSED state
        """
        if self._state != ConnectionState.PAUSED:
            raise SerialIOError(
                f"Cannot resume from state {self._state.value}. Must be PAUSED."
            )

        # Critical Fix #4: Check we have a saved state
        if self._paused_from_state is None:
            raise SerialIOError("Cannot resume: no saved state from pause")

        logger.info(f"Resuming acquisition (restoring {self._paused_from_state.value})...")

        # Re-read config to get current mode and parameters
        config = self._read_config_snapshot()
        self._config = config

        # Exit menu with 'X' (triggers device reset)
        assert self._transport is not None
        self._transport.write_cmd(protocol.MENU_CMD_EXIT)

        # Wait for device to reboot
        logger.debug("Device resetting, waiting for reboot...")
        time.sleep(protocol.DELAY_POST_RESET)

        # Flush banner from input buffer
        self._transport.flush_input()

        # Restart appropriate reader thread based on saved mode
        if self._paused_from_state == ConnectionState.ACQ_FREERUN:
            self._start_freerun_thread()
            self._state = ConnectionState.ACQ_FREERUN
            logger.info("Resumed freerun acquisition")
        elif self._paused_from_state == ConnectionState.ACQ_POLLED:
            # Use same poll rate as before (default 1 Hz if not stored)
            poll_hz = getattr(self, '_last_poll_hz', 1.0)
            tag = config.tag or "A"
            self._start_polled_thread(tag, poll_hz)
            self._state = ConnectionState.ACQ_POLLED
            logger.info(f"Resumed polled acquisition at {poll_hz} Hz")
        else:
            raise SerialIOError(f"Cannot resume from saved state {self._paused_from_state.value}")

        # Clear saved state
        self._paused_from_state = None

    def stop(self) -> None:
        """Stop acquisition and return to CONFIG_MENU state.

        Unlike pause(), this is a clean stop that leaves controller in menu.
        Use when you want to reconfigure before next acquisition.

        Raises:
            SerialIOError: If not in acquisition or paused state
        """
        with self._state_lock:
            if self._state not in (
                ConnectionState.ACQ_FREERUN,
                ConnectionState.ACQ_POLLED,
                ConnectionState.PAUSED,
            ):
                raise SerialIOError(
                    f"Cannot stop from state {self._state.value}. Not acquiring."
                )

            logger.info("Stopping acquisition...")

            # Stop threads if running
            self._stop_acquisition_thread()

            # Critical Fix #3: Always enter menu after stopping
            # (even if paused, re-enter menu to ensure clean state)
            assert self._transport is not None
            self._enter_menu()

            self._state = ConnectionState.CONFIG_MENU
            # Clear saved pause state
            self._paused_from_state = None
            logger.info("Acquisition stopped, in menu")

    # ========================================================================
    # Data Access
    # ========================================================================

    def read_buffer_snapshot(self) -> list[Reading]:
        """Get a snapshot of all buffered readings.

        Thread-safe. Returns copy of current buffer contents.

        Returns:
            List of Reading instances, ordered oldest to newest
        """
        return self._buffer.snapshot()

    def clear_buffer(self) -> None:
        """Clear all buffered readings."""
        self._buffer.clear()

    def read_latest(self) -> Optional[Reading]:
        """Get the most recent reading from buffer.

        Thread-safe. Returns None if buffer is empty.

        Returns:
            Latest Reading instance or None
        """
        snapshot = self._buffer.snapshot()
        return snapshot[-1] if snapshot else None

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def sensor_id(self) -> str:
        """Sensor serial number/ID."""
        return self._sensor_id

    def is_connected(self) -> bool:
        """Check if controller is connected to sensor.

        Returns:
            True if transport is open and state is not DISCONNECTED
        """
        return (
            self._transport is not None
            and self._transport.is_open
            and self._state != ConnectionState.DISCONNECTED
        )

    def reconnect(self) -> None:
        """Reconnect to sensor using last known port/baud.

        Disconnects current session if connected, then reconnects with
        stored connection parameters.

        Raises:
            SerialIOError: If no previous connection exists or reconnection fails
        """
        if self._last_port is None:
            raise SerialIOError("Cannot reconnect: no previous connection")

        logger.info(f"Reconnecting to {self._last_port} at {self._last_baud} baud...")

        # Disconnect if currently connected
        if self.is_connected():
            self.disconnect()

        # Reconnect with stored parameters
        self.connect(port=self._last_port, baud=self._last_baud)

    # ========================================================================
    # Internal Helpers: Menu Operations
    # ========================================================================

    def _ensure_in_menu(self) -> None:
        """Raise if not in CONFIG_MENU state."""
        if self._state != ConnectionState.CONFIG_MENU:
            raise SerialIOError(
                f"Operation requires CONFIG_MENU state, current: {self._state.value}"
            )

    def _enter_menu(self) -> None:
        """Force entry into config menu by sending ESC and waiting for prompt."""
        assert self._transport is not None

        # Wait for power-on banner to complete (sensor outputs ~11 lines on startup)
        # If we send ESC while banner is printing, sensor ignores it
        time.sleep(protocol.DELAY_POST_OPEN)

        # Flush any pending input (discard power-on banner)
        self._transport.flush_input()

        # Send ESC to enter menu
        self._transport.write_bytes(protocol.ESC)

        # Wait for menu prompt
        if not self._wait_for_menu_prompt(timeout=protocol.TIMEOUT_MENU_PROMPT):
            raise MenuTimeout("Did not receive menu prompt after ESC")

        logger.debug("Entered config menu")

    def _wait_for_menu_prompt(self, timeout: float = 5.0) -> bool:
        """Wait for "Select the letter of the menu entry:" prompt.

        Args:
            timeout: Max seconds to wait

        Returns:
            True if prompt found, False on timeout
        """
        return self._wait_for_prompt(protocol.RE_MENU_PROMPT, timeout)

    def _wait_for_prompt(
        self, pattern: "re.Pattern[str]", timeout: float
    ) -> bool:
        """Wait for a line matching regex pattern.

        Args:
            pattern: Compiled regex to match
            timeout: Max seconds to wait

        Returns:
            True if pattern matched, False on timeout
        """
        assert self._transport is not None
        start = time.time()

        while time.time() - start < timeout:
            line = self._transport.readline()
            if not line:
                continue

            if pattern.search(line):
                logger.debug(f"Found prompt: {line!r}")
                return True

        logger.warning(f"Timeout waiting for pattern {pattern.pattern}")
        return False

    def _read_config_snapshot(self) -> SensorConfig:
        """Read current config by sending '^' and parsing CSV response.

        Returns:
            SensorConfig parsed from device response

        Raises:
            MenuTimeout: If config CSV not received
            InvalidResponse: If CSV cannot be parsed
        """
        assert self._transport is not None

        # Send '^' to get config dump
        self._transport.write_cmd(protocol.MENU_CMD_CONFIG_DUMP)

        # Read until we get a line that looks like CSV
        start = time.time()
        while time.time() - start < 5.0:
            line = self._transport.readline()
            if not line:
                continue

            # Try parsing as config CSV
            try:
                config, extras = parsing.parse_config_csv(line)
                self._sensor_id = config.serial_number
                logger.debug(f"Parsed config from CSV: {config}")
                return config
            except InvalidResponse:
                # Not the CSV line yet, keep reading
                continue

        raise MenuTimeout("Timeout waiting for config CSV response")

    # ========================================================================
    # Internal Helpers: Acquisition Threads
    # ========================================================================

    def _start_freerun_thread(self) -> None:
        """Start background thread to read freerun data stream."""
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._freerun_reader_loop,
            name="FreerunReader",
            daemon=True,
        )
        self._reader_thread.start()
        logger.debug("Started freerun reader thread")

    def _start_polled_thread(self, tag: str, poll_hz: float) -> None:
        """Start background thread to poll at specified rate.

        Protocol assumptions (require hardware verification):
        - Init command format: `*<TAG>Q000!` with CR terminator (e.g., `*AQ000!\r`)
        - Device acknowledges init (or silently accepts - verify on hardware)
        - After init, device responds to `><TAG>` queries

        Args:
            tag: TAG character for queries
            poll_hz: Polling frequency in Hz
        """
        assert self._transport is not None

        # Critical Fix #5: Send polled init command with CR terminator
        # write_cmd() adds CR automatically per protocol spec
        init_cmd = protocol.make_polled_init_cmd(tag)
        self._transport.write_cmd(init_cmd)
        logger.debug(f"Sent polled init command: {init_cmd} (with CR)")

        # Wait for averaging to fill
        if self._config:
            wait_time = self._config.sample_period_s + 0.5
            logger.debug(f"Waiting {wait_time:.2f}s for averaging to fill...")
            time.sleep(wait_time)

        # Start poller thread
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._polled_reader_loop,
            args=(tag, poll_hz),
            name="PolledReader",
            daemon=True,
        )
        self._reader_thread.start()
        logger.debug(f"Started polled reader thread at {poll_hz} Hz")

    def _stop_acquisition_thread(self) -> None:
        """Stop and join acquisition thread if running."""
        if self._reader_thread and self._reader_thread.is_alive():
            logger.debug("Stopping acquisition thread...")
            self._stop_event.set()
            self._reader_thread.join(timeout=5.0)

            if self._reader_thread.is_alive():
                logger.warning("Acquisition thread did not stop cleanly")

            self._reader_thread = None

    def _freerun_reader_loop(self) -> None:
        """Background thread loop for freerun mode.

        Continuously reads lines, parses as freerun data, and appends to buffer.
        """
        logger.info(f"Freerun reader loop started (thread {threading.get_ident()})")
        assert self._transport is not None

        while not self._stop_event.is_set():
            try:
                line = self._transport.readline()
                if not line:
                    continue

                # Filter out known menu/banner/diagnostic lines before attempting parse
                # These appear after 'X' command due to device reset
                menu_markers = [
                    "Select the letter of",
                    " to set ",
                    "Operating in",
                    "ADC sample rate",
                    "Averaging",
                    "Sensor temperature:",
                    "Input Supply Voltage",
                    "Calfactor:",
                    "Reset ADC",
                    "Start free run",
                    "Starting Sampling",
                    "Biospherical Instruments",
                    "Digital Engine",
                    "Unit ID",
                    "Rebooting program",
                    "gain ",
                    "Buffer disabled",
                ]

                if any(marker in line for marker in menu_markers):
                    logger.debug(f"Skipping menu/diagnostic line: {line[:60]}")
                    continue

                # Try parsing as freerun data
                try:
                    data = parsing.parse_freerun_line(line)
                    reading = Reading(
                        ts=datetime.now(timezone.utc),
                        sensor_id=self._sensor_id,
                        mode="freerun",
                        data=data,
                    )
                    self._buffer.append(reading)
                    logger.debug(f"Freerun reading: {data}")

                except InvalidResponse as e:
                    # Line might be banner noise or error - log but continue
                    logger.debug(f"Skipping unparseable line: {e}")

            except Exception as e:
                logger.error(f"Error in freerun reader loop: {e}", exc_info=True)
                # Don't crash thread on transient errors
                time.sleep(0.1)

        logger.info("Freerun reader loop stopped")

    def _polled_reader_loop(self, tag: str, poll_hz: float) -> None:
        """Background thread loop for polled mode.

        Sends query command at poll_hz rate, reads response, parses, and buffers.

        Protocol assumptions (require hardware verification):
        - Query command format: `><TAG>` (e.g., `>A`) with CR terminator
        - Response format: `<TAG><value>[,TempC][,Vin]` with CRLF terminator
        - TAG validation: Response TAG must match query TAG (strict check)
        - Timing: Device responds within readline timeout (default 0.5s)

        Args:
            tag: TAG character for queries
            poll_hz: Polling frequency in Hz
        """
        logger.info(f"Polled reader loop started (thread {threading.get_ident()}) at {poll_hz} Hz")
        assert self._transport is not None

        poll_period = 1.0 / poll_hz
        query_cmd = protocol.make_polled_query_cmd(tag)

        while not self._stop_event.is_set():
            cycle_start = time.time()

            try:
                # Send query
                self._transport.write_cmd(query_cmd)

                # Read response with timeout
                line = self._transport.readline()
                if not line:
                    logger.warning("No response to polled query, will retry")
                    # Micro-fix #4: Use Event.wait for cancellable sleep
                    if self._stop_event.wait(timeout=poll_period):
                        break  # Stop event set during sleep
                    continue

                # Parse polled line (Critical Fix #5: TAG validation already in parsing.py)
                try:
                    data = parsing.parse_polled_line(line, tag)
                    reading = Reading(
                        ts=datetime.now(timezone.utc),
                        sensor_id=self._sensor_id,
                        mode="polled",
                        data=data,
                    )
                    self._buffer.append(reading)
                    logger.debug(f"Polled reading: {data}")

                except InvalidResponse as e:
                    logger.warning(f"Failed to parse polled response: {e}")

            except Exception as e:
                logger.error(f"Error in polled reader loop: {e}", exc_info=True)

            # Micro-fix #4: Use Event.wait for cancellable sleep
            elapsed = time.time() - cycle_start
            sleep_time = max(0, poll_period - elapsed)
            if sleep_time > 0:
                if self._stop_event.wait(timeout=sleep_time):
                    break  # Stop event set during sleep

        logger.info("Polled reader loop stopped")
