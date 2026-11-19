"""Tests for the heart-rate summary. Run from analytics/:  python -m pytest"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from heart_rate_summary import (PACKET_FORMAT, PACKET_SIZE,  # noqa: E402
                                decode_packet, summarise_heart_rate)


def reading(sequence, bpm, flags=0, device_id=1, is_test=0):
    """One reading with the field names the firmware uses."""
    return {"device_id": device_id, "sequence": sequence,
            "uptime_ms": sequence * 1000, "heart_bpm": bpm,
            "batt_lvl": 80, "isTest": is_test, "flags": flags}


# --- reading the bracelet's packet ------------------------------------------
def test_packet_is_25_bytes_like_the_firmware():
    assert PACKET_SIZE == 25


def test_decode_packet_gives_named_fields():
    raw = struct.pack(PACKET_FORMAT, 4660, 7, 7000, 72, 6900, 51000, 49000,
                      87, 0x60)
    assert decode_packet(raw) == {
        "device_id": 4660, "sequence": 7, "uptime_ms": 7000, "heart_bpm": 72,
        "heart_lastBeat": 6900, "heart_IrValue": 51000,
        "heart_redValue": 49000, "batt_lvl": 87, "flags": 0x60}


def test_wrong_packet_size_gives_a_clear_error():
    with pytest.raises(ValueError):
        decode_packet(b"\x00" * 20)


def test_raw_packets_can_be_summarised_directly():
    raw = [struct.pack(PACKET_FORMAT, 1, n, n * 1000, 70 + n, 0, 0, 0, 90, 0)
           for n in range(1, 4)]
    result = summarise_heart_rate(raw)
    assert result["used"] == 3
    assert result["latest"] == 73


# --- the checks ---------------------------------------------------------------
def test_normal_readings_are_summarised():
    result = summarise_heart_rate([reading(1, 70), reading(2, 72),
                                   reading(3, 74)])
    assert result == {"enough_data": True, "used": 3, "removed": 0,
                      "removed_why": {}, "lost": 0, "late": 0, "test_data": False,
                      "latest": 74, "average": 72, "min": 70, "max": 74,
                      "battery": 80}


def test_sensor_errors_are_removed():
    result = summarise_heart_rate([reading(1, 70), reading(2, 71, flags=2)])
    assert result["used"] == 1
    assert result["removed_why"] == {"sensor_error": 1}


def test_impossible_values_are_removed():
    result = summarise_heart_rate([reading(1, 70), reading(2, 25),
                                   reading(3, 240)])
    assert result["used"] == 1
    assert result["removed_why"] == {"impossible": 2}


def test_resent_packet_is_counted_once():
    result = summarise_heart_rate([reading(1, 70), reading(2, 71),
                                   reading(2, 71)])
    assert result["used"] == 2
    assert result["removed_why"] == {"duplicate": 1}


def test_restart_of_the_bracelet_is_not_a_duplicate():
    after_restart = dict(reading(1, 75), uptime_ms=500)
    result = summarise_heart_rate([reading(1, 70), reading(2, 71),
                                   after_restart])
    assert result["used"] == 3
    assert result["lost"] == 0


def test_stuck_value_is_removed():
    stuck = [reading(n, 40) for n in range(1, 41)]        # 40 times 40 BPM
    moving = [reading(n, 60 + n % 5) for n in range(41, 51)]
    result = summarise_heart_rate(stuck + moving)
    assert result["used"] == 10
    assert result["removed_why"] == {"stuck": 40}
    assert result["min"] >= 60


def test_lost_readings_are_counted():
    result = summarise_heart_rate([reading(1, 70), reading(2, 71),
                                   reading(6, 72)])
    assert result["lost"] == 3


def test_test_data_is_labelled():
    result = summarise_heart_rate([reading(1, 70, is_test=1)])
    assert result["test_data"] is True


def test_two_bracelets_are_never_mixed():
    with pytest.raises(ValueError):
        summarise_heart_rate([reading(1, 70, device_id=1),
                              reading(1, 90, device_id=2)])


def test_no_usable_data():
    result = summarise_heart_rate([reading(1, 240), reading(2, 70, flags=2)])
    assert result["enough_data"] is False
    assert "average" not in result
    assert summarise_heart_rate([])["enough_data"] is False


def test_plain_numbers_still_work():
    result = summarise_heart_rate([72, 230, None, 76])
    assert result["used"] == 2
    assert result["average"] == 74


# --- readings in the form the website and backend use ------------------------
def backend_reading(sequence, bpm, **extra):
    return {"type": "heart_rate", "value": float(bpm), "ts": 1000.0 + sequence,
            "sequence": sequence, "uptime_ms": sequence * 1000,
            "device_id": 7, "is_test": 0, "flags": 0, "battery": 55, **extra}


def test_backend_field_names_are_understood():
    result = summarise_heart_rate([backend_reading(1, 70),
                                   backend_reading(2, 72, is_test=1),
                                   backend_reading(3, 250),
                                   backend_reading(4, 74, flags=2)])
    assert result["used"] == 2
    assert result["removed_why"] == {"impossible": 1, "sensor_error": 1}
    assert result["test_data"] is True
    assert result["battery"] == 55


def test_other_sensor_types_are_ignored():
    other = {"type": "skin_conductance", "value": 3.2, "ts": 1.0}
    result = summarise_heart_rate([backend_reading(1, 70), other])
    assert result["used"] == 1
    assert result["removed"] == 0


def test_clean_readings_returns_the_usable_readings():
    from heart_rate_summary import clean_readings
    usable, report = clean_readings([backend_reading(1, 70),
                                     backend_reading(2, 10)])
    assert [r["heart_bpm"] for r in usable] == [70.0]
    assert usable[0]["ts"] == 1001.0
    assert report["removed_why"] == {"impossible": 1}


# --- results per minute (stored in the database) ------------------------------
def timed(sequence, bpm, second, **extra):
    """A reading measured and received at `second` (bracelet switched on at 0)."""
    return dict(backend_reading(sequence, bpm, **extra),
                ts=float(second), uptime_ms=second * 1000)


def test_averages_per_minute():
    from heart_rate_summary import averages_per_minute
    rows = averages_per_minute([timed(1, 70, 120), timed(2, 74, 150),
                                timed(3, 250, 170), timed(4, 80, 200)])
    assert rows == [
        {"minute": 120, "average": 72.0, "min": 70.0, "max": 74.0, "used": 2, "removed": 1},
        {"minute": 180, "average": 80.0, "min": 80.0, "max": 80.0, "used": 1, "removed": 0},
    ]


def test_minute_with_only_bad_readings():
    from heart_rate_summary import averages_per_minute
    assert averages_per_minute([timed(1, 10, 60)]) == [
        {"minute": 60, "average": None, "min": None, "max": None, "used": 0, "removed": 1}]


def test_check_readings_marks_every_reading():
    from heart_rate_summary import check_readings
    checked, lost = check_readings([backend_reading(1, 70), backend_reading(1, 70),
                                    backend_reading(2, 71, flags=2), backend_reading(5, 10)])
    assert [r["check"] for r in checked] == ["usable", "duplicate", "sensor_error", "impossible"]
    assert lost == 2


# --- the flags of the new firmware (one switch per bit) -----------------------
from heart_rate_summary import (FLAG_BATTERY_CHARGING, FLAG_LOW_BATTERY,  # noqa: E402
                                FLAG_OFFLINE_BUFFERING, FLAG_POOR_WRIST_CONTACT,
                                FLAG_SENSOR_ERROR, FLAG_SIMULATION_MODE)

NORMAL = FLAG_SIMULATION_MODE | FLAG_OFFLINE_BUFFERING   # every simulated packet


def test_status_flags_do_not_remove_readings():
    readings = [reading(1, 70, flags=NORMAL),
                reading(2, 71, flags=NORMAL | FLAG_LOW_BATTERY),
                reading(3, 72, flags=NORMAL | FLAG_BATTERY_CHARGING)]
    result = summarise_heart_rate(readings)
    assert result["used"] == 3
    assert result["removed"] == 0


def test_sensor_error_and_poor_contact_are_removed():
    result = summarise_heart_rate([
        reading(1, 70, flags=NORMAL),
        reading(2, 71, flags=NORMAL | FLAG_SENSOR_ERROR),
        reading(3, 72, flags=NORMAL | FLAG_POOR_WRIST_CONTACT)])
    assert result["used"] == 1
    assert result["removed_why"] == {"sensor_error": 1, "poor_contact": 1}


def test_simulation_switch_marks_test_data():
    assert summarise_heart_rate([reading(1, 70, flags=NORMAL)])["test_data"] is True
    assert summarise_heart_rate([reading(1, 70, flags=FLAG_OFFLINE_BUFFERING)])[
        "test_data"] is False


def test_raw_packet_of_the_new_firmware():
    raw = struct.pack(PACKET_FORMAT, 40848, 5, 5000, 77, 0, 0, 0, 90, NORMAL)
    result = summarise_heart_rate([raw])
    assert result["used"] == 1
    assert result["test_data"] is True


# --- interruption recovery ---------------------------------------------------
def test_buffered_readings_are_put_back_at_their_real_time():
    from heart_rate_summary import check_readings
    # Measured every second from second 100 to 119. Seconds 105-114 were
    # buffered during a connection drop and all arrived just before second 115.
    readings = []
    for n, second in enumerate(range(100, 120), start=1):
        arrived = 114.7 + (second - 105) * 0.02 if 105 <= second <= 114 else float(second)
        readings.append(dict(backend_reading(n, 70), ts=arrived, uptime_ms=second * 1000))
    readings.sort(key=lambda r: r["ts"])
    checked, lost = check_readings(readings)
    assert lost == 0
    by_sequence = {r["sequence"]: r for r in checked}
    assert round(by_sequence[6]["measured_ts"]) == 105     # second 105, not 115
    assert by_sequence[6]["late"] is True
    assert by_sequence[1]["late"] is False


def test_late_readings_are_counted_and_used():
    readings = [dict(backend_reading(n, 70 + n % 5), ts=float(100 + n), uptime_ms=n * 1000)
                for n in range(1, 6)]
    for n in range(6, 16):   # buffered for 10 seconds, arrived together at 116
        readings.append(dict(backend_reading(n, 70 + n % 5), ts=116.0 + n * 0.02,
                             uptime_ms=n * 1000))
    result = summarise_heart_rate(readings)
    assert result["used"] == 15
    assert result["late"] == 6     # the ones more than 5 seconds late


def test_late_readings_count_for_the_minute_they_were_measured():
    from heart_rate_summary import averages_per_minute
    # Measured at seconds 55-59 (minute 0) but arrived at second 64 (minute 60).
    readings = [timed(1, 70, 50)]
    for n, second in enumerate(range(55, 60), start=2):
        readings.append(dict(backend_reading(n, 80), ts=64.0 + n * 0.02,
                             uptime_ms=second * 1000))
    rows = averages_per_minute(readings)
    assert rows == [{"minute": 0, "average": 78.3, "min": 70.0, "max": 80.0,
                     "used": 6, "removed": 0}]


def test_restart_starts_a_new_run_of_times():
    from heart_rate_summary import check_readings
    first = [dict(backend_reading(n, 70), ts=1000.0 + n, uptime_ms=500_000 + n * 1000)
             for n in range(1, 4)]
    after_restart = [dict(backend_reading(n, 71), ts=2000.0 + n, uptime_ms=n * 1000)
                     for n in range(1, 4)]
    checked, _ = check_readings(first + after_restart)
    assert [round(r["measured_ts"]) for r in checked] == [1001, 1002, 1003, 2001, 2002, 2003]
    assert not any(r["late"] for r in checked)


def test_lost_readings_are_counted_when_arriving_out_of_order():
    readings = [reading(n, 70 + n % 5) for n in (1, 2, 4, 3, 7)]
    assert summarise_heart_rate(readings)["lost"] == 2     # 5 and 6


def test_history_only_contains_the_chosen_time_range():
    from heart_rate_summary import heart_rate_history
    # one reading every 10 seconds for 30 minutes, the last one is impossible
    items = [timed(n, 60 + n % 20, n * 10) for n in range(1, 181)]
    items[-1] = timed(180, 250, 1800)
    history = heart_rate_history(items, minutes=15)
    assert history["end"] == 1800 and history["start"] == 900
    assert all(r["t"] > 900 for r in history["readings"])
    assert len(history["readings"]) == 90
    assert history["readings"][-1]["check"] == "impossible"
    assert history["summary"]["used"] == 89 and history["summary"]["removed"] == 1
    assert history["per_minute"][0]["minute"] >= 900


def test_history_of_a_day_has_no_single_readings():
    from heart_rate_summary import heart_rate_history
    items = [timed(n, 70 + n % 5, n * 10) for n in range(1, 31)]
    history = heart_rate_history(items, minutes=1440)
    assert history["readings"] == [] and len(history["per_minute"]) == 6
    assert history["summary"]["used"] == 30


def test_history_without_readings():
    from heart_rate_summary import heart_rate_history
    history = heart_rate_history([], minutes=15)
    assert history["end"] is None and history["summary"]["enough_data"] is False


def test_history_of_one_minute():
    from heart_rate_summary import heart_rate_history
    items = [timed(n, 60 + n % 20, n) for n in range(1, 301)]   # 5 minutes, 1 per second
    history = heart_rate_history(items, minutes=1)
    assert history["start"] == 240 and len(history["readings"]) == 60


def test_restart_is_found_by_the_reading_number_too():
    from heart_rate_summary import estimate_measured_times
    # second run: counter starts again at 1, but the uptime does not drop
    first = [timed(n, 70, n) for n in range(1, 101)]
    second = [dict(timed(n, 70, 500 + n), uptime_ms=(200 + n) * 1000) for n in range(1, 11)]
    result = estimate_measured_times(first + second)
    assert [r["measured_ts"] for r in result[-10:]] == [500.0 + n for n in range(1, 11)]
    assert not any(r["late"] for r in result)


def test_buffered_mark_from_the_website_counts_as_late():
    # the website already put these readings back at their measured time
    items = [timed(n, 70 + n % 5, n) for n in range(1, 21)]
    for r in items[10:15]:
        r["buffered"] = 1
    assert summarise_heart_rate(items)["late"] == 5


def test_bluetooth_error_is_removed():
    readings = [reading(n, 70 + n % 5) for n in range(1, 6)] + [reading(6, 72, flags=0x01)]
    result = summarise_heart_rate(readings)
    assert result["used"] == 5 and result["removed_why"] == {"ble_error": 1}
