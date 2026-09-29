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
from .hours import closed_info
from .plans import get_plan
from .security import new_id
from .billing import iso as _iso

UID_RE = re.compile(r"^[0-9A-F]{8,20}$")
TAP_MAX_AGE_S = 60  # ältere, nachgesendete Taps werden nicht mehr ausgeführt

RESULTS = ("checked_in", "checked_out", "unknown_card", "blocked", "occupied_by_other", "open_elsewhere", "expired",
           "duplicate", "feature_disabled", "maintenance", "closed", "reserved", "insufficient_balance", "learned")
LEARN_WINDOW_S = 60  # Anlern-Modus: so lange wartet der Server auf die nächste Karte an einem Leser der Organisation
UID_FORMATS = ("hex", "dec", "dec_rev")
TOPUP_MAX_CENTS = 50_000


def normalize_uid(uid: str) -> str:
    u = re.sub(r"[\s:\-]", "", uid or "").upper()
    if not UID_RE.match(u):
        raise ValueError("invalid_uid")
    return u


def uid_from(value: str, fmt: str = "hex") -> str:
    """UID in Hex umrechnen. USB-Leser im Tastaturmodus tippen die UID oft als Dezimalzahl: "dec" = Bytes in
    Lese-Reihenfolge, "dec_rev" = Bytes umgekehrt (häufig bei 10-stelliger Ausgabe, z. B. 0012345678)."""
    if fmt == "hex":
        return normalize_uid(value)
    v = re.sub(r"\s", "", value or "")
    if not v.isdigit() or len(v) > 20:
        raise ValueError("invalid_uid")
    n = int(v)
    length = 4 if n < 2**32 else 7 if n < 2**56 else 10
    if n >= 2 ** (8 * length):
        raise ValueError("invalid_uid")
    raw = n.to_bytes(length, "big")
    if fmt == "dec_rev":
        raw = raw[::-1]
    elif fmt != "dec":
        raise ValueError("invalid_uid_format")
    return normalize_uid(raw.hex())


def uid_hmac(core: Core, tenant_id: str, uid: str) -> str:
    return hmac.new(core.s.data_key, f"nfc:{tenant_id}:{normalize_uid(uid)}".encode(), hashlib.sha256).hexdigest()


def prepaid(core: Core, tenant_id: str) -> bool:
    t = core.db.one("SELECT plan, payment_mode FROM tenant WHERE id = ?", (tenant_id,))
    return bool(t and t["payment_mode"] == "prepaid" and get_plan(t["plan"]).parking_billing)


def tariff_for(core: Core, station: sqlite3.Row) -> dict:
    """Tarif des Stellplatzes, sonst der Organisation, sonst Standard. Ohne Abrechnungs-Funktion: kostenlos."""
    tenant = core.db.one("SELECT plan, tariff FROM tenant WHERE id = ?", (station["tenant_id"],))
    if not get_plan(tenant["plan"]).parking_billing:
        return billing.normalize_tariff({"mode": "free", "price_cents": 0})
    return billing.load_tariff(station["tariff"] or tenant["tariff"])


class Parking:
    def __init__(self, core: Core, reservations=None):
        self.core = core
        self.db = core.db
        self.reservations = reservations

    # ------------------------------------------------------------------ Guthaben
    def book(self, c: sqlite3.Connection, card: sqlite3.Row, kind: str, amount: int, *, session_id=None, note="",
             actor=None, source="live") -> int:
        """Buchung auf dem Guthaben der Karte (innerhalb einer Transaktion). Gibt den neuen Stand zurück."""
        bal = c.execute("SELECT balance_cents FROM card WHERE id = ?", (card["id"],)).fetchone()[0] + amount
        c.execute("UPDATE card SET balance_cents = ? WHERE id = ?", (bal, card["id"]))
        c.execute("INSERT INTO card_txn (id, tenant_id, card_id, at, kind, amount_cents, balance_after, session_id, note, actor, source) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (new_id("tx"), card["tenant_id"], card["id"], self.core.clock(), kind, amount, bal, session_id, note, actor, source))
        return bal

    def topup(self, card: sqlite3.Row, amount: int, *, note: str, actor: str, kind: str = "topup", source: str = "live") -> int:
        if kind == "topup" and not 0 < amount <= TOPUP_MAX_CENTS:
            raise ValueError("invalid_amount")
        if kind == "correction" and not (amount != 0 and abs(amount) <= TOPUP_MAX_CENTS):
            raise ValueError("invalid_amount")
        with self.db.tx() as c:
            return self.book(c, card, kind, amount, note=note, actor=actor, source=source)

    def transactions(self, card_id: str, limit: int = 100) -> list[dict]:
        return [{"id": r["id"], "at": _iso(r["at"]), "kind": r["kind"], "amount_cents": r["amount_cents"],
                 "balance_after": r["balance_after"], "note": r["note"], "actor": r["actor"], "session_id": r["session_id"],
                 "simulated": r["source"] == "simulated"}
                for r in self.db.all("SELECT * FROM card_txn WHERE card_id = ? ORDER BY at DESC, rowid DESC LIMIT ?", (card_id, limit))]

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
    def tap(self, station: sqlite3.Row, device_id: str, sequence: int, uid: str, age_s: float, source: str,
            reader: str | None = None) -> dict:
        now = self.core.clock()
        t = now - age_s
        tenant_id = station["tenant_id"]

        def log(result: str, card_id=None, amount=None) -> dict:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO nfc_tap (tenant_id, station_id, device_id, sequence, card_id, at, result, amount_cents, source, reader) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", (tenant_id, station["id"], device_id, sequence, card_id, t, result, amount, source, reader))
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

        learn = self.db.one("SELECT * FROM card_learn WHERE tenant_id = ? AND expires_at >= ? AND card_id IS NULL", (tenant_id, now))
        if learn is not None and source == "live":
            return self._learn(learn, log, tenant_id, uid, station["id"])

        card = self.find_or_create_card(tenant_id, uid, station["id"])
        self.db.execute("UPDATE card SET last_seen_at = ?, last_station_id = ? WHERE id = ?", (now, station["id"], card["id"]))
        if card["status"] == "pending":
            return {**log("unknown_card", card["id"]), "card_id": card["id"]}
        if card["status"] == "blocked":
            return log("blocked", card["id"])

        # Vor dem Check-in: Öffnungszeiten, Reservierung, Guthaben (Auschecken ist immer möglich).
        current = self.open_session(station["id"])
        checking_out = current is not None and current["card_id"] == card["id"]
        reservation = self.reservations.active(station["id"]) if self.reservations else None
        tariff = tariff_for(self.core, station)
        is_prepaid = prepaid(self.core, tenant_id)
        if not checking_out and current is None:
            if closed_info(self.db, station, now):
                return log("closed", card["id"])
            if reservation is not None and reservation["card_id"] != card["id"]:
                return log("reserved", card["id"])
            if is_prepaid and tariff["mode"] != "free" and card["balance_cents"] <= 0:
                return {**log("insufficient_balance", card["id"]), "balance_cents": card["balance_cents"]}

        balance = None
        with self.db.tx() as c:
            current = c.execute("SELECT * FROM parking_session WHERE station_id = ? AND status = 'open' LIMIT 1", (station["id"],)).fetchone()
            if current is not None and current["card_id"] == card["id"]:
                amount = billing.compute_fee(current["started_at"], t, json.loads(current["tariff"]))
                c.execute("UPDATE parking_session SET status = 'closed', ended_at = ?, amount_cents = ?, closed_by = 'nfc' WHERE id = ?",
                          (t, amount, current["id"]))
                result, amt, sid = "checked_out", amount, current["id"]
                if is_prepaid and amount:
                    balance = self.book(c, card, "fee", -amount, session_id=sid, note=station["name"], source=source)
            elif current is not None:
                result, amt, sid = "occupied_by_other", None, None
            elif c.execute("SELECT 1 FROM parking_session WHERE card_id = ? AND status = 'open'", (card["id"],)).fetchone():
                result, amt, sid = "open_elsewhere", None, None
            else:
                sid = new_id("ps")
                c.execute("INSERT INTO parking_session (id, tenant_id, station_id, card_id, started_at, tariff, status, source) "
                          "VALUES (?,?,?,?,?,?,?,?)",
                          (sid, tenant_id, station["id"], card["id"], t, json.dumps(tariff), "open", source))
                result, amt = "checked_in", None
        if result == "checked_in" and reservation is not None:
            self.reservations.end(reservation, "fulfilled")
        if is_prepaid and balance is None and result in ("checked_in", "checked_out"):
            balance = self.db.scalar("SELECT balance_cents FROM card WHERE id = ?", (card["id"],))
        out = log(result, card["id"], amt)
        if balance is not None:
            out["balance_cents"] = balance
            self.db.execute("UPDATE nfc_tap SET balance_cents = ? WHERE device_id = ? AND sequence = ?", (balance, device_id, sequence))
        if sid:
            out["session_id"] = sid
        if result in ("checked_in", "checked_out"):
            self.core.emit("parking." + result, tenant_id, {
                "station_id": station["id"], "station_name": station["name"], "session_id": sid, "card_id": card["id"],
                "card_label": card["label"], "amount_cents": amt, "simulated": source == "simulated"})
        return out

    # ------------------------------------------------------------------ Anlern-Modus
    def _learn(self, learn: sqlite3.Row, log, tenant_id: str, uid: str, station_id: str) -> dict:
        """Nächste Karte an irgendeinem Leser der Organisation wird benannt und freigegeben (kein Check-in)."""
        card = self.find_or_create_card(tenant_id, uid, station_id, label=learn["label"], status="active")
        known = card["status"] != "pending" and card["label"] and card["label"] != learn["label"]
        if not known:
            self.db.execute("UPDATE card SET status = 'active', label = ?, last_seen_at = ?, last_station_id = ? WHERE id = ?",
                            (learn["label"], self.core.clock(), station_id, card["id"]))
        self.db.execute("UPDATE card_learn SET card_id = ?, outcome = ?, station_id = ? WHERE tenant_id = ?",
                        (card["id"], "known" if known else "learned", station_id, tenant_id))
        self.core.audit("card_learned", tenant_id=tenant_id, user_id=learn["created_by"], actor="nfc", target=card["id"],
                        detail={"known": bool(known)})
        return {**log("learned", card["id"]), "card_id": card["id"]}

    def start_learn(self, tenant_id: str, label: str, user_id: str) -> dict:
        now = self.core.clock()
        self.db.execute("INSERT INTO card_learn (tenant_id, label, created_by, created_at, expires_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET label = excluded.label, created_by = excluded.created_by, "
                        "created_at = excluded.created_at, expires_at = excluded.expires_at, card_id = NULL, outcome = NULL, station_id = NULL",
                        (tenant_id, label, user_id, now, now + LEARN_WINDOW_S))
        return self.learn_status(tenant_id)

    def learn_status(self, tenant_id: str) -> dict:
        r = self.db.one("SELECT * FROM card_learn WHERE tenant_id = ?", (tenant_id,))
        now = self.core.clock()
        if r is None:
            return {"state": "idle"}
        if r["card_id"]:
            card = self.db.one("SELECT id, label, status FROM card WHERE id = ?", (r["card_id"],))
            st = self.db.scalar("SELECT name FROM station WHERE id = ?", (r["station_id"],))
            return {"state": r["outcome"], "label": r["label"], "station_name": st,
                    "card": {"id": card["id"], "label": card["label"], "status": card["status"]} if card else None}
        if r["expires_at"] < now:
            return {"state": "expired", "label": r["label"]}
        return {"state": "waiting", "label": r["label"], "expires_at": _iso(r["expires_at"]), "seconds_left": round(r["expires_at"] - now)}

    def cancel_learn(self, tenant_id: str) -> None:
        self.db.execute("DELETE FROM card_learn WHERE tenant_id = ?", (tenant_id,))

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
        r = self.db.one("SELECT at, result, amount_cents, balance_cents FROM nfc_tap WHERE station_id = ? AND at >= ? "
                        "ORDER BY at DESC, id DESC LIMIT 1", (station_id, self.core.clock() - within_s))
        return None if r is None else {"at": _iso(r["at"]), "result": r["result"], "amount_cents": r["amount_cents"],
                                       "balance_cents": r["balance_cents"]}

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

