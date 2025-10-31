"""Wire protocol constants and patterns for Q-Series sensor firmware 4.003.

This module defines exact byte sequences, prompts, and timing based on the
reverse-engineered serial protocol from 2150REV4.003.bas.
"""

import re
from typing import Final

# ============================================================================
# Line Termination
# ============================================================================

# Device expects CR (0x0D) as input terminator
INPUT_TERMINATOR: Final[bytes] = b"\r"

# Device sends CRLF (0x0D 0x0A) for all output line endings
OUTPUT_TERMINATOR: Final[bytes] = b"\r\n"

# ============================================================================
# Control Characters
# ============================================================================

ESC: Final[bytes] = b"\x1b"  # Enter menu from any acquisition mode
QUESTION_MARK: Final[bytes] = b"?"  # Alternative menu entry character

# ============================================================================
# Menu Commands (single character + CR)
# ============================================================================

MENU_CMD_AVERAGING: Final[str] = "A"  # Set averaging
MENU_CMD_RATE: Final[str] = "R"  # Set ADC sample rate
MENU_CMD_MODE: Final[str] = "M"  # Set operating mode (freerun/polled)
MENU_CMD_CONFIG_DUMP: Final[str] = "^"  # Get configuration CSV dump
MENU_CMD_EXIT: Final[str] = "X"  # Exit menu (triggers device reset)
MENU_CMD_OUTPUTS: Final[str] = "O"  # Configure temp/voltage outputs
MENU_CMD_QUIET: Final[str] = "Q"  # Set quiet mode (suppress banner)
MENU_CMD_REDISPLAY: Final[str] = "?"  # Redisplay menu

# ============================================================================
# Polled Mode Commands
# ============================================================================

# Initialize polled averaging: *<TAG>Q000!
# Must be sent once after entering polled mode before querying
POLLED_INIT_PREFIX: Final[str] = "*"
POLLED_INIT_CMD: Final[str] = "Q"
POLLED_INIT_PADDING: Final[str] = "000"
POLLED_INIT_TERM: Final[str] = "!"


def make_polled_init_cmd(tag: str) -> str:
    """Build polled mode initialization command: *<TAG>Q000!

    Args:
        tag: Single uppercase character A-Z

    Returns:
        Command string (no CR appended - transport layer handles)
    """
    return f"{POLLED_INIT_PREFIX}{tag}{POLLED_INIT_CMD}{POLLED_INIT_PADDING}{POLLED_INIT_TERM}"


# Query single sample in polled mode: ><TAG>
POLLED_QUERY_PREFIX: Final[str] = ">"


def make_polled_query_cmd(tag: str) -> str:
    """Build polled mode query command: ><TAG>

    Args:
        tag: Single uppercase character A-Z

    Returns:
        Command string (no CR appended)
    """
    return f"{POLLED_QUERY_PREFIX}{tag}"


# ============================================================================
# Timing Constants (seconds)
# ============================================================================

# Delay before menu displays after entry (DelayMS 1000 at line 1092)
MENU_DISPLAY_DELAY: Final[float] = 1.0

# Time to wait for device banner after power-on/reset (QuietMode=0)
BANNER_SETTLE_TIME: Final[float] = 2.5

# Time to wait for device banner after reset (QuietMode=1, minimal output)
BANNER_SETTLE_TIME_QUIET: Final[float] = 0.5

# Timeout for menu command responses (M command has 20s timeout, A/R block indefinitely)
MENU_RESPONSE_TIMEOUT: Final[float] = 25.0

# Timeout for single data line in acquisition mode
DATA_LINE_TIMEOUT: Final[float] = 5.0

# Delay after invalid averaging input (line 1118)
ERROR_DELAY_AVERAGING: Final[float] = 4.0

# Delay after invalid rate input (line 1458-1459)
ERROR_DELAY_RATE: Final[float] = 5.0

# Delay after ADC config failure (line 1465-1466)
ERROR_DELAY_ADC_CONFIG: Final[float] = 3.0

# ============================================================================
# Regular Expressions for Prompt/Response Matching
# ============================================================================

# Menu entry prompt (appears after ESC/? and after each menu command)
RE_MENU_PROMPT: Final[re.Pattern[str]] = re.compile(
    r"Select the letter of the menu entry:", re.IGNORECASE
)

# Signon banner contains version
RE_SIGNON_BANNER: Final[re.Pattern[str]] = re.compile(
    r"Biospherical Instruments Inc.*Digital.*Engine.*Vers\s+([\d.]+)", re.IGNORECASE
)

# Serial number in banner: "Unit ID <serial>"
RE_UNIT_ID: Final[re.Pattern[str]] = re.compile(r"Unit ID\s+(.+)", re.IGNORECASE)

# Operating mode in banner
RE_OPERATING_MODE_FREERUN: Final[re.Pattern[str]] = re.compile(
    r"Operating in free run mode", re.IGNORECASE
)
RE_OPERATING_MODE_POLLED: Final[re.Pattern[str]] = re.compile(
    r"Operating in polled mode with tag of\s+(\w)", re.IGNORECASE
)

# Averaging set confirmation
RE_AVERAGING_SET: Final[re.Pattern[str]] = re.compile(
    r"ADC set to averaging\s+(\d+)", re.IGNORECASE
)

# Rate set confirmation
RE_RATE_SET: Final[re.Pattern[str]] = re.compile(
    r"ADC rate set to\s+(\d+)", re.IGNORECASE
)

# Averaging prompt
RE_AVERAGING_PROMPT: Final[re.Pattern[str]] = re.compile(
    r"Enter # readings to average before update.*:", re.IGNORECASE
)

# Rate prompt
RE_RATE_PROMPT: Final[re.Pattern[str]] = re.compile(
    r"Enter ADC rate.*:", re.IGNORECASE | re.DOTALL
)

# Mode prompt
RE_MODE_PROMPT: Final[re.Pattern[str]] = re.compile(
    r"Enter the operating mode number:", re.IGNORECASE
)

# Tag prompt (appears after selecting mode=1)
RE_TAG_PROMPT: Final[re.Pattern[str]] = re.compile(
    r"Enter the single character.*tag.*polling", re.IGNORECASE | re.DOTALL
)

# Exit/reboot message
RE_REBOOTING: Final[re.Pattern[str]] = re.compile(r"Rebooting program", re.IGNORECASE)

# Error messages
RE_ERROR_INVALID_AVERAGING: Final[re.Pattern[str]] = re.compile(
    r"Invalid number.*averaging set to 12", re.IGNORECASE
)
RE_ERROR_INVALID_RATE: Final[re.Pattern[str]] = re.compile(
    r"Invalid rate.*Command is ignored", re.IGNORECASE
)
RE_ERROR_BAD_TAG: Final[re.Pattern[str]] = re.compile(r"Bad TAG", re.IGNORECASE)
RE_ERROR_MODE_CONFUSED: Final[re.Pattern[str]] = re.compile(r"I am confused", re.IGNORECASE)

# ============================================================================
# Data Line Patterns
# ============================================================================

# Freerun data line: <preamble><value>[, <temp>][, <vin>] CRLF
# Example: "$LITE123.456789, 21.34, 12.345"
# Preamble is optional, may be empty
# Temperature and Vin are optional (if enabled in config)
RE_FREERUN_LINE: Final[re.Pattern[str]] = re.compile(
    r"^([^\d\-]*?)"  # Group 1: optional preamble (non-numeric prefix)
    r"([\-\d.]+)"  # Group 2: value (may be negative)
    r"(?:,\s*([\-\d.]+))?"  # Group 3: optional temp
    r"(?:,\s*([\-\d.]+))?"  # Group 4: optional vin
    r"\s*$"
)

# Polled data line: <TAG>,<preamble><value>[, <temp>][, <vin>] CRLF
# Example: "A,123.456789, 21.34"
RE_POLLED_LINE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Z]),"  # Group 1: TAG
    r"([^\d\-]*?)"  # Group 2: optional preamble
    r"([\-\d.]+)"  # Group 3: value
    r"(?:,\s*([\-\d.]+))?"  # Group 4: optional temp
    r"(?:,\s*([\-\d.]+))?"  # Group 5: optional vin
    r"\s*$"
)

# Configuration CSV dump from "^" command
# Format (line 1670-1689):
# <adcToAverage>,<baudrate>,<CalFactor>,<Description>,E,<Version>,G,H,<Serial>,...
RE_CONFIG_CSV: Final[re.Pattern[str]] = re.compile(
    r"^(\d+),"  # Group 1: adcToAverage
    r"(\d+),"  # Group 2: baudrate
    r"([\d.]+),"  # Group 3: CalFactor
    r"([^,]*),"  # Group 4: Description
    r"E,"  # Literal 'E'
    r"([\d.]+),"  # Group 5: Version
    r"G,H,"  # Literals 'G', 'H'
    r"([^,]*),"  # Group 6: Serial
    r"([\d.]+),"  # Group 7: Immersion
    r"([\d.]+),"  # Group 8: DarkValue
    r"([\d.]+),"  # Group 9: SupplyVoltage
    r"([^,]+),"  # Group 10: OperatingMode ("0" or "1")
    r"([^,]*),"  # Group 11: sTAG
)

# ============================================================================
# Valid Configuration Values
# ============================================================================

VALID_ADC_RATES: Final[frozenset[int]] = frozenset({4, 8, 16, 33, 62, 125, 250, 500})

AVERAGING_MIN: Final[int] = 1
AVERAGING_MAX: Final[int] = 65535

VALID_TAGS: Final[str] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
