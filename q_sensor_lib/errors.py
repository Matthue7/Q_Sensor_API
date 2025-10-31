"""Custom exceptions for Q-Series sensor library."""


class QSensorError(Exception):
    """Base exception for all Q-Sensor library errors."""

    pass


class MenuTimeout(QSensorError):
    """Raised when a menu operation times out waiting for expected prompt."""

    pass


class InvalidResponse(QSensorError):
    """Raised when device sends unexpected or malformed response."""

    pass


class InvalidConfigValue(QSensorError):
    """Raised when attempting to set an invalid configuration parameter."""

    pass


class DeviceResetError(QSensorError):
    """Raised when device reset behavior is unexpected or fails."""

    pass


class SerialIOError(QSensorError):
    """Raised when serial communication fails (port closed, timeout, etc)."""

    pass
