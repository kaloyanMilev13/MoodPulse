"""
MoodPulse - heart-rate checks and summary (analytics module, owner: Dobromir)

The bracelet sends one reading per second. This file decides which readings
are usable and turns them into short results:
  - summarise_heart_rate()  one summary (latest, average, range, what was removed)
  - averages_per_minute()   one row per minute, stored in the database
The backend uses these before anything is shown on the website or sent to
the AI.

Try it (from the analytics/ folder):
    python src/heart_rate_summary.py
"""

import struct
from collections import Counter
from itertools import groupby

MIN_BPM = 30       # lower than this is not a real heart rate
MAX_BPM = 220      # higher than this is not a real heart rate
STUCK_AFTER = 30   # the same value this many times in a row = stuck sensor

# The bracelet's 26-byte packet, in the same order as the Payload struct in
# firmware/xiao/xiao.ino.
#   <  no padding, lowest byte first     H  2 bytes
#   I  4 bytes     l  4 bytes (signed)   B  1 byte
PACKET_FORMAT = "<HIIBIllBBB"
PACKET_FIELDS = ("device_id", "sequence", "uptime_ms", "heart_bpm",
                 "heart_lastBeat", "heart_IrValue", "heart_redValue",
                 "batt_lvl", "isTest", "flags")
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)   # 26 bytes

# The website and backend use slightly different names for three fields.
OTHER_NAMES = {"value": "heart_bpm", "is_test": "isTest", "battery": "batt_lvl"}


def decode_packet(data):
    """Turn one raw 26-byte packet into a dictionary with named fields."""
    if len(data) != PACKET_SIZE:
        raise ValueError(f"A packet must be {PACKET_SIZE} bytes, "
                         f"but this one is {len(data)} bytes.")
    return dict(zip(PACKET_FIELDS, struct.unpack(PACKET_FORMAT, data)))


def _as_reading(item):
    """Accept a raw packet, a reading (dictionary) or a plain number."""
    if isinstance(item, (bytes, bytearray)):
        return decode_packet(item)
    if isinstance(item, dict):
        reading = dict(item)
        for other_name, our_name in OTHER_NAMES.items():
            if other_name in reading and our_name not in reading:
                reading[our_name] = reading[other_name]
        return reading
    return {"heart_bpm": item}


def check_readings(items):
    """
    Check the heart-rate readings of ONE bracelet, one by one.

    Returns two things:
      checked - every heart-rate reading with the check result in "check":
                "usable", "duplicate", "sensor_error", "impossible" or "stuck"
      lost    - how many readings the bracelet measured but never arrived
    """
    readings = [_as_reading(item) for item in items]
    readings = [r for r in readings if r.get("type", "heart_rate") == "heart_rate"]

    devices = {r["device_id"] for r in readings if r.get("device_id") is not None}
    if len(devices) > 1:
        raise ValueError("Readings of more than one bracelet were given. "
                         "Check each bracelet separately.")

    seen = set()          # packets we already had (to find duplicates)
    last_sequence = None
    lost = 0

    for r in readings:
        sequence = r.get("sequence")
        if sequence is not None:
            # The counter restarts at 1 when the bracelet restarts, so the
            # uptime is included: only a true resend has both the same.
            packet_id = (sequence, r.get("uptime_ms"))
            if packet_id in seen:
                r["check"] = "duplicate"
                continue
            seen.add(packet_id)
            if last_sequence is not None and sequence > last_sequence + 1:
                lost += sequence - last_sequence - 1
            last_sequence = sequence

        bpm = r.get("heart_bpm")
        if r.get("flags"):
            r["check"] = "sensor_error"
        elif bpm is None or not MIN_BPM <= bpm <= MAX_BPM:
            r["check"] = "impossible"
        else:
            r["check"] = "usable"

    # When the sensor has no new measurement, the firmware keeps sending the
    # previous value. A value that never changes is treated as stuck.
    candidates = [r for r in readings if r["check"] == "usable"]
    for bpm, run in groupby(candidates, key=lambda r: r["heart_bpm"]):
        run = list(run)
        if len(run) >= STUCK_AFTER:
            for r in run:
                r["check"] = "stuck"

    return readings, lost


def clean_readings(items):
    """
    Check the readings of ONE bracelet and keep only the usable ones.

    Returns two things:
      usable - the readings that passed every check, in the order received
      report - what was removed and why, how many readings never arrived,
               whether it is test data, and the last battery level
    """
    checked, lost = check_readings(items)
    removed = Counter(r["check"] for r in checked if r["check"] != "usable")
    report = {
        "removed": sum(removed.values()),
        "removed_why": dict(removed),
        "lost": lost,
        "test_data": any(r.get("isTest") for r in checked),
        "battery": checked[-1].get("batt_lvl") if checked else None,
    }
    return [r for r in checked if r["check"] == "usable"], report


def summarise_heart_rate(items):
    """Check the readings of ONE bracelet and summarise the usable ones."""
    usable, report = clean_readings(items)
    values = [r["heart_bpm"] for r in usable]

    summary = {
        "enough_data": len(values) > 0,
        "used": len(values),
        "removed": report["removed"],
        "removed_why": report["removed_why"],
        "lost": report["lost"],
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


def averages_per_minute(items):
    """
    Check the readings of ONE bracelet and summarise them per minute.

    Every reading needs "ts": the time it was received, in seconds since
    1970 (UTC). Returns one row per minute, oldest first:
      minute            start of the minute, in seconds since 1970
      average, min, max of the usable heart rates (None if none were usable)
      used              readings that passed the checks
      removed           readings that were left out
    """
    checked, _ = check_readings(items)

    minutes = {}
    for r in checked:
        if r.get("ts") is None:
            continue
        minute = int(r["ts"] // 60) * 60
        row = minutes.setdefault(minute, {"values": [], "removed": 0})
        if r["check"] == "usable":
            row["values"].append(r["heart_bpm"])
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


if __name__ == "__main__":
    # Five packets like the bracelet sends in its simulation mode
    # (heart rate counting up from 41), then one resend and one sensor error.
    def packet(sequence, bpm, flags=0):
        return struct.pack(PACKET_FORMAT, 4660, sequence, sequence * 1000,
                           bpm, 0, 0, 0, 87, 1, flags)

    packets = [packet(n, 40 + n) for n in range(1, 6)]
    packets.append(packets[-1])               # the same packet twice
    packets.append(packet(6, 46, flags=2))    # sensor error

    print("First packet decoded:")
    print(" ", decode_packet(packets[0]))
    print("Summary of", len(packets), "packets:")
    print(" ", summarise_heart_rate(packets))
