
from flask import Blueprint, request, jsonify, session
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

import database

auth_bp = Blueprint("auth", __name__)
password_hasher = PasswordHasher()


@auth_bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if not all(isinstance(v, str) for v in (name, email, password)):
        return jsonify({"error": "Name, email and password are required"}), 400

    name = name.strip()
    email = email.strip().lower()

    if not name or len(name) > 100 or not email or len(email) > 255:
        return jsonify({"error": "Invalid name or email"}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must contain at least 8 characters"}), 400

    password_hash = password_hasher.hash(password)

    try:
        with database.connect() as db:
            row = db.execute(
                """
                INSERT INTO users (name, email, password_hash)
                VALUES (%s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                RETURNING id, name, email
                """,
                (name, email, password_hash)
            ).fetchone()

        if row is None:
            return jsonify({"error": "Email already registered"}), 409

        return jsonify({
            "message": "Account created successfully",
            "user": row
        }), 201

    except Exception:
        return jsonify({"error": "Registration could not be completed"}), 500

@auth_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid request"}), 400

    email = data.get("email")
    password = data.get("password")

    if not isinstance(email, str) or not isinstance(password, str):
        return jsonify({"error": "Email and password are required"}), 400

    email = email.strip().lower()

    try:
        with database.connect() as db:
            user = db.execute(
                """
                SELECT id, name, email, password_hash
                FROM users
                WHERE email = %s
                """,
                (email,)
            ).fetchone()

        if user is None:
            return jsonify({"error": "Invalid email or password"}), 401

        try:
            password_hasher.verify(user["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            return jsonify({"error": "Invalid email or password"}), 401

        session.clear()
        session["user_id"] = user["id"]

        return jsonify({
            "message": "Login successful",
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"]
            }
        }), 200

    except Exception:
        return jsonify({"error": "Login could not be completed"}), 500
@auth_bp.route("/api/me", methods=["GET"])
def current_user():
    user_id = session.get("user_id")

    if user_id is None:
        return jsonify({"error": "Not authenticated"}), 401

    try:
        with database.connect() as db:
            user = db.execute(
                """
                SELECT id, name, email
                FROM users
                WHERE id = %s
                """,
                (user_id,)
            ).fetchone()

        if user is None:
            session.clear()
            return jsonify({"error": "Not authenticated"}), 401

        return jsonify({"user": user}), 200

    except Exception:
        return jsonify({"error": "Could not retrieve user"}), 500
@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()

    return jsonify({
        "message": "Logout successful"
    }), 200