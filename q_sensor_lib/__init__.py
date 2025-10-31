"""
q_sensor_lib - Production-quality Python library for Biospherical Q-Series sensors.

Supports firmware version 4.003 in Digital Output, Linear (2150 mode).
"""

from q_sensor_lib.controller import SensorController
from q_sensor_lib.errors import (
    DeviceResetError,
    InvalidConfigValue,
    InvalidResponse,
    MenuTimeout,
    SerialIOError,
)
from q_sensor_lib.models import ConnectionState, Reading, SensorConfig

__version__ = "0.1.0"

__all__ = [
    "SensorController",
    "SensorConfig",
    "Reading",
    "ConnectionState",
    "MenuTimeout",
    "InvalidResponse",
    "InvalidConfigValue",
    "DeviceResetError",
    "SerialIOError",
]
