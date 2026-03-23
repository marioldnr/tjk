import sqlite3  # Verbindung zu SQLite (ist in Python eingebaut)

DB_NAME = "tjf.db"  # unsere Datenbank-Datei

def init_db():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    # Tabelle: Benutzer
    cur.execute("""
    CREATE TABLE IF NOT EXISTS benutzer (
      benutzer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
      benutzername  TEXT NOT NULL UNIQUE,
      email         TEXT NOT NULL UNIQUE,
      passwort_hash TEXT NOT NULL,
      pin           TEXT NOT NULL
    );
    """)

    # Tabelle: Titel (Film/Serie)
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

    # Tabelle: Status (gesehen / wunschliste)
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

    # Tabelle: Bewertung (nur ganze Sterne 1..5)
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

    # Tabelle: Kritik
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
    print("Fertig: tjf.db erstellt + Tabellen angelegt.")

if __name__ == "__main__":
    init_db()