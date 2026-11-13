import base64
import math
import threading
import time
from collections import Counter
from advice import bp as advice_bp
import cv2
import numpy as np
from deepface import DeepFace
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app, resources={r"/*": {"origins": "*"}})
app.register_blueprint(advice_bp)

session_logs = []
sensor_readings = []  
sensor_session_active = False
MAX_SENSOR_READINGS = 20000
MAX_SENSOR_BATCH = 500
SENSOR_RANGES = {
    "heart_rate": (30, 220),
    "skin_conductance": (0.01, 100),
}
session = {
    "active": False,
    "started_at": None,
    "ended_at": None,
    "resumed_at": None,
    "duration_seconds": 0,
}
state_lock = threading.Lock()
model_lock = threading.Lock()

NO_FACE = "No Face Detected"
MULTI = "More than one person"


def warm_up():
    """Load/download the models once at startup so the first real frame is fast."""
    try:
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        DeepFace.analyze(
            img_path=blank,
            actions=["emotion"],
            detector_backend="yunet",
            enforce_detection=False,
        )
        print("Models loaded.", flush=True)
    except Exception as e:
        print(f"Warm-up failed (will retry on first request): {e}", flush=True)


warm_up()


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


def session_info():
    """Must be called with state_lock held."""
    duration = session["duration_seconds"]
    if session["active"] and session["resumed_at"]:
        duration += time.time() - session["resumed_at"]
    return {
        "active": session["active"],
        "started_at": session["started_at"],
        "duration_seconds": int(duration),
    }


@app.route("/continue-session", methods=["POST"])
def continue_session():
    with state_lock:
        already = session["active"]
        if not already:
            now = time.time()
            if session["started_at"] is None:
                session_logs.clear()
                session["started_at"] = now
                session["duration_seconds"] = 0
            session["active"] = True
            session["ended_at"] = None
            session["resumed_at"] = now
        info = session_info()
    return jsonify({"status": "success", "already_active": already, **info})


@app.route("/stop-session", methods=["POST"])
def stop_session():
    with state_lock:
        if session["active"]:
            now = time.time()
            if session["resumed_at"] is not None:
                session["duration_seconds"] += now - session["resumed_at"]
            session["active"] = False
            session["ended_at"] = now
            session["resumed_at"] = None
        info = session_info()
    return jsonify({"status": "success", **info})


@app.route("/session-status", methods=["GET"])
def session_status():
    with state_lock:
        info = session_info()
    return jsonify({"status": "success", **info})


@app.route("/api/sensor-session", methods=["GET", "POST"])
def sensor_session_route():
    global sensor_session_active

    if request.method == "GET":
        with state_lock:
            active = sensor_session_active
        return jsonify({"status": "success", "active": active})

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
        return jsonify({"status": "error", "message": "'active' must be a boolean"}), 400

    with state_lock:
        sensor_session_active = data["active"]
    return jsonify({"status": "success", "active": sensor_session_active})


@app.route("/api/sensors", methods=["GET", "POST"])
def sensor_readings_route():
    if request.method == "GET":
        with state_lock:
            readings = list(sensor_readings)
        return jsonify({"status": "success", "readings": readings})

    with state_lock:
        if not sensor_session_active:
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
        if not isinstance(kind, str) or kind not in SENSOR_RANGES:
            return jsonify({"status": "error", "message": f"Reading {index} has an unsupported sensor type"}), 400

        try:
            value = float(reading.get("value"))
            timestamp = float(reading.get("ts", time.time()))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": f"Reading {index} needs numeric value and timestamp"}), 400
        if not math.isfinite(value) or not math.isfinite(timestamp):
            return jsonify({"status": "error", "message": f"Reading {index} values must be finite numbers"}), 400

        low, high = SENSOR_RANGES[kind]
        if not low <= value <= high:
            return jsonify({"status": "error", "message": f"Reading {index} value is outside the allowed range for {kind}"}), 400
        accepted.append({"type": kind, "value": value, "ts": timestamp})

    with state_lock:
        if not sensor_session_active:
            return jsonify({"status": "inactive", "message": "Bracelet readings are not active"}), 409
        sensor_readings.extend(accepted)
        if len(sensor_readings) > MAX_SENSOR_READINGS:
            del sensor_readings[:-MAX_SENSOR_READINGS]
        total = len(sensor_readings)
    return jsonify({"status": "success", "accepted": len(accepted), "total": total}), 201


@app.route("/analyze-emotion", methods=["POST", "OPTIONS"])
def analyze_emotion():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    with state_lock:
        active = session["active"]
    if not active:
        return jsonify({"status": "inactive", "message": "No active session"}), 409

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
            predictions = DeepFace.analyze(
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
                if session["active"]:
                    session_logs.append({"timestamp": time.time(), "emotion": current_status})

        print(f"Status: {current_status}", flush=True)
        return jsonify({"status": "success", "emotion": current_status})

    except Exception as e:
        print(f"Backend Error: {e}", flush=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/emotion-summary", methods=["GET"])
def get_summary():
    with state_lock:
        emotions = [log["emotion"] for log in session_logs]
        info = session_info()

    if not emotions:
        return jsonify({"status": "success", "summary": {}, "total_records": 0, **info})

    dominant_emotion = Counter(emotions).most_common(1)[0][0]
    total = len(emotions)
    counts = Counter(emotions)
    percentages = {e: round(c / total * 100, 1) for e, c in counts.items()}
    return jsonify({"status": "success", "summary": percentages, "total_records": total, "dominant_emotion": dominant_emotion, **info})


@app.route("/reset-session", methods=["POST"])
def reset_session():
    with state_lock:
        session_logs.clear()
        sensor_readings.clear()
        if session["active"]:
            now = time.time()
            session["started_at"] = now
            session["resumed_at"] = now
            session["duration_seconds"] = 0
        else:
            session["started_at"] = None
            session["ended_at"] = None
            session["resumed_at"] = None
            session["duration_seconds"] = 0
    return jsonify({"status": "success", "message": "Session history cleared"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
