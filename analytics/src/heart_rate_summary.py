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
LATE_AFTER = 5     # a reading that arrives this many seconds late was buffered

# The bracelet's 25-byte packet, in the same order as the Payload struct in
# firmware/xiao/xiao.ino (firmware/buffering branch, 18 November).
#   <  no padding, lowest byte first     H  2 bytes
#   I  4 bytes     l  4 bytes (signed)   B  1 byte
PACKET_FORMAT = "<HIIBIllBB"
PACKET_FIELDS = ("device_id", "sequence", "uptime_ms", "heart_bpm",
                 "heart_lastBeat", "heart_IrValue", "heart_redValue",
                 "batt_lvl", "flags")
PACKET_SIZE = struct.calcsize(PACKET_FORMAT)   # 25 bytes

# The flags byte: 8 switches, one per bit (same values as in the firmware).
FLAG_BLE_ERROR = 0x01           # Bluetooth did not start
FLAG_SENSOR_ERROR = 0x02        # the heart-rate sensor did not start
FLAG_BUFFER_OVERFLOW = 0x04     # the bracelet's buffer was full: readings were dropped
FLAG_LOW_BATTERY = 0x08         # battery below 20%
FLAG_POOR_WRIST_CONTACT = 0x10  # the sensor does not touch the skin well
FLAG_OFFLINE_BUFFERING = 0x20   # the reading went through the bracelet's buffer
FLAG_SIMULATION_MODE = 0x40     # the heart rate is simulated, not measured
FLAG_BATTERY_CHARGING = 0x80    # the bracelet is plugged in

# Flags that make the heart rate itself unusable. The other flags only
# describe the bracelet (battery, charging, buffering) and do not.
UNUSABLE_FLAGS = {FLAG_SENSOR_ERROR: "sensor_error",
                  FLAG_POOR_WRIST_CONTACT: "poor_contact",
                  FLAG_BLE_ERROR: "ble_error"}

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
        reading = decode_packet(item)
    elif isinstance(item, dict):
        reading = dict(item)
        for other_name, our_name in OTHER_NAMES.items():
            if other_name in reading and our_name not in reading:
                reading[our_name] = reading[other_name]
    else:
        reading = {"heart_bpm": item}
    # Simulated readings: the firmware's simulation switch, or the older
    # separate test field.
    flags = reading.get("flags") or 0
    if flags & FLAG_SIMULATION_MODE:
        reading["isTest"] = 1
    return reading


def _unusable_reason(flags):
    """Why the flags make a reading unusable, or None if they do not."""
    for flag, reason in UNUSABLE_FLAGS.items():
        if flags & flag:
            return reason
    return None


def estimate_measured_times(readings):
    """
    Interruption recovery: work out WHEN each reading was really measured.

    "ts" is the time a reading ARRIVED. Normally that is about the moment it
    was measured. But when the Bluetooth connection drops, the bracelet keeps
    its readings in its buffer and sends them all at once after reconnecting,
    so they arrive late, squeezed together.

    The bracelet also sends "uptime_ms": how long it has been switched on when
    it measured. For one run of the bracelet:
        moment it was switched on = arrival time - uptime
    For readings that arrived on time this gives the real switch-on moment;
    for late readings it gives a later moment. So the EARLIEST result is the
    real switch-on moment, and every reading's measurement time is:
        measured_ts = switch-on moment + uptime

    The website may already have corrected "ts" this way for a batch of
    buffered readings; it then marks them with "buffered". Doing it again here
    changes nothing for those, and still catches late readings it missed.

    Adds to every reading:
      measured_ts - when it was measured, in seconds since 1970 (UTC)
      late        - True if it arrived more than LATE_AFTER seconds late, or
                    the website marked it as buffered
    """
    run, max_uptime, max_sequence = [], None, None

    def finish(run):
        if not run:
            return
        switched_on = min(r["ts"] - r["uptime_ms"] / 1000 for r in run)
        for r in run:
            r["measured_ts"] = switched_on + r["uptime_ms"] / 1000
            r["late"] = r["ts"] - r["measured_ts"] > LATE_AFTER or bool(r.get("buffered"))

    for r in readings:
        if r.get("ts") is None or r.get("uptime_ms") is None:
            if r.get("ts") is not None:
                r["measured_ts"], r["late"] = r["ts"], bool(r.get("buffered"))
            continue
        # A much smaller uptime or reading number than before means the
        # bracelet restarted: start a new run (late readings are only a few
        # seconds / numbers smaller).
        sequence = r.get("sequence")
        restarted = (max_uptime is not None and r["uptime_ms"] < max_uptime - 60_000) or \
                    (sequence is not None and max_sequence is not None and sequence < max_sequence - 60)
        if restarted:
            finish(run)
            run, max_uptime, max_sequence = [], None, None
        run.append(r)
        max_uptime = max(max_uptime or 0, r["uptime_ms"])
        if sequence is not None:
            max_sequence = max(max_sequence or 0, sequence)
    finish(run)
    return readings


def check_readings(items):
    """
    Check the heart-rate readings of ONE bracelet, one by one.

    Returns two things:
      checked - every heart-rate reading with the check result in "check":
                "usable", "duplicate", "sensor_error", "poor_contact",
                "impossible" or "stuck"
      lost    - how many readings the bracelet measured but never arrived
    """
    readings = [_as_reading(item) for item in items]
    readings = [r for r in readings if r.get("type", "heart_rate") == "heart_rate"]

    devices = {r["device_id"] for r in readings if r.get("device_id") is not None}
    if len(devices) > 1:
        raise ValueError("Readings of more than one bracelet were given. "
                         "Check each bracelet separately.")

    seen = set()          # packets we already had (to find duplicates)
    runs = []             # the counter numbers received, per run of the bracelet

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
            # A counter far below the highest one so far means a restart.
            if not runs or sequence < max(runs[-1]) - 60:
                runs.append(set())
            runs[-1].add(sequence)

        bpm = r.get("heart_bpm")
        reason = _unusable_reason(r.get("flags") or 0)
        if reason:
            r["check"] = reason
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

    # Lost readings: counter numbers that are missing between the lowest and
    # the highest one received (works even when readings arrive out of order).
    lost = sum(max(run) - min(run) + 1 - len(run) for run in runs)

    estimate_measured_times(readings)
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
        "late": sum(1 for r in checked if r["check"] == "usable" and r.get("late")),
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


def averages_per_minute(items):
    """
    Check the readings of ONE bracelet and summarise them per minute.

    Every reading needs "ts": the time it was received, in seconds since
    1970 (UTC). Readings are put in the minute they were MEASURED in (see
    estimate_measured_times), so readings that arrived late after a
    connection drop still count for the right minute.
    Returns one row per minute, oldest first:
      minute            start of the minute, in seconds since 1970
      average, min, max of the usable heart rates (None if none were usable)
      used              readings that passed the checks
      removed           readings that were left out
    """
    checked, _ = check_readings(items)

    minutes = {}
    for r in checked:
        if r.get("measured_ts") is None:
            continue
        minute = int(r["measured_ts"] // 60) * 60
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


def heart_rate_history(items, minutes=15):
    """
    Everything the heart-rate charts need, for the last `minutes` minutes of
    data of ONE bracelet (counted back from its latest reading, so older test
    data can still be shown).

    Returns a dictionary:
      minutes     the length of the window
      start, end  the window, in seconds since 1970 (by measurement time)
      summary     the same summary as summarise_heart_rate, for the window only
      per_minute  the averages per minute in the window
      readings    every checked reading in the window (only for windows up to
                  an hour, to keep the answer small): time "t" (measured),
                  heart rate "bpm" and check result "check"
    """
    checked, _ = check_readings(items)
    times = [r["measured_ts"] for r in checked if r.get("measured_ts") is not None]
    if not times:
        return {"minutes": minutes, "start": None, "end": None,
                "summary": summarise_heart_rate([]), "per_minute": [], "readings": []}

    end = max(times)
    start = end - minutes * 60
    window = [r for r in checked
              if r.get("measured_ts") is not None and r["measured_ts"] > start]

    history = {"minutes": minutes, "start": start, "end": end,
               "summary": summarise_heart_rate(window),
               "per_minute": averages_per_minute(window),
               "readings": []}
    if minutes <= 60:
        window_checked, _ = check_readings(window)
        history["readings"] = [
            {"t": round(r["measured_ts"], 3), "bpm": r.get("heart_bpm"), "check": r["check"]}
            for r in window_checked if r.get("measured_ts") is not None]
    return history


if __name__ == "__main__":
    # Five packets like the bracelet sends in its simulation mode
    # (heart rate counting up from 41), then one resend and one sensor error.
    # The firmware in simulation mode sets these two switches on every packet.
    NORMAL = FLAG_SIMULATION_MODE | FLAG_OFFLINE_BUFFERING

    def packet(sequence, bpm, flags=NORMAL):
        return struct.pack(PACKET_FORMAT, 4660, sequence, sequence * 1000,
                           bpm, 0, 0, 0, 87, flags)

    packets = [packet(n, 40 + n) for n in range(1, 6)]
    packets.append(packets[-1])                                 # sent twice
    packets.append(packet(6, 46, NORMAL | FLAG_SENSOR_ERROR))   # sensor error

    print("First packet decoded:")
    print(" ", decode_packet(packets[0]))
    print("Summary of", len(packets), "packets:")
    print(" ", summarise_heart_rate(packets))
