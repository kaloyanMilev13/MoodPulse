"""
MoodPulse heart-rate checks and summary.

The analytics module decides which bracelet readings are usable and turns
them into summaries and per-minute results.
"""

import struct
from collections import Counter
from itertools import groupby

MIN_BPM = 30
MAX_BPM = 220
STUCK_AFTER = 30
LATE_AFTER = 5

PACKET_FORMAT = "<HIIBIllBB"
PACKET_FIELDS = (
    "device_id",
    "sequence",
    "uptime_ms",
    "heart_bpm",
    "heart_lastBeat",
    "heart_IrValue",
    "heart_redValue",
    "batt_lvl",
    "flags",
)
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)

FLAG_BLE_ERROR = 0x01
FLAG_SENSOR_ERROR = 0x02
FLAG_BUFFER_OVERFLOW = 0x04
FLAG_LOW_BATTERY = 0x08
FLAG_POOR_WRIST_CONTACT = 0x10
FLAG_OFFLINE_BUFFERING = 0x20
FLAG_SIMULATION_MODE = 0x40
FLAG_BATTERY_CHARGING = 0x80

UNUSABLE_FLAGS = {
    FLAG_SENSOR_ERROR: "sensor_error",
    FLAG_POOR_WRIST_CONTACT: "poor_contact",
    FLAG_BLE_ERROR: "ble_error",
}

OTHER_NAMES = {
    "value": "heart_bpm",
    "is_test": "isTest",
    "battery": "batt_lvl",
}


def decode_packet(data):
    """Decode one raw 25-byte bracelet packet."""
    if len(data) != PACKET_SIZE:
        raise ValueError(
            f"A packet must be {PACKET_SIZE} bytes, but this one is {len(data)} bytes."
        )
    return dict(zip(PACKET_FIELDS, struct.unpack(PACKET_FORMAT, data)))


def _as_reading(item):
    """Accept a raw packet, a reading dictionary, or a plain heart-rate value."""
    if isinstance(item, (bytes, bytearray)):
        reading = decode_packet(item)
    elif isinstance(item, dict):
        reading = dict(item)
        for other_name, our_name in OTHER_NAMES.items():
            if other_name in reading and our_name not in reading:
                reading[our_name] = reading[other_name]
    else:
        reading = {"heart_bpm": item}

    flags = reading.get("flags") or 0
    if flags & FLAG_SIMULATION_MODE:
        reading["isTest"] = 1
    return reading


def _unusable_reason(flags):
    """Return why a reading is unusable, or None if its flags are acceptable."""
    for flag, reason in UNUSABLE_FLAGS.items():
        if flags & flag:
            return reason
    return None


def estimate_measured_times(readings):
    """Estimate measurement times for delayed readings using bracelet uptime."""
    run, max_uptime, max_sequence = [], None, None

    def finish(current_run):
        if not current_run:
            return
        switched_on = min(
            reading["ts"] - reading["uptime_ms"] / 1000
            for reading in current_run
        )
        for reading in current_run:
            reading["measured_ts"] = (
                switched_on + reading["uptime_ms"] / 1000
            )
            reading["late"] = (
                reading["ts"] - reading["measured_ts"] > LATE_AFTER
                or bool(reading.get("buffered"))
            )

    for reading in readings:
        if reading.get("ts") is None or reading.get("uptime_ms") is None:
            if reading.get("ts") is not None:
                reading["measured_ts"] = reading["ts"]
                reading["late"] = bool(reading.get("buffered"))
            continue

        sequence = reading.get("sequence")
        restarted = (
            max_uptime is not None
            and reading["uptime_ms"] < max_uptime - 60_000
        ) or (
            sequence is not None
            and max_sequence is not None
            and sequence < max_sequence - 60
        )
        if restarted:
            finish(run)
            run, max_uptime, max_sequence = [], None, None

        run.append(reading)
        max_uptime = max(max_uptime or 0, reading["uptime_ms"])
        if sequence is not None:
            max_sequence = max(max_sequence or 0, sequence)

    finish(run)
    return readings


def check_readings(items):
    """Check readings from one bracelet; return checked readings and lost count."""
    readings = [_as_reading(item) for item in items]
    readings = [
        reading
        for reading in readings
        if reading.get("type", "heart_rate") == "heart_rate"
    ]

    devices = {
        reading["device_id"]
        for reading in readings
        if reading.get("device_id") is not None
    }
    if len(devices) > 1:
        raise ValueError(
            "Readings of more than one bracelet were given. "
            "Check each bracelet separately."
        )

    seen = set()
    runs = []

    for reading in readings:
        sequence = reading.get("sequence")
        if sequence is not None:
            packet_id = (sequence, reading.get("uptime_ms"))
            if packet_id in seen:
                reading["check"] = "duplicate"
                continue
            seen.add(packet_id)

            if not runs or sequence < max(runs[-1]) - 60:
                runs.append(set())
            runs[-1].add(sequence)

        bpm = reading.get("heart_bpm")
        reason = _unusable_reason(reading.get("flags") or 0)
        if reason:
            reading["check"] = reason
        elif bpm is None or not MIN_BPM <= bpm <= MAX_BPM:
            reading["check"] = "impossible"
        else:
            reading["check"] = "usable"

    candidates = [
        reading for reading in readings if reading["check"] == "usable"
    ]
    for bpm, run in groupby(candidates, key=lambda reading: reading["heart_bpm"]):
        run = list(run)
        if len(run) >= STUCK_AFTER:
            for reading in run:
                reading["check"] = "stuck"

    lost = sum(max(run) - min(run) + 1 - len(run) for run in runs)
    estimate_measured_times(readings)
    return readings, lost


def _clean_checked(checked, lost):
    """Return usable readings and a report from already checked readings."""
    removed = Counter(
        reading["check"]
        for reading in checked
        if reading["check"] != "usable"
    )
    report = {
        "removed": sum(removed.values()),
        "removed_why": dict(removed),
        "lost": lost,
        "late": sum(
            1
            for reading in checked
            if reading["check"] == "usable" and reading.get("late")
        ),
        "test_data": any(reading.get("isTest") for reading in checked),
        "battery": checked[-1].get("batt_lvl") if checked else None,
    }
    return [
        reading for reading in checked if reading["check"] == "usable"
    ], report


def clean_readings(items):
    """Return usable readings and a report of removed, lost, and late data."""
    checked, lost = check_readings(items)
    return _clean_checked(checked, lost)


def _summarise_checked(checked, lost):
    usable, report = _clean_checked(checked, lost)
    values = [reading["heart_bpm"] for reading in usable]

    summary = {
        "enough_data": len(values) > 0,
        "used": len(values),
        "removed": report["removed"],
        "removed_why": report["removed_why"],
        "lost": report["lost"],
        "late": report["late"],
        "test_data": report["test_data"],
    }
    if values:
        summary["latest"] = values[-1]
        summary["average"] = round(sum(values) / len(values))
        summary["min"] = min(values)
        summary["max"] = max(values)
    if report["battery"] is not None:
        summary["battery"] = report["battery"]
    return summary


def summarise_heart_rate(items):
    """Check readings and summarise the usable heart rates."""
    checked, lost = check_readings(items)
    return _summarise_checked(checked, lost)


def _averages_from_checked(checked):
    minutes = {}
    for reading in checked:
        if reading.get("measured_ts") is None:
            continue
        minute = int(reading["measured_ts"] // 60) * 60
        row = minutes.setdefault(minute, {"values": [], "removed": 0})
        if reading["check"] == "usable":
            row["values"].append(reading["heart_bpm"])
        else:
            row["removed"] += 1

    result = []
    for minute in sorted(minutes):
        values = minutes[minute]["values"]
        result.append({
            "minute": minute,
            "average": round(sum(values) / len(values), 1) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "used": len(values),
            "removed": minutes[minute]["removed"],
        })
    return result


def averages_per_minute(items):
    """Summarise usable readings per minute of their estimated measurement time."""
    checked, _ = check_readings(items)
    return _averages_from_checked(checked)


def heart_rate_history(items, minutes=15):
    """Return a summary and chart data for the latest measurement-time window."""
    checked, _ = check_readings(items)
    times = [
        reading["measured_ts"]
        for reading in checked
        if reading.get("measured_ts") is not None
    ]
    if not times:
        return {
            "minutes": minutes,
            "start": None,
            "end": None,
            "summary": _summarise_checked([], 0),
            "per_minute": [],
            "readings": [],
        }

    end = max(times)
    start = end - minutes * 60
    window = [
        reading
        for reading in checked
        if reading.get("measured_ts") is not None
        and reading["measured_ts"] > start
    ]
    window_checked, lost = check_readings(window)

    history = {
        "minutes": minutes,
        "start": start,
        "end": end,
        "summary": _summarise_checked(window_checked, lost),
        "per_minute": _averages_from_checked(window_checked),
        "readings": [],
    }
    if minutes <= 60:
        history["readings"] = [
            {
                "t": round(reading["measured_ts"], 3),
                "bpm": reading.get("heart_bpm"),
                "check": reading["check"],
            }
            for reading in window_checked
            if reading.get("measured_ts") is not None
        ]
    return history
