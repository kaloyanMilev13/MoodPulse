import os
import threading
import time
from urllib.parse import quote

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:          # only needed when a PostgreSQL URL is configured
    psycopg = None

# Bracelet fields that are stored next to the value.
READING_FIELDS = ("sequence", "uptime_ms", "device_id", "is_test", "flags", "battery", "buffered")
FLAG_SIMULATION_MODE = 0x40
MAX_MEMORY_READINGS = 20000   # memory storage keeps at most this many readings

SCHEMA = """
CREATE TABLE IF NOT EXISTS public.users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    age SMALLINT,
    gender VARCHAR(30),
    height_cm DOUBLE PRECISION,
    weight_kg DOUBLE PRECISION,
    data_processing_consent BOOLEAN NOT NULL DEFAULT FALSE,
    data_processing_consent_at TIMESTAMPTZ,
    data_processing_consent_version VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS age SMALLINT;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS gender VARCHAR(30);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS height_cm DOUBLE PRECISION;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS weight_kg DOUBLE PRECISION;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS data_processing_consent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS data_processing_consent_at TIMESTAMPTZ;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS data_processing_consent_version VARCHAR(32);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON public.users (lower(email));

CREATE TABLE IF NOT EXISTS public.devices (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL,
    device_identifier VARCHAR(255) NOT NULL UNIQUE,
    device_name VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE IF NOT EXISTS public.journal_entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE IF NOT EXISTS public.measurements (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL,
    device_id BIGINT NOT NULL,
    measurement_type VARCHAR(50) NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit VARCHAR(30) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES public.devices(id),
    FOREIGN KEY (user_id) REFERENCES public.users(id)
);

CREATE TABLE IF NOT EXISTS readings (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT REFERENCES public.users(id),
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
ALTER TABLE readings ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES public.users(id);
CREATE INDEX IF NOT EXISTS readings_by_device_time ON readings (device_id, ts);
CREATE INDEX IF NOT EXISTS readings_by_time ON readings (ts);
CREATE INDEX IF NOT EXISTS readings_by_user_device_time ON readings (user_id, device_id, ts);

"""

_key_vault_lock = threading.Lock()
_key_vault_cache = {}


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------
def _read_env_file():
    """Load local configuration from the .env beside this file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            values = {}
            for line in f:
                key, _, value = line.strip().partition("=")
                if key.strip() and not key.startswith("#"):
                    values[key.strip()] = value.strip()
    except FileNotFoundError:
        return

    use_key_vault = (
        os.environ.get("AZURE_KEY_VAULT_URL")
        or values.get("AZURE_KEY_VAULT_URL")
    )
    for key, value in values.items():
        if key == "DATABASE_URL" and use_key_vault:
            continue
        os.environ.setdefault(key, value)


def key_vault_secret(secret_name=None):
    """Return a cached Key Vault secret using the configured Azure identity."""
    _read_env_file()
    direct_secret = os.environ.get("FLASK_SECRET_KEY")
    if direct_secret:
        return direct_secret
    if secret_name is None:
        secret_name = os.environ.get(
            "FLASK_SECRET_KEY_SECRET_NAME",
            "moodpulse-session-signing-key",
        )
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if not vault_url:
        raise RuntimeError("AZURE_KEY_VAULT_URL must be set to read Key Vault secrets.")
    cache_key = (vault_url, secret_name)
    with _key_vault_lock:
        if cache_key not in _key_vault_cache:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient
            except ImportError as e:
                raise RuntimeError(
                    "Azure Key Vault is configured, but its SDK packages are "
                    "missing. Install azure-identity and azure-keyvault-secrets."
                ) from e

            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=vault_url, credential=credential)
            secret_value = client.get_secret(secret_name).value
            if not secret_value:
                raise RuntimeError(f"Key Vault secret '{secret_name}' has no value.")
            _key_vault_cache[cache_key] = secret_value
        return _key_vault_cache[cache_key]


def database_url():
    """Build the database URL using the Key Vault password, or use DATABASE_URL."""
    _read_env_file()
    direct_password = os.environ.get("DATABASE_PASSWORD")
    if direct_password:
        host = os.environ.get("DATABASE_HOST", "localhost")
        port = os.environ.get("DATABASE_PORT", "5432")
        name = os.environ.get("DATABASE_NAME", "moodpulse")
        user = os.environ.get("DATABASE_USER", "moodpulse_app")
        if not all((host, port, name, user)):
            raise RuntimeError(
                "DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, and DATABASE_USER "
                "must all be set when using DATABASE_PASSWORD."
            )
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return "postgresql://{}:{}@{}:{}/{}".format(
            quote(user, safe=""),
            quote(direct_password, safe=""),
            host,
            quote(port, safe=""),
            quote(name, safe=""),
        )
    vault_url = os.environ.get("AZURE_KEY_VAULT_URL")
    if vault_url:
        secret_name = os.environ.get(
            "DATABASE_PASSWORD_SECRET_NAME",
            "moodpulse-database-password",
        )
        password = key_vault_secret(secret_name)
        host = os.environ.get("DATABASE_HOST", "localhost")
        port = os.environ.get("DATABASE_PORT", "5432")
        name = os.environ.get("DATABASE_NAME", "moodpulse")
        user = os.environ.get("DATABASE_USER", "moodpulse_app")
        if not all((host, port, name, user)):
            raise RuntimeError(
                "DATABASE_HOST, DATABASE_PORT, DATABASE_NAME, and DATABASE_USER "
                "must all be set when using Azure Key Vault."
            )
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return "postgresql://{}:{}@{}:{}/{}".format(
            quote(user, safe=""),
            quote(password, safe=""),
            host,
            quote(port, safe=""),
            quote(name, safe=""),
        )
    return os.environ.get("DATABASE_URL") or None


def create_user(name, email, password_hash, age, gender, height_cm, weight_kg, consent_version):
    """Create an account, returning None when its email is already registered."""
    with connect() as db:
        return db.execute(
            "INSERT INTO public.users"
            " (name, email, password_hash, age, gender, height_cm, weight_kg,"
            "  data_processing_consent, data_processing_consent_at, data_processing_consent_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP, %s)"
            " ON CONFLICT DO NOTHING"
            " RETURNING id, name, email, age, gender, height_cm, weight_kg,"
            " data_processing_consent, data_processing_consent_at, data_processing_consent_version",
            (name, email, password_hash, age, gender, height_cm, weight_kg, consent_version),
        ).fetchone()


def user_by_email(email):
    """Find an account by case-insensitive email address."""
    with connect() as db:
        return db.execute(
            "SELECT id, name, email, password_hash, age, gender, height_cm, weight_kg,"
            " data_processing_consent, data_processing_consent_at, data_processing_consent_version"
            " FROM public.users WHERE lower(email) = %s",
            (email.lower(),),
        ).fetchone()


def update_password_hash(user_id, password_hash):
    """Update an account's Argon2 hash after successful verification."""
    with connect() as db:
        db.execute(
            "UPDATE public.users SET password_hash = %s WHERE id = %s",
            (password_hash, user_id),
        )


def user_by_id(user_id):
    """Return public account fields, excluding the password hash."""
    with connect() as db:
        return db.execute(
            "SELECT id, name, email, age, gender, height_cm, weight_kg,"
            " data_processing_consent, data_processing_consent_at, data_processing_consent_version"
            " FROM public.users WHERE id = %s",
            (user_id,),
        ).fetchone()


def journal_entries(user_id, limit=300):
    """Return one account's journal entries, newest first."""
    if storage() == "memory":
        with _memory_journal_lock:
            rows = [
                dict(row) for row in _memory_journal_entries
                if row["user_id"] == user_id
            ]
        rows.sort(key=lambda row: (row["created_at"], row["id"]), reverse=True)
        return rows[:limit]
    with connect() as db:
        rows = db.execute(
            "SELECT id, content, created_at, updated_at"
            " FROM public.journal_entries WHERE user_id = %s"
            " ORDER BY created_at DESC, id DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "content": row["content"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
        for row in rows
    ]


def create_journal_entry(user_id, content):
    """Create a journal entry for one account."""
    if storage() == "memory":
        with _memory_journal_lock:
            global _memory_journal_next_id
            row = {
                "id": _memory_journal_next_id,
                "user_id": user_id,
                "content": content,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            _memory_journal_next_id += 1
            _memory_journal_entries.append(row)
        return {
            "id": row["id"],
            "content": row["content"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    with connect() as db:
        row = db.execute(
            "INSERT INTO public.journal_entries (user_id, content)"
            " VALUES (%s, %s)"
            " RETURNING id, content, created_at, updated_at",
            (user_id, content),
        ).fetchone()
    return {
        "id": row["id"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def delete_journal_entry(user_id, entry_id):
    """Delete an entry only when it belongs to the account."""
    if storage() == "memory":
        with _memory_journal_lock:
            before = len(_memory_journal_entries)
            _memory_journal_entries[:] = [
                row for row in _memory_journal_entries
                if not (row["user_id"] == user_id and row["id"] == entry_id)
            ]
        return len(_memory_journal_entries) != before
    with connect() as db:
        row = db.execute(
            "DELETE FROM public.journal_entries"
            " WHERE id = %s AND user_id = %s"
            " RETURNING id",
            (entry_id, user_id),
        ).fetchone()
    return row is not None


def storage():
    """'postgresql' when a database URL is available, otherwise 'memory'."""
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
        raise RuntimeError("A PostgreSQL URL is configured, but psycopg is not installed: "
                           'run  pip install "psycopg[binary]"')
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
# Memory storage (used when no database URL is configured)
# ---------------------------------------------------------------------------
_memory_lock = threading.Lock()
_memory_readings = []        # the readings, in the order they were stored
_memory_next_id = 1
_memory_journal_lock = threading.Lock()
_memory_journal_entries = []
_memory_journal_next_id = 1


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


def add_readings(user_id, readings):
    """Store readings (dictionaries with type, value, ts and optional fields).
    Returns how many readings are stored in total."""
    global _memory_next_id
    if storage() == "memory":
        with _memory_lock:
            for r in readings:
                row = {
                    "id": _memory_next_id,
                    "user_id": user_id,
                    "type": r["type"],
                    "value": r["value"],
                    "ts": r["ts"],
                }
                _memory_next_id += 1
                for field in READING_FIELDS:
                    row[field] = r.get(field)
                row["is_test"] = _with_test_mark(r)
                _memory_readings.append(row)
            if len(_memory_readings) > MAX_MEMORY_READINGS:
                del _memory_readings[:-MAX_MEMORY_READINGS]
            return sum(r["user_id"] == user_id for r in _memory_readings)

    rows = [(user_id, r["type"], r["value"], r["ts"],
             r.get("device_id"), r.get("sequence"), r.get("uptime_ms"),
             _with_test_mark(r), r.get("flags"), r.get("battery"), r.get("buffered"))
            for r in readings]
    with connect() as db:
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO readings (user_id, type, value, ts, device_id, sequence, uptime_ms,"
                " is_test, flags, battery, buffered)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rows)
        return db.execute(
            "SELECT COUNT(*) AS n FROM readings WHERE user_id = %s",
            (user_id,),
        ).fetchone()["n"]


def recent_readings(user_id, hours=24, limit=20000):
    """The most recent readings for one account, oldest first."""
    if storage() == "memory":
        since = _since(hours)
        return [_as_dict(r) for r in _memory_select(
            lambda r: r["user_id"] == user_id and r["ts"] >= since, limit)]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM (SELECT * FROM readings WHERE user_id = %s AND ts >= %s"
            " ORDER BY ts DESC, id DESC LIMIT %s) AS recent ORDER BY ts, id",
            (user_id, _since(hours), limit)).fetchall()
    return [_as_dict(row) for row in rows]


def latest_device(user_id):
    """The most recently used bracelet for one account (or None)."""
    if storage() == "memory":
        rows = _memory_select(
            lambda r: r["user_id"] == user_id and r["type"] == "heart_rate")
        return rows[-1]["device_id"] if rows else None
    with connect() as db:
        row = db.execute(
            "SELECT device_id FROM readings WHERE user_id = %s AND type = 'heart_rate'"
            " ORDER BY ts DESC, id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["device_id"] if row else None


def heart_readings(user_id, device_id, hours=24):
    """
    The heart-rate readings of one bracelet, in the order the website received
    them from the bracelet (by "ts"). The website sends readings at the same
    time, so they can be stored slightly out of order; sorting by "ts" puts
    them back in the bracelet's order.
    """
    if storage() == "memory":
        since = _since(hours)
        return [_as_dict(r) for r in _memory_select(
            lambda r: r["user_id"] == user_id and r["type"] == "heart_rate"
            and r["ts"] >= since and r["device_id"] == device_id)]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM readings WHERE user_id = %s AND type = 'heart_rate' AND ts >= %s"
            " AND device_id IS NOT DISTINCT FROM %s ORDER BY ts, id",
            (user_id, _since(hours), device_id)).fetchall()
    return [_as_dict(row) for row in rows]


def heart_readings_until_latest(user_id, device_id, seconds):
    """
    The heart-rate readings of one bracelet that arrived in the last `seconds`
    seconds before its LATEST reading (not before now), so the charts can also
    show data from yesterday. Ordered like heart_readings.
    """
    if storage() == "memory":
        rows = _memory_select(
            lambda r: r["user_id"] == user_id and r["type"] == "heart_rate"
            and r["device_id"] == device_id)
        if not rows:
            return []
        start = max(r["ts"] for r in rows) - seconds
        return [_as_dict(r) for r in rows if r["ts"] >= start]
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM readings WHERE user_id = %s AND type = 'heart_rate'"
            " AND device_id IS NOT DISTINCT FROM %s"
            " AND ts >= (SELECT max(ts) FROM readings WHERE user_id = %s AND type = 'heart_rate'"
            "            AND device_id IS NOT DISTINCT FROM %s) - %s"
            " ORDER BY ts, id",
            (user_id, device_id, user_id, device_id, seconds)).fetchall()
    return [_as_dict(row) for row in rows]


def clear(user_id):
    """Delete only the specified account's stored sensor readings."""
    if storage() == "memory":
        with _memory_lock:
            _memory_readings[:] = [
                reading for reading in _memory_readings
                if reading["user_id"] != user_id
            ]
        return
    with connect() as db:
        db.execute("DELETE FROM readings WHERE user_id = %s", (user_id,))
