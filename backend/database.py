"""
MoodPulse - database (PostgreSQL)

Stores the bracelet readings and the analytics results in PostgreSQL, so they
are kept when the server restarts.

How it connects
---------------
PostgreSQL is a separate program (a database server). This file connects to it
with the psycopg library, using one address called DATABASE_URL, for example:

    postgresql://postgres:YOUR_PASSWORD@localhost:5432/moodpulse
                 |        |             |         |    |
                 user     password      computer  port database name

The address is read from the file backend/.env, so the password is never in
the code (.env is in .gitignore and is not uploaded to GitHub).

The tables
----------
  readings           every bracelet reading exactly as it arrived (raw data,
                     never changed). Bad readings are stored too, so the
                     analytics can count what it removes.

  heart_rate_minute  the analytics results: per bracelet and per minute, the
                     average, lowest and highest usable heart rate, and how
                     many readings were used or left out.

The tables are created automatically when the server starts.

Without DATABASE_URL
--------------------
If DATABASE_URL is not set, the readings are kept in the server's memory
instead (the way the backend worked before the database was added). Everything
still works, but the readings are gone when the server stops. /health shows
which storage is used.
"""

import os
import threading
import time

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:          # only needed when DATABASE_URL is set
    psycopg = None

# Bracelet fields that are stored next to the value.
READING_FIELDS = ("sequence", "uptime_ms", "device_id", "is_test", "flags", "battery", "buffered")
FLAG_SIMULATION_MODE = 0x40
MAX_MEMORY_READINGS = 20000   # memory storage keeps at most this many readings

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type        TEXT             NOT NULL,   -- 'heart_rate' (or 'skin_conductance')
    value       DOUBLE PRECISION NOT NULL,   -- BPM for heart rate
    ts          DOUBLE PRECISION NOT NULL,   -- when it was received, seconds since 1970 (UTC)
    received_at TIMESTAMPTZ GENERATED ALWAYS AS (to_timestamp(ts)) STORED,  -- the same, readable
    device_id   INTEGER,                     -- which bracelet
    sequence    BIGINT,                      -- the bracelet's reading counter
    uptime_ms   BIGINT,                      -- time since the bracelet was switched on
    is_test     SMALLINT,                    -- 1 = simulated reading
    flags       SMALLINT,                    -- status bits from the bracelet (see analytics README)
    battery     SMALLINT,                    -- battery percentage
    buffered    BOOLEAN,                     -- true = sent late from the bracelet's buffer
    stored_at   TIMESTAMPTZ NOT NULL DEFAULT now()  -- when the server stored it
);
-- databases made before the "buffered" column existed get it added
ALTER TABLE readings ADD COLUMN IF NOT EXISTS buffered BOOLEAN;
CREATE INDEX IF NOT EXISTS readings_by_device_time ON readings (device_id, ts);
CREATE INDEX IF NOT EXISTS readings_by_time ON readings (ts);

CREATE TABLE IF NOT EXISTS heart_rate_minute (
    device_id   INTEGER NOT NULL,            -- -1 = readings without a bracelet id
    minute      BIGINT  NOT NULL,            -- start of the minute, seconds since 1970 (UTC)
    minute_start TIMESTAMPTZ GENERATED ALWAYS AS (to_timestamp(minute)) STORED,
    average     DOUBLE PRECISION,            -- of the usable readings (NULL if none)
    min         DOUBLE PRECISION,
    max         DOUBLE PRECISION,
    used        INTEGER NOT NULL,            -- readings that passed the checks
    removed     INTEGER NOT NULL,            -- readings that were left out
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (device_id, minute)
);
"""


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------
def _read_env_file():
    """Read DATABASE_URL from backend/.env if it is not set already."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key.strip() and not key.startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def database_url():
    """The PostgreSQL address, or None when no database is configured."""
    _read_env_file()
    return os.environ.get("DATABASE_URL") or None


def storage():
    """'postgresql' when DATABASE_URL is set, otherwise 'memory'."""
    return "postgresql" if database_url() else "memory"


def connect():
    """
    Open a connection to PostgreSQL. Use it with "with":
        with connect() as db:
            db.execute(...)
    At the end of the "with" block the changes are saved (committed) and the
    connection is closed.
    """
    if psycopg is None:
        raise RuntimeError("DATABASE_URL is set, but the psycopg library is not installed: "
                           "run  pip install -r backend/requirements.txt")
    return psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5)


def init():
    """Check the connection and create the tables if they do not exist yet."""
    if storage() == "memory":
        return
    with connect() as db:
        db.execute(SCHEMA)


def _since(hours):
    return time.time() - hours * 3600


def _as_dict(row):
    """A stored row as a reading, leaving out the fields that are empty."""
    reading = {"type": row["type"], "value": row["value"], "ts": row["ts"]}
    for field in READING_FIELDS:
        if row.get(field) is not None:
            reading[field] = row[field]
    return reading


# ---------------------------------------------------------------------------
# Memory storage (used when DATABASE_URL is not set)
# ---------------------------------------------------------------------------
_memory_lock = threading.Lock()
_memory_readings = []        # the readings, in the order they were stored
_memory_minutes = {}         # (device_id, minute) -> per-minute result
_memory_next_id = 1


def _memory_select(keep, limit=None):
    """Stored readings for which keep(r) is true, ordered by ts (like the SQL)."""
    with _memory_lock:
        rows = [dict(r) for r in _memory_readings if keep(r)]
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    if limit is not None:
        rows = rows[-limit:]
    return rows


# ---------------------------------------------------------------------------
# Readings
# ---------------------------------------------------------------------------
def _with_test_mark(r):
    """is_test: as sent, otherwise taken from the bracelet's simulation flag."""
    if r.get("is_test") is not None:
        return r.get("is_test")
    flags = r.get("flags")
    if flags is None:
        return None
    return 1 if flags & FLAG_SIMULATION_MODE else 0


def add_readings(readings):
    """Store readings (dictionaries with type, value, ts and optional fields).
    Returns how many readings are stored in total."""
    global _memory_next_id
    if storage() == "memory":
        with _memory_lock:
            for r in readings:
                row = {"id": _memory_next_id, "type": r["type"], "value": r["value"], "ts": r["ts"]}
                _memory_next_id += 1
                for field in READING_FIELDS:
                    row[field] = r.get(field)
                row["is_test"] = _with_test_mark(r)
                _memory_readings.append(row)
            if len(_memory_readings) > MAX_MEMORY_READINGS:
                del _memory_readings[:-MAX_MEMORY_READINGS]
            return len(_memory_readings)

    rows = [(r["type"], r["value"], r["ts"],
             r.get("device_id"), r.get("sequence"), r.get("uptime_ms"),
             _with_test_mark(r), r.get("flags"), r.get("battery"), r.get("buffered"))
            for r in readings]
    with connect() as db:
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO readings (type, value, ts, device_id, sequence, uptime_ms,"
                " is_test, flags, battery, buffered)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows)
        return db.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]


def recent_readings(hours=24, limit=20000):
    """The most recent readings of all types, oldest first."""
    if storage() == "memory":
        since = _since(hours)
        return [_as_dict(r) for r in _memory_select(lambda r: r["ts"] >= since, limit)]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM (SELECT * FROM readings WHERE ts >= %s"
            " ORDER BY ts DESC, id DESC LIMIT %s) AS recent ORDER BY ts, id",
            (_since(hours), limit)).fetchall()
    return [_as_dict(row) for row in rows]


def latest_device():
    """The bracelet that sent the most recent heart-rate reading (or None)."""
    if storage() == "memory":
        rows = _memory_select(lambda r: r["type"] == "heart_rate")
        return rows[-1]["device_id"] if rows else None
    with connect() as db:
        row = db.execute(
            "SELECT device_id FROM readings WHERE type = 'heart_rate'"
            " ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
    return row["device_id"] if row else None


def heart_readings(device_id, hours=24):
    """
    The heart-rate readings of one bracelet, in the order the website received
    them from the bracelet (by "ts"). The website sends readings at the same
    time, so they can be stored slightly out of order; sorting by "ts" puts
    them back in the bracelet's order.
    """
    if storage() == "memory":
        since = _since(hours)
        return [_as_dict(r) for r in _memory_select(
            lambda r: r["type"] == "heart_rate" and r["ts"] >= since and r["device_id"] == device_id)]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM readings WHERE type = 'heart_rate' AND ts >= %s"
            " AND device_id IS NOT DISTINCT FROM %s ORDER BY ts, id",
            (_since(hours), device_id)).fetchall()
    return [_as_dict(row) for row in rows]


def heart_readings_until_latest(device_id, seconds):
    """
    The heart-rate readings of one bracelet that arrived in the last `seconds`
    seconds before its LATEST reading (not before now), so the charts can also
    show data from yesterday. Ordered like heart_readings.
    """
    if storage() == "memory":
        rows = _memory_select(lambda r: r["type"] == "heart_rate" and r["device_id"] == device_id)
        if not rows:
            return []
        start = max(r["ts"] for r in rows) - seconds
        return [_as_dict(r) for r in rows if r["ts"] >= start]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM readings WHERE type = 'heart_rate'"
            " AND device_id IS NOT DISTINCT FROM %s"
            " AND ts >= (SELECT max(ts) FROM readings WHERE type = 'heart_rate'"
            "            AND device_id IS NOT DISTINCT FROM %s) - %s"
            " ORDER BY ts, id",
            (device_id, device_id, seconds)).fetchall()
    return [_as_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Analytics results
# ---------------------------------------------------------------------------
def save_minutes(device_id, minutes):
    """Store (or update) the per-minute analytics results of one bracelet."""
    key = -1 if device_id is None else device_id
    if storage() == "memory":
        with _memory_lock:
            for m in minutes:
                _memory_minutes[(key, m["minute"])] = {
                    k: m[k] for k in ("minute", "average", "min", "max", "used", "removed")}
        return
    with connect() as db:
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO heart_rate_minute (device_id, minute, average, min, max,"
                " used, removed) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (device_id, minute) DO UPDATE SET average = EXCLUDED.average,"
                " min = EXCLUDED.min, max = EXCLUDED.max, used = EXCLUDED.used,"
                " removed = EXCLUDED.removed, updated_at = now()",
                [(key, m["minute"], m["average"], m["min"], m["max"], m["used"],
                  m["removed"]) for m in minutes])


def minutes(device_id, hours=24):
    """The stored per-minute results of one bracelet, oldest first."""
    key = -1 if device_id is None else device_id
    first = int(_since(hours) // 60) * 60
    if storage() == "memory":
        with _memory_lock:
            return [dict(v) for (dev, minute), v in sorted(_memory_minutes.items())
                    if dev == key and minute >= first]
    with connect() as db:
        rows = db.execute(
            "SELECT minute, average, min, max, used, removed FROM heart_rate_minute"
            " WHERE device_id = %s AND minute >= %s ORDER BY minute",
            (key, first)).fetchall()
    return [dict(row) for row in rows]


def clear():
    """Delete all readings and analytics results."""
    if storage() == "memory":
        with _memory_lock:
            _memory_readings.clear()
            _memory_minutes.clear()
        return
    with connect() as db:
        db.execute("DELETE FROM readings")
        db.execute("DELETE FROM heart_rate_minute")
