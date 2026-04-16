from flask import Flask, request, jsonify, send_from_directory, session
import sqlite3
import hashlib
import secrets
import webbrowser
import re 
from threading import Timer
from datetime import datetime, timedelta

from Datenbank import init_db, get_db_connection, DB_NAME

def ist_gueltige_email(email):
    if not email:
        return False

    # Leerzeichen vorne und hinten entfernen
    email = email.strip().lower()

    # Einfaches Format prüfen
    muster = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    if not re.match(muster, email):
        return False

    # Letzten Teil der Domain prüfen, z. B. "de" bei max@gmail.de
    endung = email.split(".")[-1]

    # Nur häufige und sinnvolle Endungen erlauben
    erlaubte_endungen = ["de", "com", "net", "org", "eu", "at", "ch"]

    if endung not in erlaubte_endungen:
        return False

    
    
    return True

APP_NAME = "tjf"
app = Flask(__name__, static_folder="static")
app.secret_key = secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# ---------------- Grundfunktionen ----------------
def get_db():
    return get_db_connection()

def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def is_valid_pin(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isdigit()

def require_login():
    benutzer_id = session.get("benutzer_id")
    if not benutzer_id:
        return None, (jsonify({"error": "nicht eingeloggt"}), 401)
    return int(benutzer_id), None

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
    try:
        cur.execute("ALTER TABLE status ADD COLUMN playlist_id INTEGER;")
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
       # 🟢🟢🟢 HIER KOMMT DER NEUE BLOCK FÜR MULTI-PLAYLIST:
    cur.execute("""
    CREATE TABLE IF NOT EXISTS playlists (
      playlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id)
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

    benutzer_id = session.get("benutzer_id")
    if not benutzer_id:
        return jsonify({"error": "nicht eingeloggt"}), 401
    titel = (data.get("titel") or "").strip()
    typ = (data.get("typ") or "").strip()
    ziel_liste = (data.get("ziel_liste") or "").strip()  # "playlist" oder "wishlist"
    kategorie = (data.get("kategorie") or "").strip() or None
    playlist_id = data.get("playlist_id")
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

        # Wenn eine Kategorie angegeben wurde, speichern wir sie als Genre beim Titel
        if kategorie:
            cur.execute(
                "UPDATE titel SET genre = ? WHERE titel_id = ?",
                (kategorie, titel_id),
            )
    else:
        cur.execute(
            "INSERT INTO titel (name, typ, genre, erscheinungsjahr, beschreibung) VALUES (?, ?, ?, NULL, NULL)",
            (titel, typ, kategorie),
        )
        titel_id = cur.lastrowid

    # 2) Status setzen
    gesehen = 1 if ziel_liste == "playlist" else 0  
    wunschliste = 1 if ziel_liste == "wishlist" else 0

    # Wunschliste hat keine Playlist-Zuordnung
    if ziel_liste == "wishlist":
        playlist_id = None

    # playlist_id prüfen und in Zahl umwandeln
    if playlist_id in ("", None):
        playlist_id = None
    else:
        try:
            playlist_id = int(playlist_id)
        except ValueError:
            return jsonify({"error": "playlist_id muss eine Zahl sein"}), 400

    cur.execute(
        """
        INSERT INTO status (benutzer_id, titel_id, gesehen, wunschliste, playlist_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(benutzer_id, titel_id)
        DO UPDATE SET
            gesehen = excluded.gesehen,
            wunschliste = excluded.wunschliste,
            playlist_id = excluded.playlist_id,
            datum = datetime('now')
        """,
        (benutzer_id, titel_id, gesehen, wunschliste, playlist_id),
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

    # 5) Wunschliste: Kategorie optional
    if ziel_liste == "wishlist":
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
    benutzer_id = session.get("benutzer_id")
    if not benutzer_id:
        return jsonify({"error": "nicht eingeloggt"}), 401

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
          COALESCE(wc.name, t.genre) AS kategorie,
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
    email = (data.get("email") or "").strip().lower()
    pin = data.get("pin") or ""

    # PIN ist euer "Passwort" -> kein extra passwort Feld
    if not benutzername or not email or not pin:
        return jsonify({"error": "benutzername, email, pin sind Pflicht"}), 400
    # E-Mail prüfen 
    if not ist_gueltige_email(email):
        return jsonify({"error": "Bitte eine gültige E-Mail-Adresse eingeben"}), 400
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

    # PIN prüfen
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

    # Login erfolgreich: Counter/Sperre zurücksetzen
    cur.execute(
        "UPDATE benutzer SET failed_login_attempts = 0, lock_until = NULL WHERE benutzer_id = ?",
        (row["benutzer_id"],),
    )
    con.commit()
    con.close()

    # Session neu aufsetzen
    session.clear()
    session["benutzer_id"] = row["benutzer_id"]
    session["benutzername"] = benutzername

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


@app.route("/api/playlists/create", methods=["POST"])
def playlists_create():
    benutzer_id, err = require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name ist Pflicht"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("INSERT INTO playlists (benutzer_id, name) VALUES (?, ?)", (benutzer_id, name))
    con.commit()
    playlist_id = cur.lastrowid
    con.close()
    return jsonify({"ok": True, "playlist_id": playlist_id})

@app.route("/api/playlists/delete", methods=["POST"])
def playlists_delete():
    benutzer_id, err = require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    if not playlist_id:
        return jsonify({"error": "playlist_id ist Pflicht"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("DELETE FROM playlists WHERE playlist_id = ? AND benutzer_id = ?", (playlist_id, benutzer_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/playlists/rename", methods=["POST"])
def playlists_rename():
    benutzer_id, err = require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    name = (data.get("name") or "").strip()
    if not playlist_id or not name:
        return jsonify({"error": "playlist_id und name sind Pflicht"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE playlists SET name = ? WHERE playlist_id = ? AND benutzer_id = ?", (name, playlist_id, benutzer_id))
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.route("/api/playlist_entry/add", methods=["POST"])
def playlist_entry_add():
    benutzer_id, err = require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    titel_id = data.get("titel_id")
    if not playlist_id or not titel_id:
        return jsonify({"error": "playlist_id und titel_id sind Pflicht"}), 400
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT COALESCE(MAX(position), 0) AS maxpos FROM playlist_entry WHERE playlist_id = ?", (playlist_id,))
    maxpos = cur.fetchone()["maxpos"]
    try:
        cur.execute(
            "INSERT INTO playlist_entry (playlist_id, titel_id, position) VALUES (?, ?, ?)",
            (playlist_id, titel_id, int(maxpos) + 1),
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "Titel ist schon in Playlist"}), 409
    con.close()
    return jsonify({"ok": True})

@app.route("/api/playlist_entry/remove", methods=["POST"])
def playlist_entry_remove():
    benutzer_id, err = require_login()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    playlist_id = data.get("playlist_id")
    titel_id = data.get("titel_id")

    if not playlist_id or not titel_id:
        return jsonify({"error": "playlist_id und titel_id sind Pflicht"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute(
        """
        UPDATE status
        SET playlist_id = NULL
        WHERE benutzer_id = ?
          AND titel_id = ?
          AND playlist_id = ?
        """,
        (benutzer_id, titel_id, playlist_id),
    )

    con.commit()
    con.close()

    return jsonify({"ok": True})

@app.route("/api/playlist_entry/list", methods=["GET"])
def playlist_entry_list():
    benutzer_id, err = require_login()
    if err:
        return err

    playlist_id = request.args.get("playlist_id", type=int)
    if not playlist_id:
        return jsonify({"error": "playlist_id ist Pflicht"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT 
            t.titel_id,
            t.name,
            t.typ,
            t.genre,
            t.erscheinungsjahr,
            t.genre AS kategorie,
            b.rating,
            k.text AS kritik
        FROM status s
        JOIN titel t ON t.titel_id = s.titel_id
        LEFT JOIN bewertung b
            ON b.titel_id = t.titel_id
            AND b.benutzer_id = s.benutzer_id
        LEFT JOIN kritik k
            ON k.titel_id = t.titel_id
            AND k.benutzer_id = s.benutzer_id
        WHERE s.benutzer_id = ?
          AND s.playlist_id = ?
          AND s.gesehen = 1
        ORDER BY t.name
    """, (benutzer_id, playlist_id))

    rows = cur.fetchall()
    con.close()

    return jsonify({"ok": True, "items": [dict(r) for r in rows]})

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

@app.route("/api/playlists/list", methods=["GET"])
def playlists_list():
    benutzer_id, err = require_login()
    if err:
        return err
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT playlist_id, name FROM playlists WHERE benutzer_id = ? ORDER BY name", (benutzer_id,))
    rows = cur.fetchall()
    con.close()
    return jsonify({"ok": True, "items": [dict(r) for r in rows]})



print("ROUTES DEBUG:")
for rule in app.url_map.iter_rules():
    print(rule, "->", rule.endpoint, "methods:", rule.methods)

if __name__ == "__main__":
    init_db()
    add_missing_structures()
    print(f"Starte {APP_NAME} mit Datenbank: {DB_NAME}")

    # Browser automatisch öffnen
    import webbrowser
    from threading import Timer
    url = "http://127.0.0.1:5000/static/TyF.html"
    Timer(1.0, lambda: webbrowser.open(url)).start()

    # WICHTIG: Debug/Reloader aus, sonst startet Flask zweimal
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)