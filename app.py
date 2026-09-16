import feedparser
import requests
import time
import os
import json
import re
import threading
import tempfile
from typing import Any

USER_DIR = "users"
CHECK_INTERVAL = 300
GRACE_PERIOD = 86400
REQUEST_TIMEOUT = 15
DEFAULT_NTFY_SERVER = "http://ntfy"

user_lock = threading.Lock()
stop_event = threading.Event()

if not os.path.exists(USER_DIR):
    os.makedirs(USER_DIR)


def sanitize_id(user_id):
    return re.sub(r"[^a-zA-Z0-9_\-]", "", user_id)


def extract_subject(title):
    match = re.search(r"'(.*?)'", title)
    return match.group(1) if match else title


def load_user(file_path) -> dict[str, Any] | None:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Fehler beim Laden von {file_path}: {e}")
        return None


def save_user(user):
    user_id = sanitize_id(user["id"])
    if not user_id:
        print("❌ Fehler beim Speichern: Ungültige User-ID.")
        return False

    file_path = os.path.join(USER_DIR, f"{user_id}.json")

    fd, temp_path = tempfile.mkstemp(dir=USER_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(user, f, indent=4, ensure_ascii=False)
        os.replace(temp_path, file_path)
        return True
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print(f"❌ Fehler beim Speichern von {user_id}: {e}")
        return False

def send_push(user, subject):
    url = f"{user['ntfy_server'].rstrip('/')}/{user['ntfy_topic']}"
    try:
        response = requests.post(
            url,
            data=f"Neue Note im Fach: {subject}".encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ['NTFY_TOKEN']}",  # ← NEU
                "Title": f"Hallo {user['id']}, neue Note!",
                "Priority": "high",
                "Tags": "mortar_board"
            },
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        print(f"✅ Push-Benachrichtigung für {user['id']} gesendet ({subject}).")
        return True
    except Exception as e:
        print(f"❌ Push für {user['id']} fehlgeschlagen: {e}")
        return False



def fetch_feed(rss_url):
    response = requests.get(rss_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return feedparser.parse(response.content)


def process_single_user(file_name, now_ts):
    file_path = os.path.join(USER_DIR, file_name)

    with user_lock:
        user = load_user(file_path)
        if not user:
            return

        has_changes = False
        rss_url = user.get("rss_url", "").strip()
        user_id = user.get("id", file_name.replace(".json", ""))
        ntfy_server = user.get("ntfy_server", DEFAULT_NTFY_SERVER)
        ntfy_topic = user.get("ntfy_topic", "").strip()

        if not rss_url:
            print(f"⚠️ {user_id}: Keine RSS-URL hinterlegt.")
            return

        if "seen_ids" not in user or not isinstance(user["seen_ids"], dict):
            user["seen_ids"] = {}
            has_changes = True

        try:
            feed = fetch_feed(rss_url)

            if getattr(feed, "bozo", 0):
                bozo_exception = getattr(feed, "bozo_exception", None)
                if bozo_exception:
                    print(f"⚠️ {user_id}: Feed ist fehlerhaft/parsing-auffällig: {bozo_exception}")

            for entry in feed.entries:
                msg_id = (
                    getattr(entry, "guid", None)
                )

                if not msg_id:
                    print(f"⚠️ {user_id}: Ein Feed-Eintrag ohne GUID/ID/Link wurde übersprungen.")
                    continue

                title = getattr(entry, "title", "")
                is_note = "note" in title.lower()

                if msg_id in user["seen_ids"]:
                    user["seen_ids"][msg_id] = now_ts
                    has_changes = True
                    continue

                should_mark_as_seen = True

                if is_note:
                    if not ntfy_topic:
                        print(f"⚠️ {user_id}: ntfy-Topic fehlt, Push nicht möglich.")
                        should_mark_as_seen = False
                    else:
                        push_user = {
                            "id": user_id,
                            "ntfy_server": ntfy_server,
                            "ntfy_topic": ntfy_topic
                        }
                        should_mark_as_seen = send_push(push_user, extract_subject(title))

                if should_mark_as_seen:
                    user["seen_ids"][msg_id] = now_ts
                    has_changes = True

            ids_to_delete = [
                sid for sid, ts in user["seen_ids"].items()
                if now_ts - ts > GRACE_PERIOD
            ]

            if ids_to_delete:
                for old_id in ids_to_delete:
                    del user["seen_ids"][old_id]
                has_changes = True

            if has_changes:
                save_user(user)

        except requests.RequestException as e:
            print(f"❌ Netzwerkfehler bei User {user_id}: {e}")
        except Exception as e:
            print(f"❌ Fehler bei User {user_id}: {e}")


def background_monitor():
    while not stop_event.is_set():
        now_ts = int(time.time())

        with user_lock:
            user_files = [f for f in os.listdir(USER_DIR) if f.endswith(".json")]

        for file_name in user_files:
            if stop_event.is_set():
                break
            process_single_user(file_name, now_ts)

        stop_event.wait(CHECK_INTERVAL)


# --- UI FUNKTIONEN ---

def add_user_ui():
    print("\n--- NEUEN NUTZER ANLEGEN ---")

    raw_id = input("Name/ID: ").strip()
    u_id = sanitize_id(raw_id)

    if not u_id:
        print("❌ Ungültige ID. Bitte nur Buchstaben, Zahlen, Unterstriche und Bindestriche verwenden.")
        return

    u_url = input("RSS-URL: ").strip()
    u_server = input(
        f"ntfy-Server (Enter für {DEFAULT_NTFY_SERVER}): "
    ).strip() or DEFAULT_NTFY_SERVER
    u_topic = input("ntfy-Topic: ").strip()

    if not u_url or not u_topic:
        print("❌ RSS-URL und ntfy-Topic dürfen nicht leer sein.")
        return

    file_path = os.path.join(USER_DIR, f"{u_id}.json")

    with user_lock:
        if os.path.exists(file_path):
            print(f"❌ Fehler: Ein Nutzer mit der ID '{u_id}' existiert bereits!")
            print("Bitte wähle einen anderen Namen oder nutze den 'edit' Befehl.")
            return

        data = {
            "id": u_id,
            "rss_url": u_url,
            "ntfy_server": u_server,
            "ntfy_topic": u_topic,
            "seen_ids": {}
        }

        save_user(data)

    print(f"✅ User {u_id} erfolgreich hinzugefügt!\n")

def edit_user_ui():
    old_raw_id = input("Welche User-ID möchtest du ändern?: ").strip()
    old_id = sanitize_id(old_raw_id)
    old_file_path = os.path.join(USER_DIR, f"{old_id}.json")

    with user_lock:
        user = load_user(old_file_path)

    if not user:
        print("❌ Nutzer nicht gefunden.")
        return

    print(f"\nBearbeite Nutzer: {old_id}")

    new_raw_id = input(f"Neue ID/Name [{user['id']}]: ").strip()
    new_url = input(f"RSS-URL [{user['rss_url']}]: ").strip()
    new_server = input(f"ntfy-Server [{user['ntfy_server']}]: ").strip()
    new_topic = input(f"ntfy-Topic [{user['ntfy_topic']}]: ").strip()

    new_id = sanitize_id(new_raw_id) if new_raw_id else old_id

    if not new_id:
        print("❌ Ungültige neue ID.")
        return

    with user_lock:
        id_was_changed = (new_id != old_id)
        new_file_path = os.path.join(USER_DIR, f"{new_id}.json")

        if id_was_changed and os.path.exists(new_file_path):
            print(f"❌ Fehler: Der Name '{new_id}' wird bereits von einem anderen Nutzer verwendet!")
            return

        if new_url:
            user["rss_url"] = new_url
        if new_server:
            user["ntfy_server"] = new_server
        if new_topic:
            user["ntfy_topic"] = new_topic

        user["id"] = new_id

        if not save_user(user):
            return

        if id_was_changed:
            try:
                os.remove(old_file_path)
                print(f"✅ ID erfolgreich von '{old_id}' zu '{new_id}' geändert. Alte Datei wurde entfernt.")
            except Exception as e:
                print(f"⚠️ Fehler beim Löschen der alten Datei: {e}")
        else:
            print("✅ Nutzerdaten wurden aktualisiert (Name blieb gleich).")


def set_interval_ui():
    global CHECK_INTERVAL
    inp = input("Neues Intervall in Minuten: ").strip()
    try:
        mins = float(inp.replace(",", "."))
        if mins <= 0:
            raise ValueError
        CHECK_INTERVAL = int(mins * 60)
        print(f"✅ Intervall auf {mins} Min. gesetzt.\n")
    except ValueError:
        print("❌ Ungültige Eingabe. Bitte eine positive Zahl eingeben.")


def delete_user_ui():
    print("\n--- NUTZER LÖSCHEN ---")
    raw_id = input("Welche User-ID soll gelöscht werden?: ").strip()
    u_id = sanitize_id(raw_id)
    file_path = os.path.join(USER_DIR, f"{u_id}.json")

    with user_lock:
        if not os.path.exists(file_path):
            print(f"❌ Fehler: Nutzer '{u_id}' existiert nicht.")
            return

    confirm = input(f"⚠️ Bist du sicher, dass du '{u_id}' unwiderruflich löschen willst? (ja/nein): ").strip().lower()

    if confirm == "ja":
        try:
            with user_lock:
                os.remove(file_path)
            print(f"✅ Nutzer '{u_id}' wurde erfolgreich gelöscht.")
        except Exception as e:
            print(f"❌ Fehler beim Löschen der Datei: {e}")
    else:
        print("❌ Löschvorgang abgebrochen.")


def list_status():
    with user_lock:
        user_files = [f.replace(".json", "") for f in os.listdir(USER_DIR) if f.endswith(".json")]

    print("\n--- STATUS ---")
    print(f"Aktive Nutzer: {', '.join(user_files) if user_files else 'Keine'}")
    print(f"Intervall: {CHECK_INTERVAL / 60} Minuten")
    print("----------------\n")


if __name__ == "__main__":
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()

    print("=== HSNR NOTEN-BOT SYSTEM ===")
    print("Befehle: 'add', 'edit', 'delete', 'list', 'interval', 'exit'")

    while True:
        try:
            cmd = input("Bot-Konsole > ").lower().strip()

            if cmd == "add":
                add_user_ui()
            elif cmd == "edit":
                edit_user_ui()
            elif cmd == "delete":
                delete_user_ui()
            elif cmd == "list":
                list_status()
            elif cmd == "interval":
                set_interval_ui()
            elif cmd == "exit":
                break
            elif cmd == "":
                continue
            else:
                print("Unbekannter Befehl.")
        except KeyboardInterrupt:
            break

    stop_event.set()
    print("Beende Bot...")