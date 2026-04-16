# TyF – Track your Film, Serien und mehr
# Web-App mit:
# - Registrierung (E-Mail + 4-stellige PIN)
# -Login / Logout
# -Film-Playlist und Wunschliste
# -Sterne-Bewertung
# -Kommentarfunktion
"""
DEPRECATED / NICHT VERWENDEN.
Diese Datei ist ein altes Demo-Beispiel und wird im Projekt nicht genutzt.
Startpunkt ist Main.py.
"""
from flask import Flask, render_template, request, redirect, url_for, session

# Flask Anwendung erstellen
app = Flask(__name__)

# Secret Key wird für Sessions benötigt (z.B. zum Speichern von Login-Daten)
import os
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))  # In echten Projekten geheim halten!

# "Datenbanken" im Speicher (für Schulprojekt ausreichend)
# Nutzer werden in einem Dictionary gespeichert:
# Schlüssel: E-Mail, Wert: Dictionary mit PIN
users = {}
# Beispielstruktur:
# users["test@example.com"] = {"pin": "1234"}

# Alle Einträge (Filme/Serien) werden in einer Liste gespeichert
entries = []
# Jeder Eintrag ist ein Dictionary mit:
# {
#   "id": int,
#   "owner_email": str,
#   "title": str,
#   "type": str,
#   "rating": int,
#   "review": str,
#   "list_type": "playlist" oder "wishlist",
#   "category": str,
#   "comments": [ { "author": str, "text": str }, ... ]
# }

# Laufende ID für neue Einträge
next_id = 1


# Hilfsfunktion: Prüfen, ob ein Nutzer eingeloggt ist
def is_logged_in():
    # In der Session wird "user_email" gespeichert, wenn jemand eingeloggt ist
    return "user_email" in session


# Startseite – nur erreichbar, wenn eingeloggt
@app.route("/", methods=["GET", "POST"])
def index():
    global next_id

    # Wenn niemand eingeloggt ist → zum Login umleiten
    if not is_logged_in():
        return redirect(url_for("login"))

    # Wenn das Formular zum Hinzufügen eines neuen Eintrags abgeschickt wurde
    if request.method == "POST":
        # Formulardaten auslesen
        title = request.form.get("title")              # Titel des Inhalts
        type_ = request.form.get("type")               # Typ (Film, Serie, ...)
        rating = int(request.form.get("rating"))       # Bewertung (1–5)
        review = request.form.get("review") or ""      # Eigene Kritik (optional)
        list_type = request.form.get("list_type")      # "playlist" oder "wishlist"
        category = request.form.get("category") or ""  # Kategorie (optional)

        # Neuen Eintrag als Dictionary erstellen
        new_entry = {
            "id": next_id,                             # eindeutige ID
            "owner_email": session["user_email"],      # gehört dem eingeloggten Nutzer
            "title": title,
            "type": type_,
            "rating": rating,
            "review": review,
            "list_type": list_type,
            "category": category,
            "comments": []                             # leere Kommentarliste
        }

        # Eintrag zur Liste hinzufügen
        entries.append(new_entry)

        # ID für den nächsten Eintrag erhöhen
        next_id += 1

        # Nach dem Speichern wieder auf die Startseite
        return redirect(url_for("index"))

    # GET-Anfrage: Seite anzeigen

    # Alle Einträge des aktuell eingeloggten Nutzers filtern
    user_entries = [e for e in entries if e["owner_email"] == session["user_email"]]

    # Playlist-Einträge (list_type == "playlist")
    playlist_entries = [e for e in user_entries if e["list_type"] == "playlist"]

    # Wunschliste-Einträge (list_type == "wishlist")
    wishlist_entries = [e for e in user_entries if e["list_type"] == "wishlist"]

    # Playlist nach Bewertung sortieren (höchste Bewertung zuerst)
    playlist_entries_sorted = sorted(playlist_entries, key=lambda e: e["rating"], reverse=True)

    # Template rendern und Daten übergeben
    return render_template(
        "index.html",
        playlist_entries=playlist_entries_sorted,
        wishlist_entries=wishlist_entries,
        user_email=session["user_email"]
    )


# Registrierung – Nutzer erstellt E-Mail + PIN
@app.route("/register", methods=["GET", "POST"])
def register():
    # Wenn das Formular abgeschickt wurde
    if request.method == "POST":

        # E-Mail und PIN aus dem Formular holen
        email = request.form.get("email")
        pin = request.form.get("pin")

        # Prüfen: ist die E-Mail schon registriert?
        if email in users:
            # Fehler an Template übergeben
            return render_template("register.html", error="Diese E-Mail ist bereits registriert.")

        # Prüfen: PIN muss 4-stellig und nur aus Ziffern bestehen
        if not (pin and pin.isdigit() and len(pin) == 4):
            return render_template("register.html", error="Die PIN muss 4-stellig und numerisch sein.")

        # Neuen Nutzer in der "Datenbank" speichern
        users[email] = {"pin": pin}

        # Nutzer direkt einloggen, indem wir die E-Mail in der Session speichern
        session["user_email"] = email

        # Nach erfolgreicher Registrierung zur Startseite
        return redirect(url_for("index"))

    # GET-Anfrage: Registrierungsformular anzeigen
    return render_template("register.html")


# Login – Nutzer meldet sich mit E-Mail + PIN an
@app.route("/login", methods=["GET", "POST"])
def login():
    # Wenn das Formular abgeschickt wurde
    if request.method == "POST":

        # E-Mail und PIN aus dem Formular holen
        email = request.form.get("email")
        pin = request.form.get("pin")

        # Prüfen: existiert diese E-Mail überhaupt?
        if email not in users:
            return render_template("login.html", error="Diese E-Mail ist nicht registriert.")

        # Prüfen: stimmt die PIN mit der gespeicherten überein?
        if users[email]["pin"] != pin:
            return render_template("login.html", error="Die PIN ist falsch.")

        # Login erfolgreich → E-Mail in Session speichern
        session["user_email"] = email

        # Zur Startseite weiterleiten
        return redirect(url_for("index"))

    # GET-Anfrage: Loginformular anzeigen
    return render_template("login.html")


# Logout – Nutzer abmelden
@app.route("/logout")
def logout():
    # Alle Session-Daten löschen (Nutzer ist ausgeloggt)
    session.clear()
    # Zur Login-Seite zurück
    return redirect(url_for("login"))


# Kommentare zu einem Eintrag hinzufügen
@app.route("/comment/<int:entry_id>", methods=["POST"])
def add_comment(entry_id):

    # Nur eingeloggte Nutzer dürfen kommentieren
    if not is_logged_in():
        return redirect(url_for("login"))

    # Kommentartext aus Formular holen
    comment_text = request.form.get("comment_text") or ""

    # Passenden Eintrag anhand der ID suchen
    for e in entries:
        # Nur Einträge des aktuellen Nutzers bearbeiten
        if e["id"] == entry_id and e["owner_email"] == session["user_email"]:
            # Kommentar als Dictionary hinzufügen
            e["comments"].append({
                "author": session["user_email"],  # E-Mail des Kommentierenden
                "text": comment_text
            })
            break

    # Zurück zur Startseite
    return redirect(url_for("index"))

# App starten
if __name__ == "__main__":
    # debug=True zeigt Fehler im Browser an (nur in Entwicklung)
    app.run(debug=True)