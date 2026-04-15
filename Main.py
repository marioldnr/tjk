from flask import Flask, request, jsonify, send_from_directory
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta

from Datenbank import init_db, get_db_connection, DB_NAME

APP_NAME = "tjf"
app = Flask(__name__, static_folder="static")


# ---------------- Grundfunktionen ----------------
def get_db():
    return get_db_connection()

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()

def add_missing_structures():
    """
    Ergänzt fehlende Spalten/Tabellen, ohne bestehende Daten zu löschen.
    """
    con = get_db()
    cur = con.cursor()

    # Login-Sperre
    try:
        cur.execute("ALTER TABLE benutzer ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0;")
    except sqlite3.OperationalError:
        pass
    try:
        cur.execute("ALTER TABLE benutzer ADD COLUMN lock_until TEXT;")
    except sqlite3.OperationalError:
        pass

    # Playlist mit Reihenfolge
    cur.execute("""
    CREATE TABLE IF NOT EXISTS playlist (
      benutzer_id INTEGER NOT NULL,
      titel_id INTEGER NOT NULL,
      position INTEGER NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (benutzer_id, titel_id),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id),
      FOREIGN KEY (titel_id) REFERENCES titel(titel_id)
    );
    """)

    # Wunschliste Kategorien + Items
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wishlist_category (
      category_id INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE (benutzer_id, name),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS wishlist_item (
      benutzer_id INTEGER NOT NULL,
      titel_id INTEGER NOT NULL,
      category_id INTEGER,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      PRIMARY KEY (benutzer_id, titel_id),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id),
      FOREIGN KEY (titel_id) REFERENCES titel(titel_id),
      FOREIGN KEY (category_id) REFERENCES wishlist_category(category_id)
    );
    """)

    con.commit()
    con.close()

def normalize_playlist(con, benutzer_id: int):
    cur = con.cursor()
    cur.execute(
        "SELECT titel_id FROM playlist WHERE benutzer_id = ? ORDER BY position ASC, created_at ASC",
        (benutzer_id,),
    )
    titel_ids = [r["titel_id"] for r in cur.fetchall()]
    for idx, tid in enumerate(titel_ids, start=1):
        cur.execute(
            "UPDATE playlist SET position = ? WHERE benutzer_id = ? AND titel_id = ?",
            (idx, benutzer_id, tid),
        )

# ---------------- Basis ----------------
@app.route("/", methods=["GET"])
def home():
    return app.send_static_file("TyF.html")

# ---------------- Titel (A) ----------------
@app.route("/api/titel/add", methods=["POST"])
def titel_add():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    typ = (data.get("typ") or "").strip()  # z.B. Film/Serie/Game
    genre = (data.get("genre") or "").strip() or None
    erscheinungsjahr = data.get("erscheinungsjahr", None)
    beschreibung = (data.get("beschreibung") or "").strip() or None

    if not name or not typ:
        return jsonify({"error": "name und typ sind Pflicht"}), 400

    if erscheinungsjahr in ("", None):
        erscheinungsjahr = None
    else:
        try:
            erscheinungsjahr = int(erscheinungsjahr)
        except ValueError:
            return jsonify({"error": "erscheinungsjahr muss eine Zahl sein"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO titel (name, typ, genre, erscheinungsjahr, beschreibung)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, typ, genre, erscheinungsjahr, beschreibung),
    )
    con.commit()
    titel_id = cur.lastrowid
    con.close()
    return jsonify({"ok": True, "titel_id": titel_id}), 201

@app.route("/api/titel/list", methods=["GET"])
def titel_list():
    q = (request.args.get("q") or "").strip().lower()

    con = get_db()
    cur = con.cursor()
    if q:
        cur.execute(
            """
            SELECT titel_id, name, typ, genre, erscheinungsjahr, beschreibung
            FROM titel
            WHERE lower(name) LIKE ?
            ORDER BY name ASC
            """,
            (f"%{q}%",),
        )
    else:
        cur.execute(
            """
            SELECT titel_id, name, typ, genre, erscheinungsjahr, beschreibung
            FROM titel
            ORDER BY name ASC
            """
        )
    rows = cur.fetchall()
    con.close()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

@app.route("/api/titel/delete", methods=["POST"])
def titel_delete():
    data = request.get_json(silent=True) or {}
    titel_id = data.get("titel_id")
    if not titel_id:
        return jsonify({"error": "titel_id ist Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("DELETE FROM titel WHERE titel_id = ?", (titel_id,))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Titel wird noch verwendet (z.B. Playlist/Wunschliste/Bewertung)"}), 409

    con.close()
    return jsonify({"ok": True})

# ---------------- Einträge speichern + laden ----------------

@app.route("/api/eintrag/speichern", methods=["POST"])
def api_eintrag_speichern():
    """
    Speichert einen Eintrag in die DB:
    - titel (name, typ)
    - status (playlist oder wishlist)
    - optional: bewertung (rating)
    - optional: kritik (text)
    - playlist/wishlist Tabellen für Zusatzinfos
    """
    data = request.get_json(silent=True) or {}

    benutzer_id = data.get("benutzer_id")
    titel = (data.get("titel") or "").strip()
    typ = (data.get("typ") or "").strip()
    ziel_liste = (data.get("ziel_liste") or "").strip()  # "playlist" oder "wishlist"
    kategorie = (data.get("kategorie") or "").strip() or None
    rating = data.get("rating")  # optional
    kritik_text = (data.get("kritik") or "").strip() or None

    # --- Validierung (simpel) ---
    if not benutzer_id:
        return jsonify({"error": "benutzer_id fehlt"}), 400
    if not titel or not typ:
        return jsonify({"error": "titel und typ sind Pflicht"}), 400
    if ziel_liste not in ("playlist", "wishlist"):
        return jsonify({"error": "ziel_liste muss playlist oder wishlist sein"}), 400

    # rating optional, aber wenn gesetzt -> 1..5
    if rating in ("", None):
        rating = None
    else:
        try:
            rating = int(rating)
        except ValueError:
            return jsonify({"error": "rating muss Zahl 1..5 sein"}), 400
        if rating < 1 or rating > 5:
            return jsonify({"error": "rating muss 1..5 sein"}), 400

    con = get_db()
    cur = con.cursor()

    # 1) Titel finden oder erstellen
    cur.execute(
        "SELECT titel_id FROM titel WHERE lower(name) = lower(?) AND typ = ?",
        (titel, typ),
    )
    row = cur.fetchone()
    if row:
        titel_id = row["titel_id"]
    else:
        cur.execute(
            "INSERT INTO titel (name, typ, genre, erscheinungsjahr, beschreibung) VALUES (?, ?, NULL, NULL, NULL)",
            (titel, typ),
        )
        titel_id = cur.lastrowid

    # 2) status setzen (playlist => gesehen=1, wishlist => wunschliste=1)
    gesehen = 1 if ziel_liste == "playlist" else 0
    wunschliste = 1 if ziel_liste == "wishlist" else 0

    cur.execute(
        """
        INSERT INTO status (benutzer_id, titel_id, gesehen, wunschliste)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(benutzer_id, titel_id)
        DO UPDATE SET gesehen = excluded.gesehen, wunschliste = excluded.wunschliste, datum = datetime('now')
        """,
        (benutzer_id, titel_id, gesehen, wunschliste),
    )

    # 3) Bewertung optional speichern
    if rating is not None:
        cur.execute(
            """
            INSERT INTO bewertung (benutzer_id, titel_id, rating)
            VALUES (?, ?, ?)
            ON CONFLICT(benutzer_id, titel_id)
            DO UPDATE SET rating = excluded.rating, datum = datetime('now')
            """,
            (benutzer_id, titel_id, rating),
        )

    # 4) Kritik optional speichern
    if kritik_text:
        cur.execute(
            """
            INSERT INTO kritik (benutzer_id, titel_id, text)
            VALUES (?, ?, ?)
            ON CONFLICT(benutzer_id, titel_id)
            DO UPDATE SET text = excluded.text, datum = datetime('now')
            """,
            (benutzer_id, titel_id, kritik_text),
        )

    # 5) Playlist / Wishlist Zusatz-Tabellen
    if ziel_liste == "playlist":
        # ans Ende der Playlist hängen
        cur.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 AS next_pos FROM playlist WHERE benutzer_id = ?",
            (benutzer_id,),
        )
        next_pos = cur.fetchone()["next_pos"]
        cur.execute(
            """
            INSERT INTO playlist (benutzer_id, titel_id, position)
            VALUES (?, ?, ?)
            ON CONFLICT(benutzer_id, titel_id) DO UPDATE SET position = excluded.position
            """,
            (benutzer_id, titel_id, next_pos),
        )
    else:
        # Wunschliste: Kategorie optional
        category_id = None
        if kategorie:
            cur.execute(
                "INSERT OR IGNORE INTO wishlist_category (benutzer_id, name) VALUES (?, ?)",
                (benutzer_id, kategorie),
            )
            cur.execute(
                "SELECT category_id FROM wishlist_category WHERE benutzer_id = ? AND name = ?",
                (benutzer_id, kategorie),
            )
            category_id = cur.fetchone()["category_id"]

        cur.execute(
            """
            INSERT INTO wishlist_item (benutzer_id, titel_id, category_id)
            VALUES (?, ?, ?)
            ON CONFLICT(benutzer_id, titel_id) DO UPDATE SET category_id = excluded.category_id
            """,
            (benutzer_id, titel_id, category_id),
        )

    con.commit()
    con.close()
    return jsonify({"ok": True, "titel_id": titel_id}), 201


@app.route("/api/eintrag/liste", methods=["GET"])
def api_eintrag_liste():
    """
    Liefert alle Einträge eines Benutzers (für Playlist + Wunschliste Anzeige).
    """
    benutzer_id = request.args.get("benutzer_id", type=int)
    if not benutzer_id:
        return jsonify({"error": "benutzer_id fehlt"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        SELECT
          t.name AS titel,
          t.typ  AS typ,
          s.gesehen,
          s.wunschliste,
          b.rating,
          k.text AS kritik,
          wc.name AS kategorie,
          p.position
        FROM status s
        JOIN titel t ON t.titel_id = s.titel_id
        LEFT JOIN bewertung b ON b.benutzer_id = s.benutzer_id AND b.titel_id = s.titel_id
        LEFT JOIN kritik k    ON k.benutzer_id = s.benutzer_id AND k.titel_id = s.titel_id
        LEFT JOIN playlist p  ON p.benutzer_id = s.benutzer_id AND p.titel_id = s.titel_id
        LEFT JOIN wishlist_item wi ON wi.benutzer_id = s.benutzer_id AND wi.titel_id = s.titel_id
        LEFT JOIN wishlist_category wc ON wc.category_id = wi.category_id
        WHERE s.benutzer_id = ?
        ORDER BY
          CASE WHEN p.position IS NULL THEN 999999 ELSE p.position END ASC,
          t.name ASC
        """,
        (benutzer_id,),
    )

    items = [dict(r) for r in cur.fetchall()]
    con.close()

    # ziel_liste fürs Frontend ableiten
    for r in items:
        if r.get("gesehen") == 1:
            r["ziel_liste"] = "playlist"
        elif r.get("wunschliste") == 1:
            r["ziel_liste"] = "wishlist"
        else:
            r["ziel_liste"] = ""

    return jsonify({"ok": True, "items": items})

# ---------------- Benutzerkonto ----------------
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    benutzername = (data.get("benutzername") or "").strip()
    email = (data.get("email") or "").strip()
    pin = data.get("pin") or ""

    # PIN ist euer "Passwort" -> kein extra passwort Feld
    if not benutzername or not email or not pin:
        return jsonify({"error": "benutzername, email, pin sind Pflicht"}), 400
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
            # wir speichern den PIN-Hash in beiden Feldern
            (benutzername, email, hash_text(pin), hash_text(pin)),
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
        "SELECT benutzer_id, pin_hash, failed_login_attempts, lock_until FROM benutzer WHERE benutzername = ?",
        (benutzername,),
    )
    row = cur.fetchone()

    if row is None:
        con.close()
        return jsonify({"error": "User nicht gefunden"}), 404

    # Sperre prüfen
    lock_until = row["lock_until"]
    if lock_until:
        try:
            lock_dt = datetime.fromisoformat(lock_until)
            if lock_dt > datetime.now():
                con.close()
                return jsonify({"error": "Account gesperrt", "lock_until": lock_until}), 423
        except ValueError:
            pass

    if hash_text(pin) != row["pin_hash"]:
        attempts = int(row["failed_login_attempts"] or 0) + 1
        if attempts >= 5:
            lock_dt = datetime.now() + timedelta(minutes=10)
            cur.execute(
                "UPDATE benutzer SET failed_login_attempts = 0, lock_until = ? WHERE benutzer_id = ?",
                (lock_dt.isoformat(), row["benutzer_id"]),
            )
        else:
            cur.execute(
                "UPDATE benutzer SET failed_login_attempts = ? WHERE benutzer_id = ?",
                (attempts, row["benutzer_id"]),
            )
        con.commit()
        con.close()
        return jsonify({"error": "Falscher PIN"}), 401

    cur.execute(
        "UPDATE benutzer SET failed_login_attempts = 0, lock_until = NULL WHERE benutzer_id = ?",
        (row["benutzer_id"],),
    )
    con.commit()
    con.close()
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

    cur.execute("UPDATE benutzer SET pin_hash = ? WHERE benutzer_id = ?", (hash_text(new_pin), benutzer_id))
    con.commit()
    con.close()
    return jsonify({"ok": True, "message": "PIN geändert"})

@app.route("/api/reset/request", methods=["POST"])
def reset_request():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    purpose = (data.get("purpose") or "").strip()

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
        cur.execute("UPDATE benutzer SET pin_hash = ? WHERE benutzer_id = ?", (hash_text(new_value), row["benutzer_id"]))
    else:
        cur.execute("UPDATE benutzer SET passwort_hash = ? WHERE benutzer_id = ?", (hash_text(new_value), row["benutzer_id"]))

    cur.execute("UPDATE reset_tokens SET used = 1 WHERE token_id = ?", (row["token_id"],))
    con.commit()
    con.close()
    return jsonify({"ok": True, "message": f"{row['purpose']} wurde zurückgesetzt"})

# ---------------- Playlist ----------------
@app.route("/api/playlist/add", methods=["POST"])
def playlist_add():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT COALESCE(MAX(position), 0) AS maxpos FROM playlist WHERE benutzer_id = ?", (benutzer_id,))
    maxpos = cur.fetchone()["maxpos"]

    try:
        cur.execute(
            "INSERT INTO playlist (benutzer_id, titel_id, position) VALUES (?, ?, ?)",
            (benutzer_id, titel_id, int(maxpos) + 1),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Titel ist schon in der Playlist"}), 409

    con.close()
    return jsonify({"ok": True})

@app.route("/api/playlist/remove", methods=["POST"])
def playlist_remove():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM playlist WHERE benutzer_id = ? AND titel_id = ?", (benutzer_id, titel_id))
    normalize_playlist(con, int(benutzer_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/playlist/list", methods=["GET"])
def playlist_list():
    benutzer_id = request.args.get("benutzer_id", type=int)
    if not benutzer_id:
        return jsonify({"error": "benutzer_id ist Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        SELECT p.titel_id, p.position, t.name, t.typ, t.genre, t.erscheinungsjahr
        FROM playlist p
        LEFT JOIN titel t ON t.titel_id = p.titel_id
        WHERE p.benutzer_id = ?
        ORDER BY p.position ASC
        """,
        (benutzer_id,),
    )
    rows = cur.fetchall()
    con.close()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

@app.route("/api/playlist/move", methods=["POST"])
def playlist_move():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")
    new_position = data.get("new_position")

    if not benutzer_id or not titel_id or new_position is None:
        return jsonify({"error": "benutzer_id, titel_id, new_position sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute(
        "UPDATE playlist SET position = ? WHERE benutzer_id = ? AND titel_id = ?",
        (int(new_position), benutzer_id, titel_id),
    )
    normalize_playlist(con, int(benutzer_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ---------------- Wunschliste + Kategorien ----------------
@app.route("/api/wishlist/category/create", methods=["POST"])
def wishlist_category_create():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    name = (data.get("name") or "").strip()

    if not benutzer_id or not name:
        return jsonify({"error": "benutzer_id und name sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("INSERT INTO wishlist_category (benutzer_id, name) VALUES (?, ?)", (benutzer_id, name))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Kategorie existiert schon"}), 409

    con.close()
    return jsonify({"ok": True})

@app.route("/api/wishlist/category/update", methods=["POST"])
def wishlist_category_update():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    category_id = data.get("category_id")
    name = (data.get("name") or "").strip()

    if not benutzer_id or not category_id or not name:
        return jsonify({"error": "benutzer_id, category_id und name sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE wishlist_category SET name = ? WHERE category_id = ? AND benutzer_id = ?",
        (name, category_id, benutzer_id),
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/wishlist/category/delete", methods=["POST"])
def wishlist_category_delete():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    category_id = data.get("category_id")

    if not benutzer_id or not category_id:
        return jsonify({"error": "benutzer_id und category_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE wishlist_item SET category_id = NULL WHERE benutzer_id = ? AND category_id = ?",
        (benutzer_id, category_id),
    )
    cur.execute("DELETE FROM wishlist_category WHERE benutzer_id = ? AND category_id = ?", (benutzer_id, category_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/wishlist/add", methods=["POST"])
def wishlist_add():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")
    category_id = data.get("category_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO wishlist_item (benutzer_id, titel_id, category_id) VALUES (?, ?, ?)",
            (benutzer_id, titel_id, category_id),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Titel ist schon in der Wunschliste"}), 409

    con.close()
    return jsonify({"ok": True})

@app.route("/api/wishlist/remove", methods=["POST"])
def wishlist_remove():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM wishlist_item WHERE benutzer_id = ? AND titel_id = ?", (benutzer_id, titel_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/wishlist/list", methods=["GET"])
def wishlist_list():
    benutzer_id = request.args.get("benutzer_id", type=int)
    category_id = request.args.get("category_id", type=int)
    sort = (request.args.get("sort") or "name").strip().lower()     # name|created_at
    direction = (request.args.get("direction") or "asc").strip().lower()  # asc|desc

    if not benutzer_id:
        return jsonify({"error": "benutzer_id ist Pflicht"}), 400

    sort_sql = "t.name"
    if sort == "created_at":
        sort_sql = "w.created_at"

    dir_sql = "DESC" if direction == "desc" else "ASC"

    params = [benutzer_id]
    where = "WHERE w.benutzer_id = ?"
    if category_id:
        where += " AND w.category_id = ?"
        params.append(category_id)

    con = get_db()
    cur = con.cursor()
    cur.execute(
        f"""
        SELECT w.titel_id, w.created_at, w.category_id,
               c.name AS category_name,
               t.name, t.typ, t.genre, t.erscheinungsjahr
        FROM wishlist_item w
        LEFT JOIN wishlist_category c ON c.category_id = w.category_id
        LEFT JOIN titel t ON t.titel_id = w.titel_id
        {where}
        ORDER BY {sort_sql} {dir_sql}
        """,
        tuple(params),
    )
    rows = cur.fetchall()
    con.close()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

# ---------------- Bewertungssystem (nutzt eure Tabelle "bewertung") ----------------
@app.route("/api/bewertung/set", methods=["POST"])
def bewertung_set():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")
    rating = data.get("rating")

    if not benutzer_id or not titel_id or rating is None:
        return jsonify({"error": "benutzer_id, titel_id, rating sind Pflicht"}), 400

    try:
        rating = int(rating)
    except ValueError:
        return jsonify({"error": "rating muss eine Zahl sein"}), 400

    if rating not in (1, 2, 3, 4, 5):
        return jsonify({"error": "rating muss zwischen 1 und 5 sein"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO bewertung (benutzer_id, titel_id, rating)
        VALUES (?, ?, ?)
        ON CONFLICT(benutzer_id, titel_id)
        DO UPDATE SET rating = excluded.rating, datum = datetime('now')
        """,
        (benutzer_id, titel_id, rating),
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/bewertung/delete", methods=["POST"])
def bewertung_delete():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM bewertung WHERE benutzer_id = ? AND titel_id = ?", (benutzer_id, titel_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ---------------- Kommentare (nutzt eure Tabelle "kritik") ----------------
@app.route("/api/comment/add", methods=["POST"])
def comment_add():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")
    text = (data.get("text") or "").strip()

    if not benutzer_id or not titel_id or not text:
        return jsonify({"error": "benutzer_id, titel_id, text sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute(
            "INSERT INTO kritik (benutzer_id, titel_id, text) VALUES (?, ?, ?)",
            (benutzer_id, titel_id, text),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Kommentar existiert schon"}), 409

    con.close()
    return jsonify({"ok": True})

@app.route("/api/comment/update", methods=["POST"])
def comment_update():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")
    text = (data.get("text") or "").strip()

    if not benutzer_id or not titel_id or not text:
        return jsonify({"error": "benutzer_id, titel_id, text sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "UPDATE kritik SET text = ?, datum = datetime('now') WHERE benutzer_id = ? AND titel_id = ?",
        (text, benutzer_id, titel_id),
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/comment/delete", methods=["POST"])
def comment_delete():
    data = request.get_json(silent=True) or {}
    benutzer_id = data.get("benutzer_id")
    titel_id = data.get("titel_id")

    if not benutzer_id or not titel_id:
        return jsonify({"error": "benutzer_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM kritik WHERE benutzer_id = ? AND titel_id = ?", (benutzer_id, titel_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

if __name__ == "__main__":
    init_db()
    add_missing_structures()
    print(f"Starte {APP_NAME} mit Datenbank: {DB_NAME}")
    app.run(debug=True)