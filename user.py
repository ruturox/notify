import os
import json
import re
import time
import requests
from dataclasses import dataclass, field
from typing import Optional

session = requests.Session()

@dataclass
class User:
    id: str
    rss_url: str
    ntfy_topic: str
    ntfy_server: Optional[str] = None
    email: Optional[str] = None
    seen_ids: dict = field(default_factory=dict)
    
    # Interne Status-Felder
    last_save_time: float = field(init=False, default=0.0)
    has_changes: bool = field(init=False, default=False) # Dirty-Flag

    def __post_init__(self):
        self.id = self.sanitize_id(self.id)
        if self.ntfy_server:
            self.ntfy_server = self.ntfy_server.rstrip('/')

    @staticmethod
    def sanitize_id(user_id):
        return re.sub(r"[^a-zA-Z0-9_\-]", "", user_id)

    def to_dict(self):
        return {
            "id": self.id,
            "rss_url": self.rss_url,
            "ntfy_topic": self.ntfy_topic,
            "ntfy_server": self.ntfy_server,
            "email": self.email,
            "seen_ids": self.seen_ids
        }

    def send_notification(self, subject, default_server):
        server = self.ntfy_server if self.ntfy_server else default_server
        url = f"{server}/{self.ntfy_topic}"
        token = os.environ.get('NTFY_TOKEN')
        headers = {"Title": f"Neue Note: {self.id}", "Priority": "high", "Tags": "mortar_board,bell", "Actions": "view, Gehe zu hio, https://hio.hsnr.de/qisserver/pages/sul/examAssessment/personExamsReadonly.xhtml?_flowId=examsOverviewForPerson-flow"}
        if token: headers["Authorization"] = f"Bearer {token}"
        if self.email: headers["Email"] = self.email
            
        try:
            resp = session.post(url, data=f"Fach: **{subject}**\n![some image](https://hio.hsnr.de/HISinOne/images/logos/hisinone_schriftzug_portal_hsnr.png)".encode("utf-8"), headers=headers, timeout=15)
            print(f"✅ Push-Benachrichtigung für {self.id} gesendet ({subject}).")
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"❌ [{self.id}] Push-Fehler: {e}")
            return False