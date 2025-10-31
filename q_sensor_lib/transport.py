"""Serial transport layer for Q-Series sensor communication."""

import logging
from typing import Optional, Protocol

from q_sensor_lib import protocol
from q_sensor_lib.errors import SerialIOError

logger = logging.getLogger(__name__)


class SerialLike(Protocol):
    """Protocol for serial port interface (allows test doubles)."""

    def write(self, data: bytes) -> int:
        """Write bytes to serial port."""
        ...

    def read(self, size: int = 1) -> bytes:
        """Read up to size bytes from serial port."""
        ...

    def readline(self) -> bytes:
        """Read a line from serial port."""
        ...

    def flush(self) -> None:
        """Flush output buffer (force transmission)."""
        ...

    def reset_input_buffer(self) -> None:
        """Flush input buffer."""
        ...

    def close(self) -> None:
        """Close serial port."""
        ...

    @property
    def is_open(self) -> bool:
        """Check if port is open."""
        ...


class Transport:
    """Wrapper around pyserial with protocol-specific helpers.

    Handles line termination, command formatting, and timeouts per
    the Q-Series protocol specification.
    """

    def __init__(self, serial_port: SerialLike) -> None:
        """Initialize transport with a serial port instance.

        Args:
            serial_port: Object implementing SerialLike protocol
                        (e.g., serial.Serial or FakeSerial for testing)
        """
        self._port = serial_port

    @classmethod
    def open(
        cls, port: str, baud: int = 9600, timeout_s: float = 0.2
    ) -> "Transport":
        """Open a real serial port (requires pyserial).

        Args:
            port: Serial port device name (e.g., "/dev/ttyUSB0")
            baud: Baud rate. Default 9600 matches typical Q-Series config.
            timeout_s: Read timeout in seconds. Default 0.2s for responsive UI.

        Returns:
            Transport instance wrapping opened serial port

        Raises:
            SerialIOError: If port cannot be opened
        """
        try:
            import serial  # type: ignore
        except ImportError as e:
            raise SerialIOError("pyserial not installed. Run: pip install pyserial") from e

        try:
            ser = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=timeout_s,
                rtscts=False,
                dsrdtr=False,
                xonxoff=False
            )
            logger.info(f"Opened serial port {port} at {baud} baud, timeout={timeout_s}s")
            return cls(ser)
        except Exception as e:
            raise SerialIOError(f"Failed to open {port} at {baud} baud: {e}") from e

    def close(self) -> None:
        """Close the serial port."""
        if self._port.is_open:
            self._port.close()
            logger.info("Closed serial port")

    @property
    def is_open(self) -> bool:
        """Check if port is currently open."""
        return self._port.is_open

    def write_bytes(self, data: bytes) -> None:
        """Write raw bytes to port (no automatic termination).

        Args:
            data: Raw bytes to send

        Raises:
            SerialIOError: If write fails
        """
        if not self._port.is_open:
            raise SerialIOError("Serial port is not open")

        try:
            sent = self._port.write(data)
            self._port.flush()  # Force immediate transmission
            logger.debug(f"Sent {sent} bytes: {data!r}")
        except Exception as e:
            raise SerialIOError(f"Failed to write to port: {e}") from e

    def write_cmd(self, text: str) -> None:
        """Write a text command with CR terminator (per protocol spec).

        The device expects all commands terminated with CR (0x0D).

        Args:
            text: Command string (e.g., "A", "125", "X")

        Raises:
            SerialIOError: If write fails
        """
        data = text.encode("ascii") + protocol.INPUT_TERMINATOR
        self.write_bytes(data)

    def readline(self, timeout: Optional[float] = None) -> Optional[str]:
        """Read one line from device, expecting CRLF termination.

        Device sends all output lines terminated with CRLF (0x0D 0x0A).

        Args:
            timeout: Optional override for read timeout (unused in pyserial,
                    timeout is set at port open time)

        Returns:
            Line as string with CRLF stripped, or None on timeout/no data

        Raises:
            SerialIOError: If port is closed or read fails
        """
        if not self._port.is_open:
            raise SerialIOError("Serial port is not open")

        try:
            line_bytes = self._port.readline()
            if not line_bytes:
                return None

            # Device sends CRLF; decode and strip
            line = line_bytes.decode("ascii", errors="replace").rstrip("\r\n")
            logger.debug(f"Received line: {line!r}")
            return line

        except Exception as e:
            raise SerialIOError(f"Failed to read line: {e}") from e

    def read_lines(self, count: int, timeout_per_line: float = 1.0) -> list[str]:
        """Read multiple lines, returning as soon as count is reached or timeout.

        Args:
            count: Number of lines to read
            timeout_per_line: Timeout for each line

        Returns:
            List of lines (may be shorter than count if timeout occurs)

        Raises:
            SerialIOError: If port is closed or read fails
        """
        lines = []
        for _ in range(count):
            line = self.readline(timeout=timeout_per_line)
            if line is None:
                break
            lines.append(line)
        return lines

    def flush_input(self) -> None:
        """Discard all pending input from device.

        Useful after device reset to clear power-on banner.

        Raises:
            SerialIOError: If port is closed
        """
        if not self._port.is_open:
            raise SerialIOError("Serial port is not open")

        try:
            self._port.reset_input_buffer()
            logger.debug("Flushed input buffer")
        except Exception as e:
            raise SerialIOError(f"Failed to flush input: {e}") from e
