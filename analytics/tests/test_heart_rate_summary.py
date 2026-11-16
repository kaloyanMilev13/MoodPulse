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
def test_packet_is_26_bytes_like_the_firmware():
    assert PACKET_SIZE == 26


def test_decode_packet_gives_named_fields():
    raw = struct.pack(PACKET_FORMAT, 4660, 7, 7000, 72, 6900, 51000, 49000,
                      87, 1, 0)
    assert decode_packet(raw) == {
        "device_id": 4660, "sequence": 7, "uptime_ms": 7000, "heart_bpm": 72,
        "heart_lastBeat": 6900, "heart_IrValue": 51000,
        "heart_redValue": 49000, "batt_lvl": 87, "isTest": 1, "flags": 0}


def test_wrong_packet_size_gives_a_clear_error():
    with pytest.raises(ValueError):
        decode_packet(b"\x00" * 20)


def test_raw_packets_can_be_summarised_directly():
    raw = [struct.pack(PACKET_FORMAT, 1, n, n * 1000, 70 + n, 0, 0, 0, 90, 0, 0)
           for n in range(1, 4)]
    result = summarise_heart_rate(raw)
    assert result["used"] == 3
    assert result["latest"] == 73


# --- the checks ---------------------------------------------------------------
def test_normal_readings_are_summarised():
    result = summarise_heart_rate([reading(1, 70), reading(2, 72),
                                   reading(3, 74)])
    assert result == {"enough_data": True, "used": 3, "removed": 0,
                      "removed_why": {}, "lost": 0, "test_data": False,
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
def test_averages_per_minute():
    from heart_rate_summary import averages_per_minute
    readings = [backend_reading(1, 70), backend_reading(2, 74), backend_reading(3, 250)]
    for r, ts in zip(readings, (120.0, 150.0, 170.0)):
        r["ts"] = ts
    later = backend_reading(4, 80)
    later["ts"] = 200.0
    rows = averages_per_minute(readings + [later])
    assert rows == [
        {"minute": 120, "average": 72.0, "min": 70.0, "max": 74.0, "used": 2, "removed": 1},
        {"minute": 180, "average": 80.0, "min": 80.0, "max": 80.0, "used": 1, "removed": 0},
    ]


def test_minute_with_only_bad_readings():
    from heart_rate_summary import averages_per_minute
    bad = backend_reading(1, 10)
    bad["ts"] = 60.0
    assert averages_per_minute([bad]) == [
        {"minute": 60, "average": None, "min": None, "max": None, "used": 0, "removed": 1}]


def test_check_readings_marks_every_reading():
    from heart_rate_summary import check_readings
    checked, lost = check_readings([backend_reading(1, 70), backend_reading(1, 70),
                                    backend_reading(2, 71, flags=2), backend_reading(5, 10)])
    assert [r["check"] for r in checked] == ["usable", "duplicate", "sensor_error", "impossible"]
    assert lost == 2
