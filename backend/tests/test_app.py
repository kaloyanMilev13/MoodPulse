"""
Tests for the whole chain on the server side:
readings in -> PostgreSQL -> analytics checks -> results in PostgreSQL -> advice.

Run from the project folder:  python -m pytest

These tests EMPTY the database they use, so they use a separate test database,
set as TEST_DATABASE_URL in backend/.env (see .env.example). Without it, these
tests are skipped. The AI model does not have to be running; the advice then
uses the built-in rules.
"""

import os
import time
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "analytics" / "src"))

import database  # noqa: E402

database._read_env_file()
TEST_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.skip("TEST_DATABASE_URL is not set in backend/.env", allow_module_level=True)
if TEST_URL == os.environ.get("DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL must be a different database than DATABASE_URL, "
                "because the tests empty it", allow_module_level=True)

os.environ["DATABASE_URL"] = TEST_URL                 # the server uses the test database
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:9/v1"   # no AI model listens here
os.environ["LLM_TIMEOUT"] = "2"

try:
    database.init()
except Exception as error:
    pytest.skip(f"test database not reachable: {error}", allow_module_level=True)

import app as backend  # noqa: E402
from fake_bracelet import make_readings  # noqa: E402


def query(sql):
    with database.connect() as db:
        return db.execute(sql).fetchone()


@pytest.fixture
def client():
    database.clear()
    backend.session_logs.clear()
    backend.sensor_session_active = False
    return backend.app.test_client()


def start(client):
    assert client.post("/api/sensor-session", json={"active": True}).status_code == 200


def summary(client):
    return client.get("/api/heart-summary").get_json()["summary"]


# --- storing readings -----------------------------------------------------------
def test_readings_are_refused_until_started(client):
    assert client.post("/api/sensors", json={"readings": make_readings(5, False)}).status_code == 409


def test_readings_are_stored_in_the_database(client):
    start(client)
    answer = client.post("/api/sensors", json={"readings": make_readings(20, False)})
    assert answer.status_code == 201
    assert answer.get_json()["total"] == 20

    row = query("SELECT value, device_id, sequence, is_test, flags, battery"
                " FROM readings ORDER BY id LIMIT 1")
    assert row == {"value": 41.0, "device_id": 4660, "sequence": 1,
                   "is_test": 1, "flags": 0x60, "battery": 87}

    stored = client.get("/api/sensors").get_json()["readings"]
    assert len(stored) == 20
    assert stored[0]["sequence"] == 1


def test_readings_survive_a_server_restart(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(10, False)})
    database.init()                       # what the server does when it starts again
    assert query("SELECT COUNT(*) AS n FROM readings") == {"n": 10}
    assert len(client.get("/api/sensors").get_json()["readings"]) == 10


def test_malformed_readings_are_rejected(client):
    start(client)
    for bad in ({"type": "heart_rate", "value": "abc"},
                {"type": "heart_rate", "value": 300},
                {"type": "temperature", "value": 37}):
        assert client.post("/api/sensors", json={"readings": [bad]}).status_code == 400


def test_readings_without_bracelet_fields_still_work(client):
    start(client)
    plain = [{"type": "heart_rate", "value": 70 + n, "ts": 1_800_000_000 + n} for n in range(6)]
    assert client.post("/api/sensors", json={"readings": plain}).status_code == 201


# --- analytics on the stored readings ---------------------------------------------
def test_clean_readings_are_summarised(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(60, False)})
    result = summary(client)
    assert result["used"] == 60
    assert result["removed"] == 0
    assert result["latest"] == 100
    assert result["device_id"] == 4660
    assert result["test_data"] is True


def test_faulty_readings_are_counted_and_left_out(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(60, True)})
    result = summary(client)
    assert result["removed_why"] == {"sensor_error": 1, "poor_contact": 1, "duplicate": 1}
    assert result["lost"] == 3
    assert result["used"] == 55
    assert result["max"] <= 220


def test_results_per_minute_are_stored_in_the_database(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(150, True)})
    per_minute = client.get("/api/heart-summary").get_json()["per_minute"]
    assert sum(m["used"] for m in per_minute) == 145
    assert sum(m["removed"] for m in per_minute) == 3

    stored = query("SELECT SUM(used) AS used, SUM(removed) AS removed"
                   " FROM heart_rate_minute WHERE device_id = 4660")
    assert stored == {"used": 145, "removed": 3}


def test_two_bracelets_are_not_mixed(client):
    start(client)
    now = make_readings(1, False)[0]["ts"]
    first = [{"type": "heart_rate", "value": 60 + n, "ts": now - 100 + n, "sequence": n,
              "uptime_ms": n * 1000, "device_id": 1} for n in range(1, 6)]
    second = [{"type": "heart_rate", "value": 120 + n, "ts": now - 50 + n, "sequence": n,
               "uptime_ms": n * 1000, "device_id": 2} for n in range(1, 6)]
    client.post("/api/sensors", json={"readings": first + second})
    result = summary(client)
    assert result["device_id"] == 2
    assert result["used"] == 5
    assert result["min"] > 120


def test_reset_clears_the_database(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(10, False)})
    summary(client)
    client.post("/reset-session")
    assert summary(client)["used"] == 0
    assert query("SELECT COUNT(*) AS n FROM heart_rate_minute") == {"n": 0}


# --- advice ---------------------------------------------------------------------
def test_advice_uses_only_the_checked_readings(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(60, True)})
    readings = client.get("/api/sensors").get_json()["readings"]
    answer = client.post("/api/advice", json={"readings": readings})
    data = answer.get_json()
    assert answer.status_code == 200
    assert data["source"] == "rules"
    heart = data["facts"]["heart_rate"]
    assert heart["samples"] == 55
    assert heart["discarded_invalid"] == 3
    assert heart["max"] <= 220


def test_advice_needs_some_data(client):
    assert client.post("/api/advice", json={"readings": []}).status_code == 422


# --- website and camera -----------------------------------------------------------
def test_website_files_are_served(client):
    for page in ("/", "/jonathan_index.html", "/bracelet.js", "/advisor.html"):
        assert client.get(page).status_code == 200


def test_face_analysis_answers_clearly_when_not_installed(client):
    try:
        backend.load_deepface()
        pytest.skip("face analysis is installed on this computer")
    except Exception:
        pass
    client.post("/continue-session")
    try:
        assert client.post("/analyze-emotion", json={"image": "x"}).status_code == 503
    finally:
        client.post("/stop-session")


def test_health_shows_the_storage(client):
    answer = client.get("/health").get_json()
    assert answer["storage"] == "postgresql" and answer["database"] is True


def test_heart_rate_outside_30_to_220_is_refused(client):
    start(client)
    now = time.time()
    good = {"type": "heart_rate", "value": 70, "ts": now, "device_id": 5,
            "sequence": 1, "uptime_ms": 1000, "flags": 0x40, "battery": 80}
    bad = dict(good, value=250, sequence=2, uptime_ms=2000, ts=now + 1)
    assert client.post("/api/sensors", json={"readings": [good, bad]}).status_code == 400
    assert client.get("/api/sensors").get_json()["readings"] == []


def test_flagged_and_buffered_readings_are_stored(client):
    start(client)
    now = time.time()
    good = {"type": "heart_rate", "value": 70, "ts": now, "device_id": 5,
            "sequence": 1, "uptime_ms": 1000, "flags": 0x40, "battery": 80}
    poor = dict(good, value=60, sequence=2, uptime_ms=2000, ts=now + 1, flags=0x50)
    late = dict(good, value=72, sequence=3, uptime_ms=3000, ts=now + 2, buffered=True)
    answer = client.post("/api/sensors", json={"readings": [good, poor, late]})
    assert answer.status_code == 201 and answer.get_json()["accepted"] == 3
    stored = client.get("/api/sensors").get_json()["readings"]
    assert [r["value"] for r in stored] == [70, 60, 72]
    assert stored[0]["is_test"] == 1 and stored[2]["buffered"] is True


def test_readings_stored_out_of_order_are_analysed_in_bracelet_order(client):
    # The website sends readings at the same time, so they can arrive mixed up.
    start(client)
    readings = make_readings(30, False)
    shuffled = readings[1::2] + readings[0::2]
    for reading in shuffled:
        client.post("/api/sensors", json={"readings": [reading]})
    result = summary(client)
    assert result["lost"] == 0
    assert result["used"] == 30
    assert result["latest"] == readings[-1]["value"]


def test_connection_drop_is_recovered(client):
    # --problems includes a 10-second connection drop: those readings arrive
    # late from the bracelet's buffer. They must still be used, counted as
    # late, and put in the minute they were measured.
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(60, True)})
    answer = client.get("/api/heart-summary").get_json()
    assert answer["summary"]["late"] >= 5
    assert answer["summary"]["used"] == 55
    assert sum(m["used"] for m in answer["per_minute"]) == 55


def test_new_firmware_flags_are_understood(client):
    start(client)
    readings = make_readings(10, False)
    readings[3]["flags"] = 0x60 | 0x10          # poor wrist contact
    readings[6]["flags"] = 0x60 | 0x08 | 0x80   # low battery and charging: still usable
    client.post("/api/sensors", json={"readings": readings})
    result = summary(client)
    assert result["used"] == 9
    assert result["removed_why"] == {"poor_contact": 1}
    assert result["test_data"] is True


def test_heart_history_for_the_charts(client):
    start(client)
    client.post("/api/sensors", json={"readings": make_readings(60, True)})
    answer = client.get("/api/heart-history?minutes=15").get_json()
    assert answer["status"] == "success"
    assert answer["summary"]["used"] == 55 and answer["summary"]["lost"] == 3
    assert len(answer["readings"]) == 58          # 55 used + 3 left out
    assert {r["check"] for r in answer["readings"]} == {"usable", "duplicate", "poor_contact", "sensor_error"}
    assert answer["per_minute"]
    assert query("SELECT count(*) AS n FROM heart_rate_minute")["n"] >= 1


def test_heart_history_only_allows_known_ranges(client):
    assert client.get("/api/heart-history?minutes=7").status_code == 400
    assert client.get("/api/heart-history?minutes=1").status_code == 200
