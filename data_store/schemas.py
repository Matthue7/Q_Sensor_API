"""Schema normalization for Q-Sensor readings to DataFrame format.

This module ensures consistent DataFrame structure across freerun and polled modes,
handling optional fields (TempC, Vin) that may be absent depending on sensor configuration.
"""

from datetime import datetime, timezone
from typing import Dict

from q_sensor_lib.models import Reading

# DataFrame schema: column names and their dtypes
# All columns are always present; missing optional fields are filled with None (becomes NaN in DataFrame)
SCHEMA = {
    "timestamp": str,  # UTC ISO 8601 format
    "sensor_id": str,  # Sensor serial number
    "mode": str,  # "freerun" or "polled"
    "value": float,  # Main calibrated sensor reading (always present)
    "TempC": float,  # Optional temperature in Celsius
    "Vin": float,  # Optional line voltage
}


def reading_to_row(reading: Reading) -> Dict[str, any]:
    """Convert a Reading instance to a DataFrame row dictionary.

    Normalizes timestamps to UTC ISO 8601 and ensures all SCHEMA keys are present.
    Missing optional fields (TempC, Vin) are filled with None, which become NaN in pandas.

    Args:
        reading: A Reading instance from SensorController buffer

    Returns:
        Dictionary with all SCHEMA keys, ready for DataFrame append

    Raises:
        ValueError: If reading.data is missing the required "value" key
    """
    if "value" not in reading.data:
        raise ValueError(f"Reading missing required 'value' field: {reading}")

    # Convert naive datetime to UTC if needed
    ts = reading.ts
    if ts.tzinfo is None:
        # Assume naive timestamps are UTC (controller uses datetime.now(timezone.utc))
        ts = ts.replace(tzinfo=timezone.utc)
    elif ts.tzinfo != timezone.utc:
        # Convert to UTC if in different timezone
        ts = ts.astimezone(timezone.utc)

    # Build row with all schema keys
    row = {
        "timestamp": ts.isoformat(),
        "sensor_id": reading.sensor_id,
        "mode": reading.mode,
        "value": reading.data["value"],
        "TempC": reading.data.get("TempC"),  # None if absent
        "Vin": reading.data.get("Vin"),  # None if absent
    }

    return row
