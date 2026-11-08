"""
Tests for the simulated data generator.

Run from the analytics/ folder:
    python -m pytest
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from generate_sample_data import UNITS, MOODS, generate  # noqa: E402


@pytest.fixture(scope="module")
def data():
    return generate(n_users=3, n_days=7, interval=300, seed=42,
                    start=date(2026, 10, 26))


def parse(ts):
    assert ts.endswith("Z"), f"timestamp not in UTC: {ts}"
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def events_of(data, kind):
    return [e for e in data["answer_key"]["events"] if e["type"] == kind]


def device_of(data, user_id):
    return next(u["device_id"] for u in data["users"] if u["user_id"] == user_id)


def readings_in(data, event, kind):
    device = device_of(data, event["user_id"])
    start, end = parse(event["start"]), parse(event["end"])
    return [r for r in data["readings"]
            if r["device_id"] == device and r["type"] == kind
            and start <= parse(r["measured_at"]) < end]


# --- format -----------------------------------------------------------------
def test_readings_follow_agreed_format(data):
    fields = {"reading_id", "device_id", "type", "value", "unit", "status",
              "measured_at", "received_at"}
    for r in data["readings"]:
        assert set(r) == fields
        assert r["unit"] == UNITS[r["type"]]
        assert r["status"] in ("valid", "unavailable")
        assert parse(r["received_at"]) >= parse(r["measured_at"])
        if r["status"] == "unavailable":
            assert r["value"] is None
        elif r["type"] == "heart_rate":
            assert isinstance(r["value"], int)


def test_journal_follows_agreed_format(data):
    for e in data["journal"]:
        assert e["mood"] in MOODS
        assert e["text"] is None or isinstance(e["text"], str)
        parse(e["created_at"])


def test_same_seed_gives_same_data():
    a = generate(2, 3, 600, seed=7, start=date(2026, 11, 2))
    b = generate(2, 3, 600, seed=7, start=date(2026, 11, 2))
    assert a == b


# --- planted events are really in the data ----------------------------------
def test_stress_raises_heart_rate_without_movement(data):
    for ev in events_of(data, "stress"):
        profile = next(p for p in data["answer_key"]["profiles"]
                       if p["user_id"] == ev["user_id"])
        hr = [r["value"] for r in readings_in(data, ev, "heart_rate")
              if r["status"] == "valid" and 30 < r["value"] < 220]
        move = [r["value"] for r in readings_in(data, ev, "movement")
                if r["status"] == "valid"]
        assert mean(hr) > profile["true_resting_heart_rate"] + 10
        assert mean(move) < 0.2


def test_exercise_has_high_movement(data):
    for ev in events_of(data, "exercise"):
        assert mean(r["value"] for r in readings_in(data, ev, "movement")
                    if r["status"] == "valid") > 0.3


def test_no_data_window_is_empty(data):
    for ev in events_of(data, "no_data"):
        assert readings_in(data, ev, "heart_rate") == []


def test_flatline_has_one_single_value(data):
    for ev in events_of(data, "flatline"):
        values = {r["value"] for r in readings_in(data, ev, "skin_conductance")
                  if r["status"] == "valid"}
        assert len(values) == 1


def test_connection_gap_readings_arrive_late(data):
    for ev in events_of(data, "connection_gap"):
        for r in readings_in(data, ev, "heart_rate"):
            assert parse(r["received_at"]) >= parse(ev["end"])


def test_not_worn_heart_rate_is_unavailable(data):
    for ev in events_of(data, "not_worn"):
        assert all(r["status"] == "unavailable"
                   for r in readings_in(data, ev, "heart_rate"))


def test_impossible_values_are_present(data):
    for ev in events_of(data, "impossible_value"):
        device = device_of(data, ev["user_id"])
        assert any(r["device_id"] == device and r["type"] == "heart_rate"
                   and r["measured_at"] == ev["measured_at"]
                   and r["value"] == ev["fake_value"]
                   for r in data["readings"])


def test_duplicates_are_present(data):
    for ev in events_of(data, "duplicate"):
        device = device_of(data, ev["user_id"])
        same = [r for r in data["readings"]
                if r["device_id"] == device and r["type"] == ev["reading_type"]
                and r["measured_at"] == ev["measured_at"]]
        assert len(same) >= 2


def test_every_stress_day_has_a_stressed_journal_entry(data):
    for ev in events_of(data, "stress"):
        day = parse(ev["start"]).date()
        moods = [e["mood"] for e in data["journal"]
                 if e["user_id"] == ev["user_id"]
                 and parse(e["created_at"]).date() == day]
        assert moods and all(m in ("stressed", "anxious") for m in moods)
