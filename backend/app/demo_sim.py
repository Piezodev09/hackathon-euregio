"""Beispiel-Stellplatz mit Simulation – zum Ausprobieren ohne Hardware (Start-Tour, Vorführung).

Der Server erzeugt für Stationen mit `demo_sim = 1` Messungen und NFC-Vorgänge über dieselben Wege wie ein
echtes Gateway (Monitoring.ingest, Parking.tap). Alles ist als "simulated" gespeichert und wird in Portal,
Anzeige, Berichten, E-Mails und Webhooks als SIMULATION gekennzeichnet.

Ablauf je Stellplatz: frei (40–110 s) → Fahrrad kommt, Karte an den Leser (Check-in) → belegt (60–170 s,
gelegentlich kräftiges Rütteln → Warnung) → Karte an den Leser (Check-out) → frei …
"""

from __future__ import annotations

import random
import sqlite3

from .core import Core
from .schemas import MeasurementIn
from .security import hash_token, new_id, new_token
from .service import STALL_KEY

DEMO_UID = "04DE3000CAFE"
DEMO_CARD_LABEL = "Beispiel-Karte"


class DemoSimulator:
    def __init__(self, core: Core, monitoring, parking, reservations):
        self.core = core
        self.db = core.db
        self.monitoring = monitoring
        self.parking = parking
        self.reservations = reservations
        self.autorun = True
        self.state: dict[str, dict] = {}
        self.rnd = random.Random()

    # ------------------------------------------------------------------ Anlegen
    def create(self, tenant_id: str, actor: str, name: str = "Beispiel-Stellplatz (Simulation)") -> dict:
        now = self.core.clock()
        sid = new_id("st")
        display, stall = new_token("bsp_"), new_token("bss_")
        with self.db.tx() as c:
            c.execute("INSERT INTO station (id, tenant_id, name, location, created_at, demo_sim, display_token_hash, display_enabled, "
                      "stall_token_hash, stall_view_enabled) VALUES (?,?,?,?,?,1,?,1,?,1)",
                      (sid, tenant_id, name, "Simulation – keine echte Hardware", now, hash_token(display), hash_token(stall)))
            c.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,1)",
                      (new_id("sl"), sid, STALL_KEY, "Stellplatz"))
        card = self.parking.find_or_create_card(tenant_id, DEMO_UID, sid, label=DEMO_CARD_LABEL, status="active")
        if card["status"] == "pending":
            self.db.execute("UPDATE card SET status = 'active', label = ? WHERE id = ?", (DEMO_CARD_LABEL, card["id"]))
        if card["balance_cents"] <= 0:
            self.parking.topup(self.db.one("SELECT * FROM card WHERE id = ?", (card["id"],)), 1000,
                               note="Startguthaben Beispiel-Karte (Simulation)", actor="demo", source="simulated")
        self.core.audit("demo_station_created", tenant_id=tenant_id, actor=actor, target=sid)
        self.step_station(self.db.one("SELECT * FROM station WHERE id = ?", (sid,)))
        base = self.core.s.base_url
        return {"id": sid, "display_url": f"{base}/display#{display}", "stall_url": f"{base}/s#{stall}"}

    # ------------------------------------------------------------------ Simulation
    def step(self) -> int:
        n = 0
        for st in self.db.all("SELECT s.* FROM station s JOIN tenant t ON t.id = s.tenant_id "
                              "WHERE s.demo_sim = 1 AND t.status = 'active'"):
            try:
                self.step_station(st)
                n += 1
            except Exception:  # eine fehlerhafte Simulation darf die anderen nicht aufhalten
                import logging

                logging.getLogger("demo_sim").exception("Simulation fehlgeschlagen für %s", st["id"])
        return n

    def _seq(self, s: dict) -> int:
        s["seq"] = max(s.get("seq", 0) + 1, int(self.core.clock() * 1000))
        return s["seq"]

    def _tap(self, st: sqlite3.Row, s: dict) -> dict:
        return self.parking.tap(st, f"sim:{st['id']}", self._seq(s), DEMO_UID, 0, "simulated")

    def step_station(self, st: sqlite3.Row) -> None:
        now = self.core.clock()
        s = self.state.setdefault(st["id"], {"present": False, "next": now + self.rnd.uniform(15, 40), "shake": 0})
        vib = self.rnd.randint(0, 40)
        if now >= s["next"]:
            if s["present"]:
                self._tap(st, s)  # Check-out vor dem Ausparken
                s.update(present=False, next=now + self.rnd.uniform(40, 110))
                vib = self.rnd.randint(150, 280)
            else:
                blocked = st["maintenance"] or self.reservations.active(st["id"]) is not None
                if blocked:
                    s["next"] = now + 20
                else:
                    s.update(present=True, next=now + self.rnd.uniform(60, 170), arrived=now,
                             shake=1 if self.rnd.random() < 0.35 else 0)
                    vib = self.rnd.randint(150, 280)
                    self._tap(st, s)  # Check-in (bei Guthaben-Modus ggf. abgelehnt – dann parkt es ohne Vorgang)
        if s["present"] and s.get("shake") and now - s.get("arrived", now) > self.core.s.grace_period_s + 10:
            vib = self.rnd.randint(600, 900)
            s["shake"] += 1
            if s["shake"] > 4:
                s["shake"] = 0
        m = MeasurementIn(station_id=st["id"], sequence=self._seq(s), occupied=s["present"], vibration_score=vib,
                          sensor_state="ok", source="simulated", age_ms=0)
        self.monitoring.ingest(st, m)
        # Guthaben der Beispiel-Karte nie leer laufen lassen
        card = self.db.one("SELECT * FROM card WHERE tenant_id = ? AND label = ? AND status = 'active'", (st["tenant_id"], DEMO_CARD_LABEL))
        if card is not None and card["balance_cents"] < 100:
            self.parking.topup(card, 1000, note="Automatisch aufgeladen (Simulation)", actor="demo", source="simulated")
