"""Reservierung, Öffnungs-/Sperrzeiten, Wartungsmodus, Guthaben, E-Mail-Benachrichtigungen, Berichte,
API-Schlüssel/Webhooks, Beispiel-Stellplatz und Erste-Schritte."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.integrations import UrlRejected, check_url
from conftest import Device

UID_A, UID_B = "04AA000001", "04BB000002"


@pytest.fixture
def school(env):
    owner = env.register(plan="school")
    sid, token = env.station(owner, name="Schulhof")
    return env, owner, Device(owner, sid, token)


def tap(dev: Device, uid: str):
    dev.seq += 1
    return dev.api.c.post("/api/v1/nfc/tap", json={"station_id": dev.sid, "sequence": dev.seq, "uid": uid},
                          headers={"Authorization": f"Bearer {dev.token}"}).json()


def status(owner, sid):
    return owner.get(f"/api/v1/stations/{sid}/status").json()


def shake(env, dev):
    dev.send(occupied=True)
    env.clock.advance(20)
    created = False
    for _ in range(3):
        env.clock.advance(0.5)
        created |= dev.send(occupied=True, vib=700).json()["alert_created"]
    return created


# ---------------------------------------------------------------------- Reservierung
def test_reservation_shows_reserved_only_when_free_and_limits_checkin(school):
    env, owner, dev = school
    a = owner.post("/api/v1/cards", {"uid": UID_A, "label": "Frau A"}).json()
    owner.post("/api/v1/cards", {"uid": UID_B, "label": "Herr B"})
    # Ohne Messung: unbekannt – Reservierung ändert daran nichts
    r = owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 15, "card_id": a["id"], "label": "Besuch"})
    assert r.status_code == 201, r.text
    st = status(owner, dev.sid)
    assert st["state"] == "unknown" and st["reservation"]["card_label"] == "Frau A"
    dev.send(occupied=False)
    st = status(owner, dev.sid)
    assert st["state"] == "reserved" and st["presence"] == "free" and 0 < st["reservation"]["remaining_s"] <= 900
    # Zweite Reservierung nicht möglich; andere Karte wird abgewiesen, reservierte Karte erfüllt
    assert owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 10}).status_code == 409
    assert tap(dev, UID_B)["result"] == "reserved"
    assert tap(dev, UID_A)["result"] == "checked_in"
    res = owner.get("/api/v1/reservations").json()["reservations"][0]
    assert res["status"] == "fulfilled"
    # Belegt -> keine neue Reservierung
    assert owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 10}).json()["detail"] == "occupied"


def test_reservation_expires_and_cancel_and_roles(school):
    env, owner, dev = school
    dev.send(occupied=False)
    rid = owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 5}).json()["id"]
    viewer = env.invite(owner, "v@example.org", "viewer")
    assert viewer.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 5}).status_code == 403
    assert viewer.delete(f"/api/v1/reservations/{rid}").status_code == 403
    env.clock.advance(301)
    dev.send(occupied=False)
    assert status(owner, dev.sid)["state"] == "free"
    assert env.app.state.reservations.expire() == 1
    rid = owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 30}).json()["id"]
    assert owner.delete(f"/api/v1/reservations/{rid}").status_code == 200
    assert status(owner, dev.sid)["reservation"] is None
    assert owner.post(f"/api/v1/stations/{dev.sid}/reservations", {"minutes": 1000}).status_code == 422


def test_free_plan_has_no_reservations(env):
    owner = env.register()
    sid, token = env.station(owner)
    Device(owner, sid, token).send(occupied=False)
    r = owner.post(f"/api/v1/stations/{sid}/reservations", {"minutes": 10})
    assert r.status_code == 402 and r.json()["detail"]["feature"] == "reservations"


# ---------------------------------------------------------------------- Öffnungs- und Sperrzeiten
def test_opening_hours_and_closures_block_checkin_not_checkout(school):
    env, owner, dev = school
    owner.post("/api/v1/cards", {"uid": UID_A, "label": "A"})
    # Testuhr: Dienstag 23:13 Uhr (Europe/Berlin)
    assert owner.put(f"/api/v1/stations/{dev.sid}/hours", {"hours": {"tue": [["07:00", "23:00"]], "wed": [["07:00", "18:00"]]}}).status_code == 200
    closed = status(owner, dev.sid)["closed"]
    assert closed["reason"] == "hours" and closed["opens_at"].startswith("2023-11-15T06:00")  # 07:00 MEZ
    assert tap(dev, UID_A)["result"] == "closed"
    assert owner.put(f"/api/v1/stations/{dev.sid}/hours", {"hours": {"tue": [["18:00", "07:00"]]}}).status_code == 422
    assert owner.put(f"/api/v1/stations/{dev.sid}/hours", {"hours": None}).status_code == 200
    assert tap(dev, UID_A)["result"] == "checked_in"
    # Sperrzeit für alle Stellplätze (Ferien): kein Check-in, Auschecken geht
    r = owner.post("/api/v1/closures", {"starts_at": "2023-11-14T00:00", "ends_at": "2023-11-20T00:00", "note": "Herbstferien"})
    assert r.status_code == 201, r.text
    st = status(owner, dev.sid)
    assert st["closed"]["reason"] == "closure" and st["closed"]["note"] == "Herbstferien"
    assert tap(dev, UID_A)["result"] == "checked_out"
    assert tap(dev, UID_A)["result"] == "closed"
    assert owner.post("/api/v1/closures", {"starts_at": "2023-11-20T00:00", "ends_at": "2023-11-19T00:00"}).status_code == 422
    assert owner.delete(f"/api/v1/closures/{r.json()['id']}").status_code == 200
    assert status(owner, dev.sid)["closed"] is None


# ---------------------------------------------------------------------- Wartungsmodus
def test_maintenance_suppresses_alerts_and_sensor_faults(school):
    env, owner, dev = school
    owner.patch(f"/api/v1/stations/{dev.sid}", {"maintenance": True})
    assert shake(env, dev) is False
    dev.send(occupied=None, state="error")
    assert owner.get("/api/v1/events").json()["events"] == []
    owner.patch(f"/api/v1/stations/{dev.sid}", {"maintenance": False})
    env.clock.advance(60)
    assert shake(env, dev) is True


# ---------------------------------------------------------------------- Guthaben
def test_prepaid_balance_topup_fee_and_insufficient(school):
    env, owner, dev = school
    card = owner.post("/api/v1/cards", {"uid": UID_A, "label": "Guthabenkarte"}).json()
    owner.put("/api/v1/billing/tariff", {"mode": "per_hour", "price_cents": 50, "free_minutes": 0})
    assert owner.put("/api/v1/billing/payment-mode", {"mode": "prepaid"}).status_code == 200
    assert owner.get("/api/v1/billing/tariff").json()["payment_mode"] == "prepaid"
    r = tap(dev, UID_A)
    assert r["result"] == "insufficient_balance" and r["balance_cents"] == 0
    operator = env.invite(owner, "op@example.org", "operator")
    r = operator.post(f"/api/v1/cards/{card['id']}/topup", {"amount_cents": 200, "note": "bar im Sekretariat"})
    assert r.status_code == 200 and r.json()["balance_cents"] == 200
    assert operator.post(f"/api/v1/cards/{card['id']}/topup", {"amount_cents": -50, "kind": "correction"}).status_code == 403
    assert owner.post(f"/api/v1/cards/{card['id']}/topup", {"amount_cents": 0}).status_code == 422
    assert tap(dev, UID_A)["result"] == "checked_in"
    for _ in range(3):  # 3 angefangene Stunden (Portal-Sitzung dazwischen aktiv halten)
        env.clock.advance(2460)
        owner.me()
    r = tap(dev, UID_A)
    assert r["result"] == "checked_out" and r["amount_cents"] == 150 and r["balance_cents"] == 50
    assert status(owner, dev.sid)["last_tap"]["balance_cents"] == 50
    tx = owner.get(f"/api/v1/cards/{card['id']}/transactions").json()["transactions"]
    assert [t["kind"] for t in tx] == ["fee", "topup"] and tx[0]["amount_cents"] == -150
    # Im Portal beendeter Vorgang bucht ebenfalls ab
    assert tap(dev, UID_A)["result"] == "checked_in"
    env.clock.advance(1800)
    sid = owner.get("/api/v1/parking/sessions?status=open").json()["sessions"][0]["id"]
    owner.post(f"/api/v1/parking/sessions/{sid}/close")
    assert owner.get("/api/v1/cards").json()["cards"][0]["balance_cents"] == 0
    assert tap(dev, UID_A)["result"] == "insufficient_balance"


# ---------------------------------------------------------------------- E-Mail-Benachrichtigungen
def test_alert_mail_throttled_and_preferences(school):
    env, owner, dev = school
    viewer = env.invite(owner, "leser@example.org", "viewer")
    before = len(env.outbox)
    assert shake(env, dev) is True
    mails = env.outbox[before:]
    assert [m.to for m in mails] == [owner.email] and "Ungewöhnliche Bewegung" in mails[0].subject
    assert "kein Nachweis" in mails[0].body
    env.clock.advance(60)
    shake(env, dev)
    assert len(env.outbox) == before + 1  # gedrosselt
    # Leser schaltet Warnungen ein, Inhaber aus
    assert viewer.put("/api/v1/me/notifications", {"alert": True, "problem": False, "tech": False, "report": "off"}).status_code == 200
    owner.put("/api/v1/me/notifications", {"alert": False, "problem": True, "tech": True, "report": "weekly"})
    env.clock.advance(700)
    shake(env, dev)
    assert env.outbox[-1].to == "leser@example.org"
    assert owner.get("/api/v1/me/notifications").json()["prefs"]["alert"] is False


def test_gateway_offline_and_online_mail(school):
    env, owner, dev = school
    dev.send(occupied=False)
    notifier = env.app.state.notifier
    assert notifier.check_devices() == 0
    env.clock.advance(400)
    assert notifier.check_devices() == 1
    assert "Gateway offline" in env.outbox[-1].subject and "bike-agent doctor" in env.outbox[-1].body
    assert notifier.check_devices() == 0  # nur einmal
    env.clock.advance(31)
    dev.send(occupied=False)
    assert notifier.check_devices() == 1 and "wieder online" in env.outbox[-1].subject


def test_problem_report_mail(env):
    owner = env.register(plan="school")
    sid, _ = env.station(owner)
    token = owner.post(f"/api/v1/stations/{sid}/stall-link").json()["token"]
    r = env.client().c.post("/api/v1/public/stall/report", json={"category": "blocked", "text": "Roller steht davor"},
                            headers={"X-Stall-Token": token})
    assert r.status_code == 202
    assert "Problem gemeldet" in env.outbox[-1].subject and "Roller steht davor" in env.outbox[-1].body


# ---------------------------------------------------------------------- Berichte
def test_report_json_csv_pdf_and_mail(school):
    env, owner, dev = school
    owner.post("/api/v1/cards", {"uid": UID_A, "label": "A"})
    owner.put("/api/v1/billing/tariff", {"mode": "flat", "price_cents": 80, "free_minutes": 0})
    dev.send(occupied=False)
    env.clock.advance(20)
    dev.send(occupied=True)
    assert tap(dev, UID_A)["result"] == "checked_in"
    env.clock.advance(20)
    dev.send(occupied=True)
    assert tap(dev, UID_A)["result"] == "checked_out"
    rep = owner.get("/api/v1/reports?period=day&day=2023-11-14").json()
    row = rep["stations"][0]
    assert row["checkins"] == 1 and row["fees_cents"] == 80 and row["occupancy"] == 0.5 and not rep["contains_simulated"]
    assert rep["title"].startswith("Tagesbericht Dienstag, 14.11.2023")
    week = owner.get("/api/v1/reports?period=week&day=2023-11-14").json()
    assert week["label"].startswith("KW 46/2023") and week["totals"]["checkins"] == 1
    csv = owner.get("/api/v1/reports?period=day&day=2023-11-14&format=csv")
    assert csv.headers["content-type"].startswith("text/csv") and "Schulhof;50;" in csv.text and "0,80" in csv.text
    pdf = owner.get("/api/v1/reports?period=week&day=2023-11-14&format=pdf")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
    assert b"Wochenbericht" in pdf.content
    r = owner.post("/api/v1/reports/send?period=week&day=2023-11-14")
    assert r.status_code == 200 and env.outbox[-1].attachments[0][0].endswith(".pdf")
    free = env.register()
    assert free.get("/api/v1/reports").status_code == 402


def test_weekly_report_mail_on_monday(env):
    owner = env.register(plan="school")
    env.station(owner)
    notifier, reports = env.app.state.notifier, env.app.state.reports
    env.clock.t = 1_700_463_600.0  # Montag, 20.11.2023, 08:00 Uhr (Berlin)
    before = len(env.outbox)
    assert notifier.send_due_reports(reports) == 1
    mail = env.outbox[-1]
    assert mail.to == owner.email and "Wochenbericht KW 46" in mail.subject and mail.attachments[0][2] == "application/pdf"
    assert notifier.send_due_reports(reports) == 0  # nur einmal
    assert len(env.outbox) == before + 1


# ---------------------------------------------------------------------- API-Schlüssel und Webhooks
def test_api_key_scopes_and_external_reservation(school):
    env, owner, dev = school
    dev.send(occupied=False)
    read = owner.post("/api/v1/integrations/keys", {"name": "Schul-App", "scopes": ["read"]}).json()
    full = owner.post("/api/v1/integrations/keys", {"name": "Schul-App (Buchung)", "scopes": ["read", "reservations"]}).json()
    assert read["key"].startswith("sbk_") and "key" not in owner.get("/api/v1/integrations").json()["api_keys"][0]
    ext = env.client().c
    h = lambda k: {"Authorization": f"Bearer {k}"}  # noqa: E731
    r = ext.get("/api/v1/ext/stations", headers=h(read["key"]))
    assert r.status_code == 200 and r.json()["stations"][0]["state"] == "free"
    assert ext.get("/api/v1/ext/stations", headers=h("sbk_falsch")).status_code == 401
    assert ext.post(f"/api/v1/ext/stations/{dev.sid}/reservations", json={"minutes": 15}, headers=h(read["key"])).status_code == 403
    r = ext.post(f"/api/v1/ext/stations/{dev.sid}/reservations", json={"minutes": 15, "label": "Frau A"}, headers=h(full["key"]))
    assert r.status_code == 201 and r.json()["via"] == "api"
    assert ext.get(f"/api/v1/ext/stations/{dev.sid}", headers=h(read["key"])).json()["state"] == "reserved"
    assert ext.delete(f"/api/v1/ext/reservations/{r.json()['id']}", headers=h(full["key"])).status_code == 200
    # Fremde Organisation sieht nichts
    other = env.register(org="Fremd", plan="school")
    okey = other.post("/api/v1/integrations/keys", {"name": "x", "scopes": ["read"]}).json()["key"]
    assert ext.get(f"/api/v1/ext/stations/{dev.sid}", headers=h(okey)).status_code == 404
    assert owner.delete(f"/api/v1/integrations/keys/{read['id']}").status_code == 200
    assert ext.get("/api/v1/ext/stations", headers=h(read["key"])).status_code == 401
    # Free-Tarif: keine Integrationen
    assert env.register().post("/api/v1/integrations/keys", {"name": "x", "scopes": ["read"]}).status_code == 402


def test_webhook_signed_delivery_and_ssrf_protection(school, monkeypatch):
    env, owner, dev = school
    sent = []
    integ = env.app.state.integrations
    monkeypatch.setattr(integ, "transport", lambda url, headers, body: sent.append((url, headers, body)) or 204)
    monkeypatch.setattr("app.integrations.check_url", lambda *a, **k: None)
    w = owner.post("/api/v1/integrations/webhooks", {"url": "https://schul-app.example.org/hook",
                                                     "events": ["alert.created", "stall.changed"]}).json()
    assert w["secret"].startswith("whsec_")
    dev.send(occupied=False)
    env.clock.advance(5)
    dev.send(occupied=True)  # frei -> belegt
    assert shake(env, dev) is True
    events = [h["X-SBB-Event"] for _, h, _ in sent]
    assert events == ["stall.changed", "alert.created"]
    url, headers, body = sent[-1]
    expected = "sha256=" + hmac.new(w["secret"].encode(), headers["X-SBB-Timestamp"].encode() + b"." + body, hashlib.sha256).hexdigest()
    assert headers["X-SBB-Signature"] == expected
    payload = json.loads(body)
    assert payload["event"] == "alert.created" and payload["data"]["station_name"] == "Schulhof"
    r = owner.post(f"/api/v1/integrations/webhooks/{w['id']}/test").json()
    assert r["deliveries"][0]["ok"] is True and r["deliveries"][0]["status_code"] == 204
    assert owner.patch(f"/api/v1/integrations/webhooks/{w['id']}", {"active": False}).json()["active"] is False


def test_webhook_url_checks():
    for url in ("http://127.0.0.1/x", "https://169.254.169.254/latest", "https://localhost/x", "ftp://example.org/x",
                "https://user:pw@example.org/x"):
        with pytest.raises(UrlRejected):
            check_url(url, allow_private=True, allow_http=True)
    with pytest.raises(UrlRejected):
        check_url("https://10.0.0.5/hook", allow_private=False, allow_http=False)
    with pytest.raises(UrlRejected):
        check_url("http://10.0.0.5/hook", allow_private=True, allow_http=False)
    check_url("https://10.0.0.5/hook", allow_private=True, allow_http=False)


# ---------------------------------------------------------------------- Beispiel-Stellplatz und Erste Schritte
def test_demo_station_simulation_and_checklist(env):
    owner = env.register(plan="school")
    ob = owner.get("/api/v1/onboarding").json()
    assert ob["done"] == 0 and not ob["hidden"] and {s["id"] for s in ob["steps"]} >= {"station", "gateway", "cards", "tariff"}
    r = owner.post("/api/v1/onboarding/demo-station")
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert owner.post("/api/v1/onboarding/demo-station").status_code == 409
    demo = env.app.state.demo
    for _ in range(120):  # ~4 min Simulation in 2-s-Schritten
        env.clock.advance(2)
        demo.step()
    st = status(owner, sid)
    assert st["simulated_data"] is True and st["state"] in ("free", "occupied")
    sessions = owner.get("/api/v1/parking/sessions").json()["sessions"]
    assert sessions and all(s["simulated"] for s in sessions)
    station = [s for s in owner.get("/api/v1/stations").json()["stations"] if s["id"] == sid][0]
    assert station["demo"] is True
    ob = owner.get("/api/v1/onboarding").json()
    assert [s for s in ob["steps"] if s["id"] == "demo"][0]["done"] is True
    assert [s for s in ob["steps"] if s["id"] == "station"][0]["done"] is False  # Beispiel zählt nicht als echter Stellplatz
    assert owner.post("/api/v1/onboarding/hide", {"hidden": True}).json()["hidden"] is True
    assert owner.me().json()["tenant"]["onboarding_hidden"] is True
