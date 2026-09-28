"""E-Mail-Benachrichtigungen: Warnungen, gemeldete Probleme, Technik (Gateway offline, Sensorfehler), Berichte.

Jede Person stellt selbst ein, was sie bekommt (Mein Konto → Benachrichtigungen). Ohne eigene Einstellung
gelten Vorgaben je Rolle. Gleiche Meldungen werden gedrosselt (je Stellplatz und Art höchstens alle 10 min),
damit ein Rütteln nicht 20 E-Mails auslöst. Simulierte Ereignisse sind im Betreff gekennzeichnet.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from .billing import TZ
from .core import ROLE_RANK, Core

log = logging.getLogger("notify")

KINDS = ("alert", "problem", "tech", "report")
REPORT_CHOICES = ("off", "daily", "weekly")
THROTTLE_S = 600
OFFLINE_AFTER_S = 300

DEFAULTS = {
    "owner": {"alert": True, "problem": True, "tech": True, "report": "weekly"},
    "admin": {"alert": True, "problem": True, "tech": True, "report": "weekly"},
    "operator": {"alert": True, "problem": True, "tech": False, "report": "off"},
    "viewer": {"alert": False, "problem": False, "tech": False, "report": "off"},
}

SUBJECTS = {
    "alert.created": "Ungewöhnliche Bewegung am Stellplatz „{station}“",
    "problem.reported": "Problem gemeldet am Stellplatz „{station}“",
    "sensor.fault": "Sensorfehler am Stellplatz „{station}“",
    "gateway.offline": "Gateway offline: Stellplatz „{station}“",
    "gateway.online": "Gateway wieder online: Stellplatz „{station}“",
}
KIND_OF = {"alert.created": "alert", "problem.reported": "problem", "sensor.fault": "tech",
           "gateway.offline": "tech", "gateway.online": "tech"}
CATEGORY = {"damaged": "beschädigt", "blocked": "zugestellt/blockiert", "wrong_status": "Anzeige stimmt nicht", "other": "Sonstiges"}


def prefs_for(user: sqlite3.Row) -> dict:
    base = dict(DEFAULTS.get(user["role"], DEFAULTS["viewer"]))
    if user["notify"]:
        try:
            saved = json.loads(user["notify"])
        except ValueError:
            saved = {}
        for k in ("alert", "problem", "tech"):
            if isinstance(saved.get(k), bool):
                base[k] = saved[k]
        if saved.get("report") in REPORT_CHOICES:
            base["report"] = saved["report"]
    return base


class Notifier:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db
        core.listeners.append(self.on_event)

    # ------------------------------------------------------------------ Drosselung
    def once(self, key: str, every_s: float | None) -> bool:
        """True, wenn für key gesendet werden darf (und merkt sich den Versand)."""
        now = self.core.clock()
        last = self.db.scalar("SELECT at FROM notice_sent WHERE key = ?", (key,))
        if last is not None and (every_s is None or now - last < every_s):
            return False
        self.db.execute("INSERT INTO notice_sent (key, at) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET at = excluded.at", (key, now))
        return True

    def recipients(self, tenant_id: str, kind: str, min_role: str = "viewer") -> list[sqlite3.Row]:
        users = self.db.all("SELECT * FROM user WHERE tenant_id = ?", (tenant_id,))
        return [u for u in users if ROLE_RANK.get(u["role"], 0) >= ROLE_RANK[min_role] and prefs_for(u).get(kind)]

    def send(self, tenant_id: str, kind: str, subject: str, body: str, attachments=None) -> int:
        n = 0
        for u in self.recipients(tenant_id, kind):
            self.core.mailer.send(u["email"], f"{self.core.s.product_name}: {subject}", body, attachments=attachments)
            n += 1
        return n

    # ------------------------------------------------------------------ Ereignisse
    def on_event(self, name: str, tenant_id: str, data: dict) -> None:
        kind = KIND_OF.get(name)
        if kind is None:
            return
        station = data.get("station_name", "?")
        key = f"{name}:{data.get('station_id') or data.get('device_id')}"
        if name not in ("gateway.offline", "gateway.online") and not self.once(key, THROTTLE_S):
            return
        sim = "[SIMULATION] " if data.get("simulated") else ""
        subject = sim + SUBJECTS[name].format(station=station)
        url = f"{self.core.s.base_url}/app#/stations/{data.get('station_id', '')}"
        lines = {
            "alert.created": "Am Stellplatz wurde eine ungewöhnliche Bewegung gemessen.\n"
                             "Das ist ein Hinweis – kein Nachweis für einen Diebstahl und keine Aussage über Personen.\n"
                             "Bitte bei Gelegenheit nachsehen und die Meldung im Portal quittieren.",
            "problem.reported": f"Jemand hat am Stellplatz ein Problem gemeldet: {CATEGORY.get(data.get('category'), '')}\n"
                                f"{('Text: ' + data['text']) if data.get('text') else ''}",
            "sensor.fault": "Der Sensor liefert keine gültigen Werte. Die Anzeige zeigt STATUS UNBEKANNT, bis wieder "
                            "verlässliche Messungen ankommen.",
            "gateway.offline": f"Seit über {OFFLINE_AFTER_S // 60} Minuten kommt keine Meldung vom Gateway "
                               f"„{data.get('device_name', '')}“. Die Anzeige zeigt STATUS UNBEKANNT.\n"
                               "Prüfen: Strom und Netzwerk des Raspberry Pi, dann auf dem Pi: sudo bike-agent doctor",
            "gateway.online": f"Das Gateway „{data.get('device_name', '')}“ meldet sich wieder.",
        }[name]
        body = (f"{lines}\n\nZeit: {data.get('occurred_at') or ''}\nStellplatz ansehen: {url}\n\n"
                "Einstellungen für E-Mails: Portal → Mein Konto → Benachrichtigungen")
        self.send(tenant_id, kind, subject, body)

    # ------------------------------------------------------------------ Gateway offline / wieder online
    def check_devices(self) -> int:
        now = self.core.clock()
        changed = 0
        rows = self.db.all(
            "SELECT d.*, s.name AS station_name FROM device d JOIN station s ON s.id = d.station_id "
            "WHERE d.revoked_at IS NULL AND d.last_seen_at IS NOT NULL")
        for d in rows:
            offline = now - d["last_seen_at"] > OFFLINE_AFTER_S
            data = {"station_id": d["station_id"], "station_name": d["station_name"], "device_id": d["id"],
                    "device_name": d["name"], "since": d["last_seen_at"],
                    "simulated": d["source"] == "simulator"}
            if offline and not d["offline_notified_at"]:
                self.db.execute("UPDATE device SET offline_notified_at = ? WHERE id = ?", (now, d["id"]))
                self.core.emit("gateway.offline", d["tenant_id"], data)
                changed += 1
            elif not offline and d["offline_notified_at"]:
                self.db.execute("UPDATE device SET offline_notified_at = NULL WHERE id = ?", (d["id"],))
                self.core.emit("gateway.online", d["tenant_id"], data)
                changed += 1
        return changed

    # ------------------------------------------------------------------ Berichte per E-Mail
    def send_due_reports(self, reports) -> int:
        """Tagesbericht ab 07:00 für gestern, Wochenbericht montags ab 07:00 für die Vorwoche."""
        from datetime import datetime, timedelta

        now = datetime.fromtimestamp(self.core.clock(), TZ)
        if now.hour < 7:
            return 0
        sent = 0
        yesterday = (now - timedelta(days=1)).date()
        last_week = (now - timedelta(days=7)).date()
        for t in self.db.all("SELECT * FROM tenant WHERE status = 'active'"):
            users = self.db.all("SELECT * FROM user WHERE tenant_id = ?", (t["id"],))
            wants = {p: [u for u in users if prefs_for(u)["report"] == p] for p in ("daily", "weekly")}
            jobs = [("day", yesterday.isoformat(), wants["daily"])]
            if now.weekday() == 0:
                jobs.append(("week", last_week.isoformat(), wants["weekly"]))
            for period, day, people in jobs:
                if not people or not reports.allowed(t):
                    continue
                if not self.once(f"report:{period}:{t['id']}:{day}", None):
                    continue
                rep = reports.build(t, period, day)
                pdf = reports.pdf(rep)
                for u in people:
                    self.core.mailer.send(u["email"], f"{self.core.s.product_name}: {rep['title']}", reports.mail_text(rep),
                                          attachments=[(reports.filename(rep, "pdf"), pdf, "application/pdf")])
                    sent += 1
        return sent
