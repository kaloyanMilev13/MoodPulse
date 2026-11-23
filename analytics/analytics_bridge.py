"""
Connects Flask to the heart-rate analytics module and database.
"""

import math
import sys
from pathlib import Path

ANALYTICS_SRC = Path(__file__).resolve().parent / "analytics" / "src"
if not ANALYTICS_SRC.is_dir():
    ANALYTICS_SRC = Path(__file__).resolve().parent.parent / "analytics" / "src"
if not ANALYTICS_SRC.is_dir():
    raise ModuleNotFoundError(
        f"Analytics source directory not found: {ANALYTICS_SRC}"
    )
sys.path.insert(0, str(ANALYTICS_SRC))

import database  # noqa: E402
from heart_rate_summary import (  # noqa: E402
    clean_readings,
    heart_rate_history,
)

EXTRA_FIELDS = database.READING_FIELDS


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def heart_readings(readings):
    """Select numeric heart-rate readings from the most recently active device."""
    result = []
    for reading in readings:
        if not isinstance(reading, dict) or reading.get("type") != "heart_rate":
            continue
        value = _number(reading.get("value"))
        timestamp = _number(reading.get("ts"))
        if value is None or timestamp is None:
            continue
        clean = {"type": "heart_rate", "value": value, "ts": timestamp}
        for field in EXTRA_FIELDS:
            number = _number(reading.get(field))
            if number is not None:
                clean[field] = int(number)
        result.append(clean)

    if result:
        device = result[-1].get("device_id")
        result = [reading for reading in result if reading.get("device_id") == device]
    return result


HISTORY_RANGES = (1, 15, 60, 1440)


def latest_bracelet_history(user_id, minutes=15):
    """Return recent chart data for the bracelet with the newest sample."""
    device = database.latest_device(user_id)
    readings = database.heart_readings_until_latest(
        user_id,
        device,
        minutes * 60 + 300,
    )
    history = heart_rate_history(readings, minutes)
    history["device_id"] = device
    return history
