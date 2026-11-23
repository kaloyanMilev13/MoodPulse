import base64
import importlib
import math
import os
import re
import threading
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from advice import bp as advice_bp
import database
import cv2
import numpy as np
from flask import Flask, request, jsonify, send_file, redirect, g, session
from analytics_bridge import HISTORY_RANGES, latest_bracelet_history

DeepFace = None


def load_deepface():
    global DeepFace
    if DeepFace is None:
        DeepFace = importlib.import_module("deepface.DeepFace")
    return DeepFace


app = Flask(__name__, static_folder="static", static_url_path="")
app.config.update(
    SECRET_KEY=database.key_vault_secret(),
    SESSION_COOKIE_NAME="moodpulse_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get(
        "SESSION_COOKIE_SECURE", "false"
    ).lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
app.register_blueprint(advice_bp)

user_states = {}
MAX_SENSOR_BATCH = 500
SENSOR_TYPES = {"heart_rate", "skin_conductance"}
SENSOR_RANGES = {"skin_conductance": (0.01, 100)}
state_lock = threading.Lock()
model_lock = threading.Lock()
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_GENDERS = {"male", "female", "rather-not-say"}
CONSENT_NOTICE_VERSION = "2026-11-24"
password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
DUMMY_PASSWORD_HASH = password_hasher.hash("moodpulse-dummy-password")
PUBLIC_ENDPOINTS = {
    "login",
    "health",
    "auth_signup",
    "auth_login",
    "advice.advice_health",
    "static",
}

NO_FACE = "No Face Detected"
MULTI = "More than one person"

def _user_state(user_id):
    return user_states.setdefault(user_id, {
        "logs": [],
        "sensor_session_active": False,
        "session": {
            "active": False,
            "started_at": None,
            "ended_at": None,
            "resumed_at": None,
            "duration_seconds": 0,
        },
    })


def _same_origin_request():
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin or origin == "null":
        return False
    source = urlsplit(origin)
    target = urlsplit(request.host_url)
    return source.scheme == target.scheme and source.netloc == target.netloc


@app.before_request
def enforce_login_and_same_origin():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if not _same_origin_request():
            return jsonify({"status": "error", "message": "Request origin was rejected."}), 403

    if request.endpoint in PUBLIC_ENDPOINTS and not (
        request.endpoint == "static" and request.path == "/advisor.html"
    ):
        return None

    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        if request.path.startswith("/api/"):
            return jsonify({"status": "error", "message": "Authentication required."}), 401
        return redirect("/login")

    g.user_id = user_id


def warm_up():
    """Load/download the models once at startup so the first real frame is fast."""
    try:
        deepface = load_deepface()
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        deepface.analyze(
            img_path=blank,
            actions=["emotion"],
            detector_backend="yunet",
            enforce_detection=False,
        )
        print("Models loaded.", flush=True)
    except Exception as e:
        print(f"Warm-up failed (will retry on first request): {e}", flush=True)


warm_up()


DATABASE_ERROR = None
try:
    database.init()
    print(f"Bracelet readings are stored in: {database.storage()}", flush=True)
except Exception as e:
    DATABASE_ERROR = f"Database not available: {type(e).__name__}: {e}"
    print(DATABASE_ERROR, flush=True)


def database_unavailable():
    global DATABASE_ERROR
    if DATABASE_ERROR:
        try:
            database.init()
            DATABASE_ERROR = None
            print(f"Bracelet readings are stored in: {database.storage()}", flush=True)
        except Exception as e:
            DATABASE_ERROR = f"Database not available: {type(e).__name__}: {e}"
            return jsonify({"status": "error", "message": DATABASE_ERROR}), 503
    return None


@app.route("/")
@app.route("/login")
def login():
    return send_file(Path(app.root_path) / "Login.html")


def _account_payload(user):
    consent_at = user.get("data_processing_consent_at")
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "age": user.get("age"),
        "gender": user.get("gender"),
        "height_cm": user.get("height_cm"),
        "weight_kg": user.get("weight_kg"),
        "data_processing_consent": {
            "given": user.get("data_processing_consent", False),
            "accepted_at": consent_at.isoformat() if consent_at else None,
            "notice_version": user.get("data_processing_consent_version"),
        },
    }


@app.route("/api/auth/signup", methods=["POST"])
def auth_signup():
    error = database_unavailable()
    if error:
        return error

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "JSON object expected."}), 400
    if data.get("data_processing_consent") is not True:
        return jsonify({
            "status": "error",
            "message": "Consent to the data processing described above is required to create an account.",
        }), 400

    name = data.get("username")
    email = data.get("email")
    password = data.get("password")
    confirmation = data.get("confirm_password")
    gender = data.get("gender")
    try:
        age = int(data.get("age"))
    except (TypeError, ValueError):
        age = None

    if not isinstance(name, str) or not 3 <= len(name.strip()) <= 100:
        return jsonify({"status": "error", "message": "Username must be 3 to 100 characters."}), 400
    name = name.strip()
    if not isinstance(email, str):
        return jsonify({"status": "error", "message": "Enter a valid email address."}), 400
    email = email.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return jsonify({"status": "error", "message": "Enter a valid email address."}), 400
    if not isinstance(password, str) or len(password) < 8:
        return jsonify({"status": "error", "message": "Password must be at least 8 characters."}), 400
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        return jsonify({"status": "error", "message": "Password contains unsupported characters."}), 400
    if len(password_bytes) > 1024:
        return jsonify({"status": "error", "message": "Password must be at most 1024 UTF-8 bytes."}), 400
    if confirmation is not None and confirmation != password:
        return jsonify({"status": "error", "message": "Passwords do not match."}), 400
    if age is None or not 1 <= age <= 120:
        return jsonify({"status": "error", "message": "Age must be between 1 and 120."}), 400
    if not isinstance(gender, str) or gender not in ALLOWED_GENDERS:
        return jsonify({"status": "error", "message": "Select a valid gender option."}), 400

    profile = {}
    for field, maximum in (("height_cm", 300), ("weight_kg", 500)):
        value = data.get(field)
        if value in (None, ""):
            profile[field] = None
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = float("nan")
        if not math.isfinite(number) or not 1 <= number <= maximum:
            return jsonify({
                "status": "error",
                "message": f"{field.replace('_', ' ')} must be between 1 and {maximum}.",
            }), 400
        profile[field] = number

    try:
        password_hash = password_hasher.hash(password)
    except Exception:
        app.logger.exception("Password hashing failed.")
        return jsonify({"status": "error", "message": "Account creation is unavailable. Try again later."}), 503
    try:
        user = database.create_user(
            name,
            email,
            password_hash,
            age,
            gender,
            profile["height_cm"],
            profile["weight_kg"],
            CONSENT_NOTICE_VERSION,
        )
    except Exception:
        app.logger.exception("Account creation failed.")
        return jsonify({"status": "error", "message": "Account creation is unavailable. Try again later."}), 503
    if user is None:
        return jsonify({"status": "error", "message": "An account with that email already exists."}), 409

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"status": "success", "user": _account_payload(user)}), 201


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    error = database_unavailable()
    if error:
        return error
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "JSON object expected."}), 400

    email = data.get("email")
    password = data.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({"status": "error", "message": "Invalid email or password."}), 401
    email = email.strip().lower()
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError:
        return jsonify({"status": "error", "message": "Invalid email or password."}), 401
    if len(password_bytes) > 1024:
        return jsonify({"status": "error", "message": "Invalid email or password."}), 401
    try:
        user = database.user_by_email(email)
    except Exception:
        app.logger.exception("Account lookup failed.")
        return jsonify({"status": "error", "message": "Login is unavailable. Try again later."}), 503

    stored_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    if user and not stored_hash.startswith("$argon2id$"):
        try:
            password_hasher.verify(DUMMY_PASSWORD_HASH, password)
        except VerifyMismatchError:
            pass
        app.logger.warning("Account %s uses an unsupported password hash and needs a reset.", user["id"])
        return jsonify({"status": "error", "message": "Invalid email or password. Reset the password to continue."}), 401
    try:
        password_matches = password_hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        password_matches = False
    except (InvalidHashError, VerificationError, ValueError, TypeError):
        app.logger.error("Stored password hash is invalid.")
        return jsonify({"status": "error", "message": "Login is unavailable. Contact support."}), 503
    if not user or not password_matches:
        return jsonify({"status": "error", "message": "Invalid email or password."}), 401

    if password_hasher.check_needs_rehash(stored_hash):
        try:
            database.update_password_hash(user["id"], password_hasher.hash(password))
        except Exception:
            app.logger.exception("Could not upgrade the account password hash.")
            return jsonify({"status": "error", "message": "Login is temporarily unavailable. Try again later."}), 503

    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"status": "success", "user": _account_payload(user)})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.clear()
    return jsonify({"status": "success"})


@app.route("/api/auth/me")
def auth_me():
    error = database_unavailable()
    if error:
        return error
    user = database.user_by_id(g.user_id)
    if user is None:
        session.clear()
        return jsonify({"status": "error", "message": "Account no longer exists."}), 401
    return jsonify({"status": "success", "user": _account_payload(user)})


@app.route("/api/journal", methods=["GET", "POST"])
def journal_route():
    error = database_unavailable()
    if error:
        return error

    if request.method == "GET":
        try:
            entries = database.journal_entries(g.user_id)
        except Exception:
            app.logger.exception("Could not load journal entries.")
            return jsonify({"status": "error", "message": "Could not load journal entries."}), 503
        return jsonify({
            "status": "success",
            "entries": [
                {
                    "id": entry["id"],
                    "note": entry["content"],
                    "date": entry["created_at"],
                    "demo": False,
                }
                for entry in entries
            ],
        })

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "JSON object expected."}), 400
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        return jsonify({"status": "error", "message": "Write something before saving."}), 400
    content = content.strip()
    if len(content) > 10000:
        return jsonify({"status": "error", "message": "Journal entries may contain at most 10000 characters."}), 400
    try:
        entry = database.create_journal_entry(g.user_id, content)
    except Exception:
        app.logger.exception("Could not save journal entry.")
        return jsonify({"status": "error", "message": "Could not save the journal entry."}), 503
    return jsonify({
        "status": "success",
        "entry": {
            "id": entry["id"],
            "note": entry["content"],
            "date": entry["created_at"],
            "demo": False,
        },
    }), 201


@app.route("/api/journal/<int:entry_id>", methods=["DELETE"])
def delete_journal_route(entry_id):
    error = database_unavailable()
    if error:
        return error
    try:
        deleted = database.delete_journal_entry(g.user_id, entry_id)
    except Exception:
        app.logger.exception("Could not delete journal entry.")
        return jsonify({"status": "error", "message": "Could not delete the journal entry."}), 503
    if not deleted:
        return jsonify({"status": "error", "message": "Journal entry not found."}), 404
    return jsonify({"status": "success"})


@app.route("/dashboard")
def index():
    return send_file(Path(app.root_path) / "Index.html")


@app.route("/index.html")
def legacy_index():
    return redirect("/dashboard", code=301)


@app.route("/health")
def health():
    error = database_unavailable()
    if error:
        return error
    return jsonify({
        "status": "ok",
        "storage": database.storage(),
        "database": DATABASE_ERROR is None,
    })


def session_info(state):
    """Must be called with state_lock held."""
    current = state["session"]
    duration = current["duration_seconds"]
    if current["active"] and current["resumed_at"]:
        duration += time.time() - current["resumed_at"]
    return {
        "active": current["active"],
        "started_at": current["started_at"],
        "duration_seconds": int(duration),
    }


@app.route("/continue-session", methods=["POST"])
def continue_session():
    with state_lock:
        state = _user_state(g.user_id)
        current = state["session"]
        already = current["active"]
        if not already:
            now = time.time()
            if current["started_at"] is None:
                state["logs"].clear()
                current["started_at"] = now
                current["duration_seconds"] = 0
            current["active"] = True
            current["ended_at"] = None
            current["resumed_at"] = now
        info = session_info(state)
    return jsonify({"status": "success", "already_active": already, **info})


@app.route("/stop-session", methods=["POST"])
def stop_session():
    with state_lock:
        state = _user_state(g.user_id)
        current = state["session"]
        if current["active"]:
            now = time.time()
            if current["resumed_at"] is not None:
                current["duration_seconds"] += now - current["resumed_at"]
            current["active"] = False
            current["ended_at"] = now
            current["resumed_at"] = None
        info = session_info(state)
    return jsonify({"status": "success", **info})


@app.route("/session-status", methods=["GET"])
def session_status():
    with state_lock:
        info = session_info(_user_state(g.user_id))
    return jsonify({"status": "success", **info})


@app.route("/api/sensor-session", methods=["GET", "POST"])
def sensor_session_route():
    if request.method == "GET":
        with state_lock:
            active = _user_state(g.user_id)["sensor_session_active"]
        return jsonify({"status": "success", "active": active})

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
        return jsonify({"status": "error", "message": "'active' must be a boolean"}), 400

    with state_lock:
        state = _user_state(g.user_id)
        state["sensor_session_active"] = data["active"]
        active = state["sensor_session_active"]
    return jsonify({"status": "success", "active": active})


@app.route("/api/sensors", methods=["GET", "POST"])
def sensor_readings_route():
    error = database_unavailable()
    if error:
        return error

    if request.method == "GET":
        readings = database.recent_readings(g.user_id, hours=24)
        return jsonify({"status": "success", "readings": readings})

    with state_lock:
        if not _user_state(g.user_id)["sensor_session_active"]:
            return jsonify({"status": "inactive", "message": "Bracelet readings are not active"}), 409

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error", "message": "JSON object expected"}), 400

    batch = data.get("readings", [data])
    if not isinstance(batch, list) or not batch:
        return jsonify({"status": "error", "message": "'readings' must be a non-empty list"}), 400
    if len(batch) > MAX_SENSOR_BATCH:
        return jsonify({"status": "error", "message": f"At most {MAX_SENSOR_BATCH} readings may be sent at once"}), 413

    accepted = []
    for index, reading in enumerate(batch):
        if not isinstance(reading, dict):
            return jsonify({"status": "error", "message": f"Reading {index} must be an object"}), 400

        kind = reading.get("type")
        if not isinstance(kind, str) or kind not in SENSOR_TYPES:
            return jsonify({"status": "error", "message": f"Reading {index} has an unsupported sensor type"}), 400

        try:
            value = float(reading.get("value"))
            timestamp = float(reading.get("ts", time.time()))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": f"Reading {index} needs numeric value and timestamp"}), 400
        if not math.isfinite(value) or not math.isfinite(timestamp):
            return jsonify({"status": "error", "message": f"Reading {index} values must be finite numbers"}), 400

        if kind in SENSOR_RANGES:
            low, high = SENSOR_RANGES[kind]
            if not low <= value <= high:
                return jsonify({"status": "error", "message": f"Reading {index} value is outside the allowed range for {kind}"}), 400

        accepted_reading = {"type": kind, "value": value, "ts": timestamp}
        metadata_ranges = {
            "device_id": (0, 0xFFFF),
            "sequence": (0, 0xFFFFFFFF),
            "uptime_ms": (0, 0xFFFFFFFF),
            "battery": (0, 256), # change to 100 later
            "flags": (0, 0xFF),
        }
        for field, (minimum, maximum) in metadata_ranges.items():
            if field not in reading:
                continue
            metadata_value = reading[field]
            if (
                isinstance(metadata_value, bool)
                or not isinstance(metadata_value, int)
                or not minimum <= metadata_value <= maximum
            ):
                return jsonify({"status": "error", "message": f"Reading {index} has invalid {field} metadata"}), 400
            accepted_reading[field] = metadata_value
        if "buffered" in reading:
            if not isinstance(reading["buffered"], bool):
                return jsonify({"status": "error", "message": f"Reading {index} has invalid buffered metadata"}), 400
            accepted_reading["buffered"] = reading["buffered"]
        accepted.append(accepted_reading)

    with state_lock:
        if not _user_state(g.user_id)["sensor_session_active"]:
            return jsonify({"status": "inactive", "message": "Bracelet readings are not active"}), 409
    try:
        total = database.add_readings(g.user_id, accepted)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Could not store the readings: {e}"}), 503
    return jsonify({"status": "success", "accepted": len(accepted), "total": total}), 201


@app.route("/api/heart-summary", methods=["GET"])
def heart_summary_route():
    error = database_unavailable()
    if error:
        return error
    history = latest_bracelet_history(g.user_id, 1440)
    summary = dict(history["summary"])
    summary["device_id"] = history["device_id"]
    with state_lock:
        active = _user_state(g.user_id)["sensor_session_active"]
    return jsonify({
        "status": "success",
        "active": active,
        "summary": summary,
        "per_minute": history["per_minute"][-60:],
    })


@app.route("/api/heart-history", methods=["GET"])
def heart_history_route():
    error = database_unavailable()
    if error:
        return error
    raw_minutes = request.args.get("minutes")
    try:
        minutes = int(raw_minutes) if raw_minutes is not None else 15
    except ValueError:
        return jsonify({
            "status": "error",
            "message": f"minutes must be one of {list(HISTORY_RANGES)}",
        }), 400
    if minutes not in HISTORY_RANGES:
        return jsonify({
            "status": "error",
            "message": f"minutes must be one of {list(HISTORY_RANGES)}",
        }), 400
    return jsonify({
        "status": "success",
        **latest_bracelet_history(g.user_id, minutes),
    })


@app.route("/analyze-emotion", methods=["POST"])
def analyze_emotion():
    with state_lock:
        active = _user_state(g.user_id)["session"]["active"]
    if not active:
        return jsonify({"status": "inactive", "message": "No active session"}), 409

    try:
        deepface = load_deepface()
    except (ImportError, OSError) as e:
        print(f"DeepFace unavailable: {e}", flush=True)
        return jsonify({
            "status": "error",
            "message": "Emotion analysis is unavailable because DeepFace could not be loaded.",
        }), 503

    try:
        data = request.get_json(silent=True)
        if not data or "image" not in data:
            return jsonify({"status": "error", "message": "No image data received"}), 400

        img_str = data["image"]
        if "," in img_str:
            img_str = img_str.split(",")[1]

        img_bytes = base64.b64decode(img_str)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({"status": "error", "message": "Failed to decode frame image"}), 400

        with model_lock:
            predictions = deepface.analyze(
                img_path=frame,
                actions=["emotion"],
                detector_backend="yunet",
                enforce_detection=False,
            )

        if isinstance(predictions, dict):
            face_list = [predictions]
        elif isinstance(predictions, list):
            face_list = predictions
        else:
            face_list = []

        valid_faces = []
        for face in face_list:
            region = face.get("region", {})
            if region.get("w", 0) < frame.shape[1] - 10:
                valid_faces.append(face)

        if len(valid_faces) == 0:
            current_status = NO_FACE
        elif len(valid_faces) > 1:
            current_status = MULTI
        else:
            current_status = valid_faces[0].get("dominant_emotion", "neutral")

        if current_status not in (NO_FACE, MULTI):
            with state_lock:
                state = _user_state(g.user_id)
                if state["session"]["active"]:
                    state["logs"].append({
                        "timestamp": time.time(),
                        "emotion": current_status,
                    })

        print(f"Status: {current_status}", flush=True)
        return jsonify({"status": "success", "emotion": current_status})

    except Exception as e:
        print(f"Backend Error: {e}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/emotion-summary", methods=["GET"])
def get_summary():
    with state_lock:
        state = _user_state(g.user_id)
        emotions = [log["emotion"] for log in state["logs"]]
        info = session_info(state)

    if not emotions:
        return jsonify({"status": "success", "summary": {}, "total_records": 0, **info})

    dominant_emotion = Counter(emotions).most_common(1)[0][0]
    total = len(emotions)
    counts = Counter(emotions)
    percentages = {e: round(c / total * 100, 1) for e, c in counts.items()}
    return jsonify({"status": "success", "summary": percentages, "total_records": total, "dominant_emotion": dominant_emotion, **info})


@app.route("/reset-session", methods=["POST"])
def reset_session():
    error = database_unavailable()
    if error:
        return error
    database.clear(g.user_id)
    with state_lock:
        state = _user_state(g.user_id)
        current = state["session"]
        state["logs"].clear()
        if current["active"]:
            now = time.time()
            current["started_at"] = now
            current["resumed_at"] = now
            current["duration_seconds"] = 0
        else:
            current["started_at"] = None
            current["ended_at"] = None
            current["resumed_at"] = None
            current["duration_seconds"] = 0
    return jsonify({"status": "success", "message": "Session history cleared"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
