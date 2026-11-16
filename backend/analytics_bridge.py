"""
Connects the backend to the analytics module in analytics/src.

The backend never judges heart-rate readings itself. It hands them to the
analytics module, which decides which readings are usable, and stores the
results in the database.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "analytics", "src"))

import database  # noqa: E402
from heart_rate_summary import (averages_per_minute, clean_readings,  # noqa: E402,F401
                                summarise_heart_rate)

# Extra fields the bracelet sends with every reading (whole numbers).
EXTRA_FIELDS = database.READING_FIELDS


def _number(x):
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def heart_readings(readings):
    """
    Pick the heart-rate readings out of a list and make them safe to analyse:
    numbers only, in the order they arrived, and of one bracelet (the one that
    sent the most recent reading), so two people's data are never mixed.
    """
    result = []
    for r in readings:
        if not isinstance(r, dict) or r.get("type") != "heart_rate":
            continue
        value, ts = _number(r.get("value")), _number(r.get("ts"))
        if value is None or ts is None:
            continue
        reading = {"type": "heart_rate", "value": value, "ts": ts}
        for field in EXTRA_FIELDS:
            n = _number(r.get(field))
            if n is not None:
                reading[field] = int(n)
        result.append(reading)

    if result:
        device = result[-1].get("device_id")
        result = [r for r in result if r.get("device_id") == device]
    return result


def analyse_latest_bracelet(hours=24, recent_minutes=60):
    """
    Run the analytics on the bracelet that sent the most recent reading:
      1. read its heart-rate readings of the last `hours` from the database
      2. check them and summarise the usable ones
      3. store the results per minute in the database
    Returns the summary and the per-minute results of the last `recent_minutes`.
    """
    device = database.latest_device()
    readings = database.heart_readings(device, hours)
    summary = summarise_heart_rate(readings)
    per_minute = averages_per_minute(readings)
    if per_minute:
        database.save_minutes(device, per_minute)
    summary["device_id"] = device
    return summary, per_minute[-recent_minutes:]
