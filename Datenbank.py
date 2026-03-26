import sqlite3

DB_NAME = "tjf.db"

def get_db_connection():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON;")
    return con

def init_db():
    con = get_db_connection()
    cur = con.cursor()

    # Benutzer (Hashes, kein Klartext)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS benutzer (
      benutzer_id      INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzername     TEXT NOT NULL UNIQUE,
      email            TEXT NOT NULL UNIQUE,
      passwort_hash    TEXT NOT NULL,
      pin_hash         TEXT NOT NULL,
      created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    # Reset Tokens (für Passwort/PIN zurücksetzen)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reset_tokens (
      token_id     INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id  INTEGER NOT NULL,
      token        TEXT NOT NULL UNIQUE,
      purpose      TEXT NOT NULL CHECK (purpose IN ('password','pin')),
      expires_at   TEXT NOT NULL,
      used         INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0,1)),
      created_at   TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id)
    );
    """)

    # Titel
    cur.execute("""
    CREATE TABLE IF NOT EXISTS titel (
      titel_id         INTEGER PRIMARY KEY AUTOINCREMENT,
      name             TEXT NOT NULL,
      typ              TEXT NOT NULL,
      genre            TEXT,
      erscheinungsjahr INTEGER,
      beschreibung     TEXT
    );
    """)

    # Status (gesehen/wunschliste) – Playlist/Wunschliste bauen wir darauf auf
    cur.execute("""
    CREATE TABLE IF NOT EXISTS status (
      status_id   INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id INTEGER NOT NULL,
      titel_id    INTEGER NOT NULL,
      gesehen     INTEGER NOT NULL DEFAULT 0 CHECK (gesehen IN (0,1)),
      wunschliste INTEGER NOT NULL DEFAULT 0 CHECK (wunschliste IN (0,1)),
      datum       TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id),
      FOREIGN KEY (titel_id)    REFERENCES titel(titel_id),
      UNIQUE (benutzer_id, titel_id)
    );
    """)

    # Bewertung: nur ganze Sterne 1..5
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bewertung (
      bewertung_id INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id  INTEGER NOT NULL,
      titel_id     INTEGER NOT NULL,
      rating       INTEGER NOT NULL CHECK (rating IN (1,2,3,4,5)),
      datum        TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id),
      FOREIGN KEY (titel_id)    REFERENCES titel(titel_id),
      UNIQUE (benutzer_id, titel_id)
    );
    """)

    # Kritik
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kritik (
      kritik_id   INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzer_id INTEGER NOT NULL,
      titel_id    INTEGER NOT NULL,
      text        TEXT NOT NULL,
      datum       TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (benutzer_id) REFERENCES benutzer(benutzer_id),
      FOREIGN KEY (titel_id)    REFERENCES titel(titel_id),
      UNIQUE (benutzer_id, titel_id)
    );
    """)

    con.commit()
    con.close()