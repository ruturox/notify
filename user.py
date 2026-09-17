import os
import re
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
        headers = {"Title": f"Neue Note fuer {self.id}", "Priority": "high", "Tags": "mortar_board,bell", "Actions": "view, Gehe zu HiO, https://hio.hsnr.de/qisserver/pages/sul/examAssessment/personExamsReadonly.xhtml?_flowId=examsOverviewForPerson-flow", "Markdown": "yes"}
        if token: headers["Authorization"] = f"Bearer {token}"
            
        try:
            resp = session.post(url, data=f"## Fach: **{subject}** [HiO](https://hio.hsnr.de/qisserver/pages/sul/examAssessment/personExamsReadonly.xhtml?_flowId=examsOverviewForPerson-flow) ##\n![](https://hio.hsnr.de/HISinOne/images/logos/hisinone_schriftzug_portal_hsnr.png)".encode("utf-8"), headers=headers, timeout=15)
            resp.raise_for_status()
            print(f"✅ Push-Benachrichtigung für {self.id} gesendet ({subject}).")
            if self.email:
                resp = session.post(url,data=f"Fach: {subject} Lik zu HIO: (https://hio.hsnr.de/qisserver/pages/sul/examAssessment/personExamsReadonly.xhtml?_flowId=examsOverviewForPerson-flow)".encode("utf-8"), headers={"Title": f"Neue Note {self.id}", "Priority": "high", "Tags": "mortar_board,bell", "Email": self.email}, timeout=15)

        except requests.RequestException as e:
            print(f"❌ [{self.id}] Push-Fehler: {e}")
            return False
        url = f"{server}/email"
        if self.email:
            try:
                headers = {"Title": f"Neue Note in {subject} fuer {self.id}", "Priority": "high","Tags": "mortar_board,bell", "Email": self.email}
                if token: headers["Authorization"] = f"Bearer {token}"
                resp = session.post(url,
                                    data=f"Fach: {subject}\nHiO: https://hio.hsnr.de/qisserver/pages/sul/examAssessment/personExamsReadonly.xhtml?_flowId=examsOverviewForPerson-flow", headers=headers, timeout=15)
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"❌ [{self.id}] Email-Fehler: {e}")

        return True