"""Pure functions for parsing sensor data lines and configuration responses."""

import logging
from typing import Dict, Optional, Tuple

from q_sensor_lib import protocol
from q_sensor_lib.errors import InvalidResponse
from q_sensor_lib.models import SensorConfig

logger = logging.getLogger(__name__)


def parse_freerun_line(line: str) -> Dict[str, float]:
    """Parse a freerun mode data line into numeric fields.

    Expected format: <preamble><value>[, <temp>][, <vin>]
    Example: "$LITE123.456789, 21.34, 12.345"

    Args:
        line: Raw line from device (CRLF should be stripped by caller)

    Returns:
        Dictionary with keys:
            - "value": Main calibrated sensor reading (always present)
            - "TempC": Temperature in Celsius (if present)
            - "Vin": Line voltage in volts (if present)

    Raises:
        InvalidResponse: If line doesn't match expected pattern
    """
    line = line.strip()
    if not line:
        raise InvalidResponse("Empty data line")

    match = protocol.RE_FREERUN_LINE.match(line)
    if not match:
        raise InvalidResponse(f"Freerun line doesn't match expected pattern: {line!r}")

    # Group 1 is preamble (ignored), Group 2 is value, 3=temp, 4=vin
    try:
        data: Dict[str, float] = {"value": float(match.group(2))}

        if match.group(3):  # Temperature present
            data["TempC"] = float(match.group(3))

        if match.group(4):  # Vin present
            data["Vin"] = float(match.group(4))

        return data

    except (ValueError, AttributeError) as e:
        raise InvalidResponse(f"Failed to parse numeric values in line: {line!r}") from e


def parse_polled_line(line: str, expected_tag: str) -> Dict[str, float]:
    """Parse a polled mode data line and validate TAG prefix.

    Expected format: <TAG>,<preamble><value>[, <temp>][, <vin>]
    Example: "A,123.456789, 21.34"

    Args:
        line: Raw line from device (CRLF should be stripped)
        expected_tag: Single uppercase character A-Z to validate against

    Returns:
        Dictionary with same keys as parse_freerun_line

    Raises:
        InvalidResponse: If line doesn't match pattern or TAG doesn't match
    """
    line = line.strip()
    if not line:
        raise InvalidResponse("Empty polled data line")

    match = protocol.RE_POLLED_LINE.match(line)
    if not match:
        raise InvalidResponse(f"Polled line doesn't match expected pattern: {line!r}")

    # Group 1 is TAG, validate it
    tag = match.group(1)
    if tag != expected_tag:
        raise InvalidResponse(
            f"TAG mismatch: expected '{expected_tag}', got '{tag}' in line: {line!r}"
        )

    # Group 2 is preamble (ignored), Group 3=value, 4=temp, 5=vin
    try:
        data: Dict[str, float] = {"value": float(match.group(3))}

        if match.group(4):
            data["TempC"] = float(match.group(4))

        if match.group(5):
            data["Vin"] = float(match.group(5))

        return data

    except (ValueError, AttributeError) as e:
        raise InvalidResponse(f"Failed to parse numeric values in polled line: {line!r}") from e


def parse_config_csv(line: str) -> Tuple[SensorConfig, Dict[str, str]]:
    """Parse configuration CSV dump from '^' command.

    CSV format (from SendAllParameters, lines 1670-1689):
    <adcToAverage>,<baudrate>,<CalFactor>,<Description>,E,<Version>,G,H,<Serial>,...

    Args:
        line: Raw CSV line from device

    Returns:
        Tuple of (SensorConfig, extras_dict)
        extras_dict contains additional fields like "baudrate", "description", etc.

    Raises:
        InvalidResponse: If CSV doesn't match expected pattern
    """
    line = line.strip()
    match = protocol.RE_CONFIG_CSV.match(line)
    if not match:
        raise InvalidResponse(f"Config CSV doesn't match expected pattern: {line!r}")

    try:
        # Extract key fields
        adc_to_average = int(match.group(1))
        baudrate = int(match.group(2))
        cal_factor = float(match.group(3))
        description = match.group(4)
        firmware_version = match.group(5)
        serial_number = match.group(6)
        # immersion = float(match.group(7))  # Not used in SensorConfig yet
        # dark_value = float(match.group(8))  # Not used in SensorConfig yet
        # supply_voltage = float(match.group(9))  # Runtime value, not config
        operating_mode_char = match.group(10)  # "0" = freerun, "1" = polled
        tag_str = match.group(11)

        # Determine mode and tag
        if operating_mode_char == "0":
            mode: str = "freerun"
            tag: Optional[str] = None
        elif operating_mode_char == "1":
            mode = "polled"
            tag = tag_str if tag_str else None
        else:
            raise InvalidResponse(f"Unknown operating mode: {operating_mode_char!r}")

        # Build SensorConfig (we don't have temp/vin flags in CSV, set to False)
        config = SensorConfig(
            averaging=adc_to_average,
            adc_rate_hz=125,  # Default - not in CSV, would need to query separately
            mode=mode,  # type: ignore
            tag=tag,
            include_temp=False,  # Not in CSV
            include_vin=False,  # Not in CSV
            preamble="",  # Available in full CSV but not captured by current regex
            calfactor=cal_factor,
            serial_number=serial_number,
            firmware_version=firmware_version,
        )

        extras = {
            "baudrate": str(baudrate),
            "description": description,
        }

        return config, extras

    except (ValueError, IndexError) as e:
        raise InvalidResponse(f"Failed to parse config CSV values: {line!r}") from e


def extract_version_from_banner(banner_text: str) -> Optional[str]:
    """Extract firmware version from signon banner.

    Args:
        banner_text: Multi-line signon banner text

    Returns:
        Version string (e.g., "4.003") or None if not found
    """
    match = protocol.RE_SIGNON_BANNER.search(banner_text)
    if match:
        return match.group(1)
    return None


def extract_serial_from_banner(banner_text: str) -> Optional[str]:
    """Extract serial number from signon banner.

    Args:
        banner_text: Multi-line signon banner text

    Returns:
        Serial number string or None if not found
    """
    match = protocol.RE_UNIT_ID.search(banner_text)
    if match:
        return match.group(1).strip()
    return None


def extract_mode_from_banner(banner_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract operating mode and TAG from signon banner.

    Args:
        banner_text: Multi-line signon banner text

    Returns:
        Tuple of (mode, tag) where mode is "freerun" or "polled" or None,
        and tag is single char or None
    """
    # Check for freerun
    if protocol.RE_OPERATING_MODE_FREERUN.search(banner_text):
        return "freerun", None

    # Check for polled
    match = protocol.RE_OPERATING_MODE_POLLED.search(banner_text)
    if match:
        tag = match.group(1).upper()
        return "polled", tag

    return None, None
