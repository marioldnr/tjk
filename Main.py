from flask import Flask, request, jsonify
import sqlite3
import hashlib
import secrets

from Datenbank import init_db, get_db_connection, DB_NAME  # nutzt eure DB-Config

APP_NAME = "tjf"
app = Flask(APP_NAME)


# ---------------- Security helpers ----------------
def hash_text(text: str) -> str:
    # SHA-256 Hash (für Schulprojekt ok, später wäre bcrypt/argon2 besser)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()


# ---------------- DB helper ----------------
def get_db():
    """
    Einheitlicher DB-Zugriff über Datenbank.py
    (row_factory ist dort gesetzt, foreign_keys ebenfalls)
    """
    return get_db_connection()


# ---------------- Routes ----------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"app": APP_NAME, "db": DB_NAME, "status": "running"})


@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    benutzername = (data.get("benutzername") or "").strip()
    email = (data.get("email") or "").strip()
    passwort = data.get("passwort") or ""
    pin = data.get("pin") or ""

    if not benutzername or not email or not passwort or not pin:
        return jsonify({"error": "benutzername, email, passwort, pin sind Pflicht"}), 400
    if not is_valid_pin(pin):
        return jsonify({"error": "PIN muss genau 4 Ziffern haben"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute(
            """
            INSERT INTO benutzer (benutzername, email, passwort_hash, pin_hash)
            VALUES (?, ?, ?, ?)
            """,
            (benutzername, email, hash_text(passwort), hash_text(pin)),
        )
        con.commit()
        return jsonify({"ok": True, "message": "Registrierung erfolgreich"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "Benutzername oder Email existiert schon"}), 409
    finally:
        con.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    benutzername = (data.get("benutzername") or "").strip()
    pin = data.get("pin") or ""

    if not benutzername or not pin:
        return jsonify({"error": "benutzername und pin sind Pflicht"}), 400
    if not is_valid_pin(pin):
        return jsonify({"error": "PIN muss genau 4 Ziffern haben"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT benutzer_id, pin_hash FROM benutzer WHERE benutzername = ?",
        (benutzername,),
    )
    row = cur.fetchone()
    con.close()

    if row is None:
        return jsonify({"error": "User nicht gefunden"}), 404
    if hash_text(pin) != row["pin_hash"]:
        return jsonify({"error": "Falscher PIN"}), 401

    return jsonify({"ok": True, "benutzer_id": row["benutzer_id"]})


@app.route("/api/pin/change", methods=["POST"])
def change_pin():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    old_pin = data.get("old_pin") or ""
    new_pin = data.get("new_pin") or ""

    if not benutzer_id or not old_pin or not new_pin:
        return jsonify({"error": "benutzer_id, old_pin, new_pin sind Pflicht"}), 400
    if not is_valid_pin(old_pin) or not is_valid_pin(new_pin):
        return jsonify({"error": "PIN muss genau 4 Ziffern haben"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT pin_hash FROM benutzer WHERE benutzer_id = ?", (benutzer_id,))
    row = cur.fetchone()
    if row is None:
        con.close()
        return jsonify({"error": "User nicht gefunden"}), 404

    if hash_text(old_pin) != row["pin_hash"]:
        con.close()
        return jsonify({"error": "Alter PIN ist falsch"}), 401

    cur.execute(
        "UPDATE benutzer SET pin_hash = ? WHERE benutzer_id = ?",
        (hash_text(new_pin), benutzer_id),
    )
    con.commit()
    con.close()
    return jsonify({"ok": True, "message": "PIN geändert"})


@app.route("/api/reset/request", methods=["POST"])
def reset_request():
    """
    Simpler Reset: man gibt email + purpose an und bekommt ein Token zurück.
    In echt würde man das Token per Mail schicken.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    purpose = (data.get("purpose") or "").strip()  # 'password' oder 'pin'

    if not email or purpose not in ("password", "pin"):
        return jsonify({"error": "email und purpose ('password' oder 'pin') sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT benutzer_id FROM benutzer WHERE email = ?", (email,))
    row = cur.fetchone()
    if row is None:
        con.close()
        return jsonify({"error": "Email nicht gefunden"}), 404

    token = secrets.token_urlsafe(24)
    cur.execute(
        """
        INSERT INTO reset_tokens (benutzer_id, token, purpose, expires_at)
        VALUES (?, ?, ?, datetime('now', '+15 minutes'))
        """,
        (row["benutzer_id"], token, purpose),
    )
    con.commit()
    con.close()

    return jsonify({"ok": True, "reset_token": token, "expires_in_minutes": 15})


@app.route("/api/reset/confirm", methods=["POST"])
def reset_confirm():
    data = request.get_json(silent=True) or {}
    token = data.get("token") or ""
    new_value = data.get("new_value") or ""

    if not token or not new_value:
        return jsonify({"error": "token und new_value sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT token_id, benutzer_id, purpose, used
        FROM reset_tokens
        WHERE token = ? AND expires_at > datetime('now')
        """,
        (token,),
    )
    row = cur.fetchone()

    if row is None:
        con.close()
        return jsonify({"error": "Token ungültig oder abgelaufen"}), 400
    if row["used"] == 1:
        con.close()
        return jsonify({"error": "Token wurde schon benutzt"}), 400

    if row["purpose"] == "pin":
        if not is_valid_pin(new_value):
            con.close()
            return jsonify({"error": "Neuer PIN muss genau 4 Ziffern haben"}), 400
        cur.execute(
            "UPDATE benutzer SET pin_hash = ? WHERE benutzer_id = ?",
            (hash_text(new_value), row["benutzer_id"]),
        )
    else:
        cur.execute(
            "UPDATE benutzer SET passwort_hash = ? WHERE benutzer_id = ?",
            (hash_text(new_value), row["benutzer_id"]),
        )

    cur.execute("UPDATE reset_tokens SET used = 1 WHERE token_id = ?", (row["token_id"],))
    con.commit()
    con.close()

    return jsonify({"ok": True, "message": f"{row['purpose']} wurde zurückgesetzt"})


if __name__ == "__main__":
    init_db()
    print(f"Starte {APP_NAME} mit Datenbank: {DB_NAME}")
    app.run(debug=True)