"""Fake serial port that simulates Q-Series sensor firmware 4.003 behavior.

This simulator emulates the exact protocol documented in 00_Serial_protocol_2150.md,
including menu navigation, command responses, timing, and data streaming.
"""

import logging
import queue
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class FakeSerial:
    """Deterministic simulator of Q-Series sensor firmware behavior.

    Implements exact wire protocol including:
    - Menu system with prompts and command handling
    - Device reset sequence on 'X' command
    - Freerun continuous streaming
    - Polled mode with initialization and query sequences
    - Error messages for invalid inputs
    - Exact line terminators (CR input, CRLF output)
    """

    def __init__(
        self,
        serial_number: str = "Q12345",
        firmware_version: str = "4.003",
        quiet_mode: bool = False,
    ) -> None:
        """Initialize fake sensor.

        Args:
            serial_number: Device serial number
            firmware_version: Firmware version string
            quiet_mode: If True, suppress power-on banner
        """
        # Device identity
        self.serial_number = serial_number
        self.firmware_version = firmware_version
        self.quiet_mode = quiet_mode

        # Configuration state (stored in simulated EEPROM)
        self.averaging = 125
        self.adc_rate_hz = 125
        self.operating_mode = "0"  # "0"=freerun, "1"=polled
        self.tag = "A"
        self.include_temp = False
        self.include_vin = False
        self.preamble = ""
        self.calfactor = 1.0

        # Runtime state
        self._state = "init"  # "init", "menu", "freerun", "polled"
        self._sampling_started = False  # For polled mode initialization
        self._last_menu_cmd: Optional[str] = None  # Track last menu command for context

        # Output queue for lines to send to "host"
        self._output_queue: queue.Queue[bytes] = queue.Queue()

        # Input buffer for commands from "host"
        self._input_buffer = bytearray()

        # Threading for freerun streaming
        self._stream_thread: Optional[threading.Thread] = None
        self._stop_streaming = threading.Event()

        # Port state
        self.is_open = True
        self.timeout = 0.5  # Readline timeout (matches Transport default)

        # Start in init state - will send banner on first interaction
        self._initial_startup = True

    def close(self) -> None:
        """Close the fake serial port."""
        self.is_open = False
        self._stop_streaming_thread()
        logger.debug("FakeSerial closed")

    def write(self, data: bytes) -> int:
        """Write data to device (from host perspective).

        Handles commands terminated with CR (0x0D).

        Args:
            data: Bytes to write

        Returns:
            Number of bytes written
        """
        if not self.is_open:
            raise RuntimeError("Port is closed")

        self._input_buffer.extend(data)
        logger.debug(f"FakeSerial received: {data!r}")

        # Process complete commands (terminated by CR)
        self._process_input()

        return len(data)

    def read(self, size: int = 1) -> bytes:
        """Read bytes from device output (not commonly used in practice)."""
        # For simplicity, we mainly support readline()
        return b""

    def readline(self) -> bytes:
        """Read one line from device output.

        Returns:
            Line as bytes with CRLF terminator, or b"" on timeout
        """
        if not self.is_open:
            raise RuntimeError("Port is closed")

        try:
            # Non-blocking read with short timeout
            line = self._output_queue.get(timeout=0.2)
            logger.debug(f"FakeSerial sending line: {line!r}")
            return line
        except queue.Empty:
            return b""

    def flush(self) -> None:
        """Flush output buffer (no-op for fake serial)."""
        # In real serial, this forces transmission
        # For fake serial, writes are immediate, so this is a no-op
        pass

    def reset_input_buffer(self) -> None:
        """Flush input buffer."""
        self._input_buffer.clear()
        logger.debug("FakeSerial input buffer flushed")

    # ========================================================================
    # Internal: Input Processing
    # ========================================================================

    def _process_input(self) -> None:
        """Process incoming bytes, handle complete commands."""
        # Look for CR-terminated commands
        while b"\r" in self._input_buffer:
            idx = self._input_buffer.index(b"\r")
            cmd_bytes = bytes(self._input_buffer[:idx])
            self._input_buffer = self._input_buffer[idx + 1 :]  # Consume including CR

            # Decode command
            cmd = cmd_bytes.decode("ascii", errors="ignore").upper()

            # Handle based on current state
            if self._state == "init":
                self._handle_init_command(cmd)
            elif self._state == "menu":
                self._handle_menu_command(cmd)
            elif self._state == "freerun":
                self._handle_freerun_command(cmd)
            elif self._state == "polled":
                self._handle_polled_command(cmd)

        # Also check for ESC/? (single byte, no CR)
        if b"\x1b" in self._input_buffer or b"?" in self._input_buffer:
            # Remove ESC or ?
            if b"\x1b" in self._input_buffer:
                self._input_buffer.remove(ord("\x1b"))
            if b"?" in self._input_buffer:
                self._input_buffer.remove(ord("?"))

            # Enter menu from any state
            self._enter_menu_from_interrupt()

    def _handle_init_command(self, cmd: str) -> None:
        """Handle commands in initial state."""
        # Send power-on banner
        if not self.quiet_mode:
            self._send_power_on_banner()

        # Transition based on operating mode
        if self.operating_mode == "0":
            self._enter_freerun_mode()
        else:
            self._enter_polled_mode()

    def _handle_menu_command(self, cmd: str) -> None:
        """Handle menu commands."""
        if not cmd:
            return

        first_char = cmd[0]

        # Special case: If waiting for TAG input after MODE=1, treat single letters as TAG
        logger.debug(f"_handle_menu_command: cmd={cmd!r}, _last_menu_cmd={self._last_menu_cmd}, operating_mode={self.operating_mode}")
        if self._last_menu_cmd == "M" and self.operating_mode == "1":
            if len(cmd) == 1 and "A" <= first_char <= "Z":
                # TAG input
                logger.debug(f"Accepting TAG: {first_char}")
                self.tag = first_char
                time.sleep(0.1)
                self._send_menu_prompt()
                self._last_menu_cmd = None  # Clear context
                return
            elif len(cmd) == 1:
                # Invalid TAG
                self._send_line(" Bad TAG ")
                time.sleep(0.1)
                self._send_menu_prompt()
                self._last_menu_cmd = None  # Clear context
                return

        if first_char == "A":
            # Averaging command
            self._last_menu_cmd = "A"  # Track context
            self._send_line("If you set this to 125 averaged and use R command to set ADC rate to")
            self._send_line("125 samples per second, then you will get data at roughly 1hz.")
            self._send_line("Enter # readings to average before update (1-65535): ")
            # Wait for numeric input (will come as separate command)

        elif first_char == "R":
            # Rate command
            self._last_menu_cmd = "R"  # Track context
            self._send_line("Enter ADC rate (4, 8, 16, 33, 62, 125, 250* Hz)")
            self._send_line(" *250Hz is at reduced resolution     ---- Enter selection: ")

        elif first_char == "M":
            # Mode command
            self._last_menu_cmd = "M"  # Track context
            self._send_line(
                "Set operating mode.  Mode 0 is freerun, 1 is polled.  "
                "Polled require a TAG to be defined"
            )
            self._send_line("Enter the operating mode number: ")

        elif first_char == "^":
            # Config dump
            self._send_config_csv()
            time.sleep(0.1)  # Small delay before menu redisplay
            self._send_menu_prompt()

        elif first_char == "X":
            # Exit and reset
            self._send_line("Calling reset!")
            self._send_line("Rebooting program")
            time.sleep(0.1)
            self._reset_device()

        elif first_char == "?" or first_char == "\x1b":
            # Redisplay menu
            self._send_menu()
            self._send_menu_prompt()

        elif first_char.isdigit():
            # Numeric input (e.g., response to averaging/rate/mode prompts)
            self._handle_numeric_input(cmd)

        else:
            # Unknown command - just redisplay menu
            self._send_menu_prompt()

    def _handle_numeric_input(self, value_str: str) -> None:
        """Handle numeric input in response to prompts.

        Uses context from _last_menu_cmd to distinguish between averaging, rate, and mode.
        """
        try:
            val = int(value_str)

            # Use context to determine what we're setting
            if self._last_menu_cmd == "A":
                # Averaging command context
                self.averaging = val
                self._send_line(f"{val} was entered")
                self._send_line("")
                self._send_line(f"ADC set to averaging {val}")
                time.sleep(0.2)
                self._send_menu_prompt()
                self._last_menu_cmd = None  # Clear context

            elif self._last_menu_cmd == "R":
                # Rate command context
                if val in {4, 8, 16, 33, 62, 125, 250, 500}:
                    self.adc_rate_hz = val
                    self._send_line("")
                    self._send_line(f"ADC rate set to {val}")
                    time.sleep(0.2)
                    self._send_menu_prompt()
                    self._last_menu_cmd = None  # Clear context
                else:
                    # Invalid rate
                    self._send_line("")
                    self._send_line("")
                    self._send_line("")
                    self._send_line("Invalid rate!!! Command is ignored.")
                    time.sleep(0.5)
                    self._send_menu_prompt()
                    self._last_menu_cmd = None  # Clear context

            elif self._last_menu_cmd == "M":
                # Mode command context
                if val == 0:
                    self.operating_mode = "0"
                    self._send_line("0")
                    time.sleep(0.1)
                    self._send_menu_prompt()
                    self._last_menu_cmd = None  # Clear context
                elif val == 1:
                    self.operating_mode = "1"
                    self._send_line("")
                    self._send_line(
                        "Enter the single character that will be the tag used in polling "
                        "(A-F) UPPER case"
                    )
                    logger.debug("Sending 'Note tags' line from numeric input handler (MODE=1)")
                    self._send_line(
                        "Note tags G-Z may not be supported in some Biospherical "
                        "acquisition software : "
                    )
                    # Wait for TAG input (don't clear context yet)
                else:
                    # Invalid mode
                    self._send_line("")
                    self._send_line("Invalid mode")
                    time.sleep(0.1)
                    self._send_menu_prompt()
                    self._last_menu_cmd = None  # Clear context

            else:
                # No context - shouldn't happen in normal flow, but fallback to old logic
                # Check ADC rates (discrete set)
                if val in {4, 8, 16, 33, 62, 125, 250, 500}:
                    self.adc_rate_hz = val
                    self._send_line("")
                    self._send_line(f"ADC rate set to {val}")
                    time.sleep(0.2)
                    self._send_menu_prompt()
                # Check mode selection
                elif val in {0, 1}:
                    if val == 0:
                        self.operating_mode = "0"
                        self._send_line("0")
                        time.sleep(0.1)
                        self._send_menu_prompt()
                    else:
                        self.operating_mode = "1"
                        self._send_line("")
                        self._send_line(
                            "Enter the single character that will be the tag used in polling "
                            "(A-F) UPPER case"
                        )
                        logger.debug("Sending 'Note tags' line from fallback handler (MODE=1)")
                        self._send_line(
                            "Note tags G-Z may not be supported in some Biospherical "
                            "acquisition software : "
                        )
                # Assume averaging
                elif 1 <= val <= 65535:
                    self.averaging = val
                    self._send_line(f"{val} was entered")
                    self._send_line("")
                    self._send_line(f"ADC set to averaging {val}")
                    time.sleep(0.2)
                    self._send_menu_prompt()
                else:
                    # Invalid
                    self._send_line("")
                    self._send_line("")
                    self._send_line("")
                    self._send_line("Invalid value")
                    time.sleep(0.5)
                    self._send_menu_prompt()

        except ValueError:
            # Not a number - might be TAG
            if len(value_str) == 1 and "A" <= value_str <= "Z":
                self.tag = value_str
                time.sleep(0.1)
                self._send_menu_prompt()
                self._last_menu_cmd = None  # Clear context
            else:
                self._send_line(" Bad TAG ")
                time.sleep(0.1)
                self._send_menu_prompt()
                self._last_menu_cmd = None  # Clear context

    def _handle_freerun_command(self, cmd: str) -> None:
        """Handle commands in freerun mode (only ESC/? handled via interrupt)."""
        pass  # Freerun mode doesn't process commands except interrupts

    def _handle_polled_command(self, cmd: str) -> None:
        """Handle commands in polled mode.

        Expected sequences:
        - *<TAG>Q000! -> Initialize sampling
        - ><TAG> -> Query single sample
        """
        if cmd.startswith("*") and "Q" in cmd and cmd.endswith("!"):
            # Polled init command: *<TAG>Q000!
            tag_from_cmd = cmd[1] if len(cmd) > 1 else ""
            if tag_from_cmd == self.tag:
                self._sampling_started = True
                logger.debug(f"Polled sampling initialized with tag {self.tag}")
                # No response to init command

        elif cmd.startswith(">"):
            # Polled query: ><TAG>
            tag_from_cmd = cmd[1] if len(cmd) > 1 else ""
            if tag_from_cmd == self.tag:
                if self._sampling_started:
                    # Send single data line
                    self._send_polled_data_line()
                else:
                    # No response - sampling not started
                    logger.warning("Polled query received but sampling not initialized")

    def _enter_menu_from_interrupt(self) -> None:
        """Handle ESC or ? interrupt - stop streaming and enter menu."""
        logger.debug("Interrupt received, entering menu")

        # Stop freerun thread if running
        self._stop_streaming_thread()

        # Enter menu state
        self._state = "menu"

        # Send banner and menu
        if not self.quiet_mode:
            self._send_power_on_banner()

        self._send_menu()
        self._send_menu_prompt()

    # ========================================================================
    # Internal: State Transitions
    # ========================================================================

    def _reset_device(self) -> None:
        """Simulate device reset (after X command)."""
        logger.debug("Device resetting...")

        # Stop any streaming
        self._stop_streaming_thread()
        self._sampling_started = False

        # Send power-on banner
        if not self.quiet_mode:
            self._send_power_on_banner()

        # Enter run mode based on operating_mode
        if self.operating_mode == "0":
            self._enter_freerun_mode()
        else:
            self._enter_polled_mode()

    def _enter_freerun_mode(self) -> None:
        """Transition to freerun streaming mode."""
        self._state = "freerun"

        if not self.quiet_mode:
            self._send_line("Start free run sampling")
            self._send_line(f"Starting Sampling; quiet mode ={1 if self.quiet_mode else 0}")

        # Start streaming thread
        self._start_streaming_thread()
        logger.debug("Entered freerun mode")

    def _enter_polled_mode(self) -> None:
        """Transition to polled mode."""
        self._state = "polled"
        self._sampling_started = False

        if not self.quiet_mode:
            self._send_line("Entering polled mainline sampling")

        logger.debug("Entered polled mode")

    # ========================================================================
    # Internal: Output Generation
    # ========================================================================

    def _send_line(self, text: str) -> None:
        """Send a line with CRLF terminator."""
        line_bytes = text.encode("ascii") + b"\r\n"
        self._output_queue.put(line_bytes)

    def _send_power_on_banner(self) -> None:
        """Send power-on signon banner."""
        self._send_line("")
        self._send_line(
            f"Biospherical Instruments Inc: Digital Engine Vers {self.firmware_version}"
        )
        self._send_line(f"Unit ID {self.serial_number}")

        if self.operating_mode == "0":
            self._send_line("Operating in free run mode")
        else:
            self._send_line(f"Operating in polled mode with tag of {self.tag}")

        self._send_line(f"ADC sample rate {self.adc_rate_hz}, gain 1")

        if self.averaging > 1:
            self._send_line(f"Averaging {self.averaging} readings")
        else:
            self._send_line("No Averaging")

        self._send_line("ADC Buffer disabled")
        self._send_line("Sensor temperature: 21.50 C")
        self._send_line("Input Supply Voltage: 12.345v")
        self._send_line(f"Calfactor: {self.calfactor:.6f}")

    def _send_menu(self) -> None:
        """Send menu display."""
        self._send_line("")
        self._send_line(
            f"Biospherical Instruments Inc: Digital Log Engine v: {self.firmware_version}"
        )
        self._send_line("")
        self._send_line(f"Model: {self.serial_number}")
        self._send_line(f"A to set number of samples averaged before update: {self.averaging}")
        self._send_line("B to set the baudrate, now: 9600")
        self._send_line(f"C to set the Calibration Factor for digital output: {self.calfactor:.6f}")
        self._send_line("D to set the description available for display in software: ")
        self._send_line("I to send unit ID and serial number (password protected)")
        self._send_line("J to set the immersion factor (not automatically applied): 1.000000")
        self._send_line("K to set dark zero, currently dark = 0.000000v. ")
        mode_text = (
            f"0=streaming, 1=polled with tag= {self.tag}) currently {self.operating_mode}"
        )
        self._send_line(f"M to set the operating mode ({mode_text}")
        self._send_line("N to set analog output mode: Digital only")
        self._send_line("O to configure the OUTPUTs, temperature is disabled, line voltage is disabled")
        self._send_line("P to set a preamble for the data transmission = ")
        self._send_line("Q to set startup mode (1 = no diagnostic messages, or 0 for diagnostics): 0")
        self._send_line(f"R to set ADC sample rate: {self.adc_rate_hz}")
        self._send_line("S to send ADC diagnostic information")
        self._send_line("U sets the calibration units: ")
        self._send_line("W to set the how calibration constants are used")
        self._send_line("X to restart sampling")
        self._send_line("Y to set Log Offset (currently 0.000000 volts)")
        self._send_line("Z to set DAC Offset (currently 0.000000 volts)")
        self._send_line("Sensor temperature: 21.50 C")
        self._send_line("Input Supply Voltage measures 12.345v")

    def _send_menu_prompt(self) -> None:
        """Send menu entry selection prompt."""
        self._send_line("")
        self._send_line("Select the letter of the menu entry:")

    def _send_config_csv(self) -> None:
        """Send configuration CSV dump (response to '^' command)."""
        # Format from SendAllParameters (lines 1670-1689)
        csv = (
            f"{self.averaging},"
            f"9600,"
            f"{self.calfactor:.6f},"
            f","  # Description
            f"E,"
            f"{self.firmware_version},"
            f"G,H,"
            f"{self.serial_number},"
            f"1.000000,"  # Immersion
            f"0.000000,"  # DarkValue
            f"12.345,"  # SupplyVoltage
            f"{self.operating_mode},"
            f"{self.tag if self.operating_mode == '1' else ''},"
        )
        self._send_line(csv)

    def _send_freerun_data_line(self) -> None:
        """Generate and send one freerun data line."""
        # Simulate sensor reading
        import random

        value = 100.0 + random.uniform(-5, 5)

        line = f"{self.preamble}{value:.6f}"

        if self.include_temp:
            temp = 21.0 + random.uniform(-1, 1)
            line += f", {temp:.2f}"

        if self.include_vin:
            vin = 12.3 + random.uniform(-0.2, 0.2)
            line += f", {vin:.3f}"

        self._send_line(line)

    def _send_polled_data_line(self) -> None:
        """Generate and send one polled data line (prefixed with TAG)."""
        import random

        value = 100.0 + random.uniform(-5, 5)

        line = f"{self.tag},{self.preamble}{value:.6f}"

        if self.include_temp:
            temp = 21.0 + random.uniform(-1, 1)
            line += f", {temp:.2f}"

        if self.include_vin:
            vin = 12.3 + random.uniform(-0.2, 0.2)
            line += f", {vin:.3f}"

        self._send_line(line)

    # ========================================================================
    # Internal: Threading
    # ========================================================================

    def _start_streaming_thread(self) -> None:
        """Start background thread to generate freerun data."""
        self._stop_streaming.clear()
        self._stream_thread = threading.Thread(
            target=self._streaming_loop,
            name="FakeSensorStream",
            daemon=True,
        )
        self._stream_thread.start()
        logger.debug("Started freerun streaming thread")

    def _stop_streaming_thread(self) -> None:
        """Stop streaming thread if running."""
        if self._stream_thread and self._stream_thread.is_alive():
            self._stop_streaming.set()
            self._stream_thread.join(timeout=2.0)
            self._stream_thread = None
            logger.debug("Stopped streaming thread")

    def _streaming_loop(self) -> None:
        """Background loop to send freerun data lines."""
        # Calculate sample period
        period = self.averaging / self.adc_rate_hz

        logger.debug(f"Streaming loop started, period={period:.3f}s")

        while not self._stop_streaming.is_set():
            self._send_freerun_data_line()
            time.sleep(period)

        logger.debug("Streaming loop stopped")
