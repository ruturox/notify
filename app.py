import os
import time
import threading
import requests
import json
import tempfile
import feedparser
import re
from user import User, session

USER_DIR = "users"
CHECK_INTERVAL = 300
ADMIN_TOPIC = os.environ.get("NTFY_ADMIN_TOPIC", "hsnr_bot_admin")
DEFAULT_NTFY = os.environ.get("NTFY_SERVER", "http://ntfy").rstrip('/')

active_users = {}
user_lock = threading.Lock()
stop_event = threading.Event()

def save_user_to_disk(user: User, force=False):
    now = time.time()

    if not force and not (user.has_changes and (now - user.last_save_time > 3600)):
        return False

    if not os.path.exists(USER_DIR):
        os.makedirs(USER_DIR)

    file_path = os.path.join(USER_DIR, f"{user.id}.json")
    fd, temp_path = tempfile.mkstemp(dir=USER_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(user.to_dict(), f, indent=4, ensure_ascii=False)
        os.replace(temp_path, file_path)
        user.last_save_time = now
        user.has_changes = False
        print(f"💾 [{user.id}] Status auf Disk gesichert.")
        return True
    except Exception as e:
        if os.path.exists(temp_path): os.remove(temp_path)
        print(f"❌ [{user.id}] Schreibfehler: {e}")
        return False

def save_all_users_final():
    print("📥 Sichere alle Nutzerdaten vor dem Beenden...")
    with user_lock:
        for user in active_users.values():
            if user.has_changes:
                save_user_to_disk(user, force=True)

def load_initial_data():
    if not os.path.exists(USER_DIR):
        os.makedirs(USER_DIR)
        return
    for file_name in os.listdir(USER_DIR):
        if file_name.endswith(".json"):
            path = os.path.join(USER_DIR, file_name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    u = User(id=data["id"], rss_url=data["rss_url"], ntfy_topic=data["ntfy_topic"],
                             ntfy_server=data.get("ntfy_server"), email=data.get("email"),
                             seen_ids=data.get("seen_ids", {}))
                    u.last_save_time = os.path.getmtime(path)
                    active_users[u.id] = u
            except Exception as e:
                print(f"❌ Fehler beim Laden von {file_name}: {e}")

def check_user_feed(user: User):
    now_ts = int(time.time())
    found_new_note = False
    
    try:
        resp = session.get(user.rss_url, timeout=15)
        feed = feedparser.parse(resp.content)
        
        for entry in feed.entries:
            msg_id = getattr(entry, "guid", None)
            if not msg_id: continue
            title = getattr(entry, "title", "")
            if "note" in title.lower():
                if msg_id in user.seen_ids:
                    if user.seen_ids[msg_id] != now_ts:
                        user.seen_ids[msg_id] = now_ts
                        user.has_changes = True
                else:
                    match = re.search(r"'(.*?)'", title)
                    subject = match.group(1) if match else title
                    if user.send_notification(subject, DEFAULT_NTFY):
                        user.seen_ids[msg_id] = now_ts
                        user.has_changes = True
                        found_new_note = True
            
        old_len = len(user.seen_ids)
        user.seen_ids = {sid: ts for sid, ts in user.seen_ids.items() if now_ts - ts < 86400}
        if len(user.seen_ids) != old_len:
            user.has_changes = True
        
        with user_lock:
            save_user_to_disk(user, force=found_new_note)
            
    except Exception as e:
        print(f"❌ [{user.id}] Feed-Fehler: {e}")

def background_monitor():
    print(f"🚀 Monitor gestartet (Intervall: {CHECK_INTERVAL}s)")
    while not stop_event.is_set():
        with user_lock:
            users = list(active_users.values())
        for user in users:
            if stop_event.is_set(): break
            check_user_feed(user)
        for _ in range(CHECK_INTERVAL):
            if stop_event.is_set(): break
            time.sleep(1)

def bereinige(args, anzahl, start_index=1):
    return tuple(
        None if (start_index + i) >= len(args) 
        or args[start_index + i] is None 
        or args[start_index + i].strip() == "-" 
        else args[start_index + i].strip() 
        for i in range(anzahl)
    )

URL_REGEX = re.compile(
    r"^https://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}"
    r"(?:/[a-zA-Z0-9._~!$&'()*+,;=:@%-]*)*"
    r"(?:\?[a-zA-Z0-9._~!$&'()*+,;=:@%/?-]*)?"
    r"$"
)
SERVER_REGEX = re.compile(
    r"^https://(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}"
    r"$"
)
TOPIC_REGEX = re.compile(r"^[a-zA-Z0-9_-]{10,}$")
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

def validate_user_params(url, topic, email, server, edit=False):
    if url:
        if not URL_REGEX.match(url):
            return False, "❌ Ungültige RSS-URL (Format: http(s)://domain.de/pfad)"
    if topic:
        if not TOPIC_REGEX.match(topic):
            return False, "❌ NTFY-Topic ungültig (min. 10 Zeichen, nur A-Z, 0-9, _, -)."
    
    if email:
        if edit and email.lower() in ["none", "clear"]:
            pass
        elif not EMAIL_REGEX.match(email):
            return False, "❌ Ungültige E-Mail-Adresse."
    
    if server:
        if edit and server.lower() in ["none", "clear"]:
            pass
        elif not SERVER_REGEX.match(server):
            return False, "❌ Ungültige Server-URL (Format: http(s)://domain.de)"
            
    return True, None

def handle_command(cmd_text):
    parts = cmd_text.split()
    if not parts: return None
    action = parts[0].lower()
    
    with user_lock:
        try:
            if action == "add" and len(parts) >= 4:
                u_id = User.sanitize_id(parts[1])
                if not u_id: return "❌ User-ID darf nicht leer sein."
                if u_id in active_users: return f"❌ User '{u_id}' existiert bereits!"
            
                rss_url, ntfy_topic, email, server = bereinige(parts, 4, start_index=2)
                
                if not rss_url or not ntfy_topic:
                    return "❌ Pflichtfelder (URL & Topic) dürfen nicht leer sein."
                
                is_valid, err = validate_user_params(rss_url, ntfy_topic, email, server)
                if not is_valid: return err
                
                new_u = User(id=u_id, rss_url=rss_url, ntfy_topic=ntfy_topic, 
                             email=email, ntfy_server=server)
                active_users[u_id] = new_u
                save_user_to_disk(new_u, force=True)
                return f"✅ User '{u_id}' angelegt."
            elif action == "edit" and len(parts) >= 2:
                u_id = User.sanitize_id(parts[1])
                if u_id not in active_users: return f"❌ User '{u_id}' nicht gefunden."
                u = active_users[u_id]
                
                new_url, new_topic, new_email, new_server = bereinige(parts, 4, start_index=2)
                
                is_valid, err = validate_user_params(new_url, new_topic, new_email, new_server, True)
                if not is_valid: return err
                
                if new_url: u.rss_url = new_url
                if new_topic: u.ntfy_topic = new_topic
                
                if new_email:
                    u.email = None if new_email.lower() in ["none", "clear"] else new_email
                
                if new_server:
                    u.ntfy_server = None if new_server.lower() in ["none", "clear"] else new_server
                
                save_user_to_disk(u, force=True)
                return f"✅ User '{u_id}' aktualisiert."
            elif action == "view" and len(parts) >= 2:
                u_id = User.sanitize_id(parts[1])
                if u_id not in active_users: return f"❌ User '{u_id}' nicht gefunden."
                u = active_users[u_id]
                return (f"👤 **User-Profil: {u.id}**\n"
                        f"🌐 RSS: `{u.rss_url}`\n"
                        f"📢 Topic: `{u.ntfy_topic}`\n"
                        f"📧 Email: `{u.email or '---'}`\n"
                        f"🖥️ Server: `{u.ntfy_server or 'Default'}`")
            elif action == "delete" and len(parts) >= 2:
                u_id = User.sanitize_id(parts[1])
                if u_id in active_users:
                    del active_users[u_id]
                    path = os.path.join(USER_DIR, f"{u_id}.json")
                    if os.path.exists(path): os.remove(path)
                    return f"🗑️ User '{u_id}' gelöscht."
                return f"❌ User '{u_id}' nicht gefunden."
            elif action == "list":
                return f"👥 Nutzer: {', '.join(active_users.keys()) if active_users else 'Keine'}"
            elif action == "help":
                return ("📖 **Befehle:**\n"
                        "• `add [id] [url] [topic] [email?] [server?]` - Neu anlegen\n"
                        "• `edit [id] [url] [topic] [email?] [server?]` - Ändern (`-` zum Überspringen)\n"
                        "• `view [id]` - Details anzeigen\n"
                        "• `delete [id]` - Löschen\n"
                        "• `list` - Alle IDs zeigen")
        except Exception as e:
            return f"❌ Systemfehler: {e}"
    return "❓ Unbekannter Befehl. Sende 'help'."

def ntfy_listener():
    print(f"👂 ntfy-Listener aktiv auf Topic: {ADMIN_TOPIC}")
    url = f"{DEFAULT_NTFY}/{ADMIN_TOPIC}/json"
    token = os.environ.get('NTFY_TOKEN')
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    while not stop_event.is_set():
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as r:
                for line in r.iter_lines():
                    if stop_event.is_set(): 
                        break
                    if line:
                        msg = json.loads(line)
                        if msg.get("event") == "message":
                            cmd = msg.get("message", "").strip()
                            if cmd.startswith("[Server]"):
                                continue 
                            
                            print(f"📩 Admin-Befehl empfangen: {cmd}")
                            res = handle_command(cmd)
                            
                            if res:
                                response_text = f"[Server]\n {res}"
                                headers["Priority"] = "1"
                                requests.post(
                                    f"{DEFAULT_NTFY}/{ADMIN_TOPIC}", 
                                    data=response_text.encode("utf-8"), 
                                    headers=headers
                                )
        except Exception as e:
            if not stop_event.is_set():
                print(f"⚠️ Listener-Verbindung unterbrochen: {e}. Reconnect in 10s...")
                time.sleep(10)

if __name__ == "__main__":
    load_initial_data()
    t1 = threading.Thread(target=background_monitor, daemon=True)
    t2 = threading.Thread(target=ntfy_listener, daemon=True)
    t1.start()
    t2.start()
    try:
        while True: time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        stop_event.set()
    
    save_all_users_final()
    print("🛑 Bot ordnungsgemäß beendet.")