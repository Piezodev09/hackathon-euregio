"""NFC-Ein-/Auschecken und Parkvorgänge.

Datenschutz: Die Karten-UID wird nur als HMAC (Schlüssel = Datenschlüssel der Plattform, getrennt je
Organisation) gespeichert. Unbekannte Karten werden als "pending" angelegt und erst nach Freigabe durch
einen Admin nutzbar ("Karte anlernen").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3

from . import billing
from .core import Core
from .plans import get_plan
from .security import new_id
from .billing import iso as _iso

UID_RE = re.compile(r"^[0-9A-F]{8,20}$")
TAP_MAX_AGE_S = 60  # ältere, nachgesendete Taps werden nicht mehr ausgeführt

RESULTS = ("checked_in", "checked_out", "unknown_card", "blocked", "occupied_by_other", "open_elsewhere", "expired",
           "duplicate", "feature_disabled", "maintenance")


def normalize_uid(uid: str) -> str:
    u = re.sub(r"[\s:\-]", "", uid or "").upper()
    if not UID_RE.match(u):
        raise ValueError("invalid_uid")
    return u


def uid_hmac(core: Core, tenant_id: str, uid: str) -> str:
    return hmac.new(core.s.data_key, f"nfc:{tenant_id}:{normalize_uid(uid)}".encode(), hashlib.sha256).hexdigest()


def tariff_for(core: Core, station: sqlite3.Row) -> dict:
    """Tarif des Stellplatzes, sonst der Organisation, sonst Standard. Ohne Abrechnungs-Funktion: kostenlos."""
    tenant = core.db.one("SELECT plan, tariff FROM tenant WHERE id = ?", (station["tenant_id"],))
    if not get_plan(tenant["plan"]).parking_billing:
        return billing.normalize_tariff({"mode": "free", "price_cents": 0})
    return billing.load_tariff(station["tariff"] or tenant["tariff"])


class Parking:
    def __init__(self, core: Core):
        self.core = core
        self.db = core.db

    # ------------------------------------------------------------------ Karten
    def find_or_create_card(self, tenant_id: str, uid: str, station_id: str | None = None,
                            label: str = "", status: str = "pending") -> sqlite3.Row:
        h = uid_hmac(self.core, tenant_id, uid)
        now = self.core.clock()
        self.db.execute(
            "INSERT OR IGNORE INTO card (id, tenant_id, uid_hmac, label, status, created_at, last_seen_at, last_station_id) "
            "VALUES (?,?,?,?,?,?,?,?)", (new_id("card"), tenant_id, h, label, status, now, now, station_id))
        return self.db.one("SELECT * FROM card WHERE tenant_id = ? AND uid_hmac = ?", (tenant_id, h))

    def open_session(self, station_id: str) -> sqlite3.Row | None:
        return self.db.one("SELECT * FROM parking_session WHERE station_id = ? AND status = 'open' ORDER BY started_at DESC LIMIT 1",
                           (station_id,))

    # ------------------------------------------------------------------ Tap
    def tap(self, station: sqlite3.Row, device_id: str, sequence: int, uid: str, age_s: float, source: str) -> dict:
        now = self.core.clock()
        t = now - age_s
        tenant_id = station["tenant_id"]

        def log(result: str, card_id=None, amount=None) -> dict:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO nfc_tap (tenant_id, station_id, device_id, sequence, card_id, at, result, amount_cents, source) "
                "VALUES (?,?,?,?,?,?,?,?,?)", (tenant_id, station["id"], device_id, sequence, card_id, t, result, amount, source))
            if cur.rowcount == 0:
                prev = self.db.one("SELECT result, amount_cents FROM nfc_tap WHERE device_id = ? AND sequence = ?", (device_id, sequence))
                return {"result": "duplicate", "previous": prev["result"], "amount_cents": prev["amount_cents"]}
            return {"result": result, "amount_cents": amount}

        if self.db.one("SELECT 1 FROM nfc_tap WHERE device_id = ? AND sequence = ?", (device_id, sequence)):
            return log("duplicate")
        plan = get_plan(self.db.scalar("SELECT plan FROM tenant WHERE id = ?", (tenant_id,)))
        if not plan.nfc:
            return log("feature_disabled")
        if age_s > TAP_MAX_AGE_S:
            return log("expired")
        if station["maintenance"]:
            return log("maintenance")

        card = self.find_or_create_card(tenant_id, uid, station["id"])
        self.db.execute("UPDATE card SET last_seen_at = ?, last_station_id = ? WHERE id = ?", (now, station["id"], card["id"]))
        if card["status"] == "pending":
            return {**log("unknown_card", card["id"]), "card_id": card["id"]}
        if card["status"] == "blocked":
            return log("blocked", card["id"])

        with self.db.tx() as c:
            current = c.execute("SELECT * FROM parking_session WHERE station_id = ? AND status = 'open' LIMIT 1", (station["id"],)).fetchone()
            if current is not None and current["card_id"] == card["id"]:
                tariff = json.loads(current["tariff"])
                amount = billing.compute_fee(current["started_at"], t, tariff)
                c.execute("UPDATE parking_session SET status = 'closed', ended_at = ?, amount_cents = ?, closed_by = 'nfc' WHERE id = ?",
                          (t, amount, current["id"]))
                result, amt, sid = "checked_out", amount, current["id"]
            elif current is not None:
                result, amt, sid = "occupied_by_other", None, None
            elif c.execute("SELECT 1 FROM parking_session WHERE card_id = ? AND status = 'open'", (card["id"],)).fetchone():
                result, amt, sid = "open_elsewhere", None, None
            else:
                sid = new_id("ps")
                c.execute("INSERT INTO parking_session (id, tenant_id, station_id, card_id, started_at, tariff, status, source) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (sid, tenant_id, station["id"], card["id"], t, json.dumps(tariff_for(self.core, station)), "open", source))
                result, amt = "checked_in", None
        out = log(result, card["id"], amt)
        if sid:
            out["session_id"] = sid
        return out

    # ------------------------------------------------------------------ Ausgabe
    def session_out(self, s: sqlite3.Row, *, public: bool = False) -> dict:
        now = self.core.clock()
        tariff = json.loads(s["tariff"])
        running = s["status"] == "open"
        amount = billing.compute_fee(s["started_at"], now, tariff) if running else s["amount_cents"]
        out = {"id": s["id"], "station_id": s["station_id"], "started_at": _iso(s["started_at"]), "ended_at": _iso(s["ended_at"]),
               "status": s["status"], "amount_cents": amount, "running": running, "tariff": tariff,
               "duration_s": round((now if running else s["ended_at"] or now) - s["started_at"]),
               "simulated": s["source"] == "simulated"}
        if not public:
            card = self.db.one("SELECT label FROM card WHERE id = ?", (s["card_id"],))
            out.update({"card_id": s["card_id"], "card_label": card["label"] if card else "", "closed_by": s["closed_by"]})
        return out

    def last_tap(self, station_id: str, within_s: float = 20) -> dict | None:
        r = self.db.one("SELECT at, result, amount_cents FROM nfc_tap WHERE station_id = ? AND at >= ? ORDER BY at DESC, id DESC LIMIT 1",
                        (station_id, self.core.clock() - within_s))
        return None if r is None else {"at": _iso(r["at"]), "result": r["result"], "amount_cents": r["amount_cents"]}

    # ------------------------------------------------------------------ Monatsabrechnung
    def statements(self, tenant_id: str, month: str) -> list[dict]:
        start, end = billing.month_bounds(month)
        rows = self.db.all(
            "SELECT c.id AS card_id, c.label, COUNT(s.id) AS n, COALESCE(SUM(s.amount_cents), 0) AS total, "
            "MAX(s.source = 'simulated') AS simulated FROM parking_session s JOIN card c ON c.id = s.card_id "
            "WHERE s.tenant_id = ? AND s.status = 'closed' AND s.ended_at >= ? AND s.ended_at < ? GROUP BY c.id ORDER BY c.label",
            (tenant_id, start, end))
        paid = {r["card_id"]: r["paid_at"] for r in self.db.all(
            "SELECT card_id, paid_at FROM statement_payment WHERE tenant_id = ? AND month = ?", (tenant_id, month))}
        return [{"card_id": r["card_id"], "card_label": r["label"], "sessions": r["n"], "total_cents": r["total"],
                 "paid_at": _iso(paid.get(r["card_id"])), "simulated": bool(r["simulated"])} for r in rows]

