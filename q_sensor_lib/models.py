"""Data models for Q-Series sensor library."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Literal, Optional


class ConnectionState(Enum):
    """Sensor controller connection states."""

    DISCONNECTED = "disconnected"
    CONFIG_MENU = "config_menu"
    ACQ_FREERUN = "acq_freerun"
    ACQ_POLLED = "acq_polled"
    PAUSED = "paused"


@dataclass
class SensorConfig:
    """Configuration parameters for Q-Series sensor.

    Attributes:
        averaging: Number of ADC readings to average (1-65535).
        adc_rate_hz: ADC sample rate in Hz. Valid: {4, 8, 16, 33, 62, 125, 250, 500}.
        mode: Operating mode - "freerun" for continuous streaming or "polled" for on-demand.
        tag: Single uppercase character A-Z used for polling (required when mode="polled").
        include_temp: Include temperature output in data stream.
        include_vin: Include line voltage (Vin) output in data stream.
        preamble: Optional preamble string prepended to data output.
        calfactor: Calibration factor for sensor readings.
        serial_number: Device serial number (read-only from device).
        firmware_version: Firmware version string (read-only from device).
    """

    averaging: int
    adc_rate_hz: int
    mode: Literal["freerun", "polled"]
    tag: Optional[str] = None
    include_temp: bool = False
    include_vin: bool = False
    preamble: str = ""
    calfactor: float = 1.0
    serial_number: str = ""
    firmware_version: str = ""

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if not (1 <= self.averaging <= 65535):
            raise ValueError(f"averaging must be 1-65535, got {self.averaging}")

        valid_rates = {4, 8, 16, 33, 62, 125, 250, 500}
        if self.adc_rate_hz not in valid_rates:
            raise ValueError(
                f"adc_rate_hz must be one of {valid_rates}, got {self.adc_rate_hz}"
            )

        if self.mode == "polled":
            if not self.tag:
                raise ValueError("tag is required when mode='polled'")
            if not (len(self.tag) == 1 and "A" <= self.tag <= "Z"):
                raise ValueError(f"tag must be single uppercase A-Z, got '{self.tag}'")
        elif self.mode == "freerun":
            # Tag is ignored in freerun mode but we don't enforce it's None
            pass
        else:
            raise ValueError(f"mode must be 'freerun' or 'polled', got '{self.mode}'")

    @property
    def sample_period_s(self) -> float:
        """Calculate the effective sample period in seconds.

        Returns:
            Time in seconds between samples: averaging / adc_rate_hz
        """
        return self.averaging / self.adc_rate_hz


@dataclass
class Reading:
    """A single sensor reading with timestamp and metadata.

    Attributes:
        ts: UTC timestamp when reading was received/parsed.
        sensor_id: Sensor serial number or identifier.
        mode: Operating mode when reading was acquired ("freerun" or "polled").
        data: Dictionary of measured values. Always contains "value" key.
                May contain "TempC" (temperature in Celsius) and "Vin" (line voltage).
    """

    ts: datetime
    sensor_id: str
    mode: Literal["freerun", "polled"]
    data: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate reading data."""
        if "value" not in self.data:
            raise ValueError("Reading data must contain 'value' key")
