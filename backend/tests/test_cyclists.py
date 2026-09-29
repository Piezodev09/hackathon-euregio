"""Anlagen, Karten-App (Karten-Link), Warteliste mit Web-Push und öffentliche Status-Seite."""

from __future__ import annotations

import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app import webpush
from app.integrations import UrlRejected
from conftest import Device
from test_features import tap

UID_A, UID_B, UID_C = "04AA000001", "04BB000002", "04CC000003"


def school_with(env, n=2):
    owner = env.register(plan="school")
    devs = []
    for i in range(n):
        sid, token = env.station(owner, name=f"Platz {i + 1}")
        devs.append(Device(owner, sid, token))
    return owner, devs


def card(owner, uid, label):
    r = owner.post("/api/v1/cards", {"uid": uid, "label": label})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def holder(env, owner, card_id):
    link = owner.post(f"/api/v1/cards/{card_id}/link").json()
    token = link["url"].split("#", 1)[1]
    c = env.client()
    h = {"X-Card-Token": token}
    return c, h, token


# ---------------------------------------------------------------------- Anlagen
def test_site_counts_never_unknown_as_free(env):
    owner, (a, b, c) = school_with(env, 3)
    r = owner.post("/api/v1/sites", {"name": "Schulhof", "station_ids": [a.sid, b.sid, c.sid]})
    assert r.status_code == 201, r.text
    site = r.json()
    assert site["total"] == 3 and site["free"] == 0 and site["counts"]["unknown"] == 3  # noch keine Messung
    a.send(occupied=False)
    b.send(occupied=True)
    owner.patch(f"/api/v1/stations/{c.sid}", {"maintenance": True})
    c.send(occupied=False)
    s = owner.get("/api/v1/sites").json()["sites"][0]
    assert s["free"] == 1 and s["counts"] == {"available": 1, "occupied": 1, "reserved": 0, "closed": 1, "unknown": 0}
    env.clock.advance(31)  # Messungen veraltet -> unbekannt, nie frei
    owner.me()
    assert owner.get("/api/v1/sites").json()["sites"][0]["free"] == 0
    # öffentliche Großanzeige
    link = owner.post(f"/api/v1/sites/{site['id']}/display-link").json()
    tok = link["url"].split("#")[1]
    pub = env.client().c.get("/api/v1/public/site/status", headers={"X-Site-Token": tok})
    assert pub.status_code == 200 and pub.json()["total"] == 3 and "hold_minutes" not in pub.json()
    assert env.client().c.get("/api/v1/public/site/status", headers={"X-Site-Token": "bsa_falsch"}).status_code == 404
    # neuer Link macht den alten ungültig; Stellplatz nur in einer Anlage
    owner.post(f"/api/v1/sites/{site['id']}/display-link")
    assert env.client().c.get("/api/v1/public/site/status", headers={"X-Site-Token": tok}).status_code == 404
    other = owner.post("/api/v1/sites", {"name": "Nord", "station_ids": [a.sid]}).json()
    assert other["total"] == 1 and owner.get("/api/v1/sites").json()["sites"][1]["total"] == 2
    # Mandantentrennung
    fremd = env.register(org="Fremd", email="f@example.org", plan="school")
    assert fremd.post("/api/v1/sites", {"name": "X", "station_ids": [a.sid]}).status_code == 404
    assert fremd.patch(f"/api/v1/sites/{site['id']}", {"name": "X"}).status_code == 404
    assert owner.delete(f"/api/v1/sites/{other['id']}").status_code == 200


def test_site_display_needs_plan(env):
    owner = env.register(plan="free")
    sid, _ = env.station(owner)
    s = owner.post("/api/v1/sites", {"name": "A", "station_ids": [sid]}).json()
    env.set_plan(owner, "free")
    # Free hat public_display -> erlaubt; Warteliste braucht Reservierungen
    assert owner.post(f"/api/v1/sites/{s['id']}/display-link").status_code == 200
    assert owner.patch(f"/api/v1/sites/{s['id']}", {"waitlist_enabled": True}).status_code == 402


# ---------------------------------------------------------------------- Karten-App
def test_card_app_link_shows_only_own_data(env):
    owner, (a, b) = school_with(env)
    ca = card(owner, UID_A, "Karte 7a")
    card(owner, UID_B, "Karte 7b")
    c, h, token = holder(env, owner, ca)
    a.send(occupied=False)
    assert tap(a, UID_A)["result"] == "checked_in"
    body = c.c.get("/api/v1/public/card", headers=h).json()
    assert body["card"]["label"] == "Karte 7a" and body["session"]["station_name"] == "Platz 1"
    text = json.dumps(body)
    assert "Karte 7b" not in text and UID_A not in text and "card_id" not in text
    assert body["features"]["reserve"] is False  # Organisation hat Selbst-Reservierung nicht erlaubt
    # neuer Link sperrt den alten; gesperrte Karte -> 404
    owner.post(f"/api/v1/cards/{ca}/link")
    assert c.c.get("/api/v1/public/card", headers=h).status_code == 404
    c2, h2, _ = holder(env, owner, ca)
    owner.patch(f"/api/v1/cards/{ca}", {"status": "blocked"})
    assert c2.c.get("/api/v1/public/card", headers=h2).status_code == 404
    assert c2.c.get("/api/v1/public/card", headers={"X-Card-Token": "falsch"}).status_code == 404
    # Rollen: Lesende dürfen keine Links erzeugen
    viewer = env.invite(owner, "leser@example.org", "viewer")
    assert viewer.post(f"/api/v1/cards/{ca}/link").status_code == 403


def test_card_app_self_reservation_rules(env):
    owner, (a, b) = school_with(env)
    ca = card(owner, UID_A, "A")
    cb = card(owner, UID_B, "B")
    c, h, _ = holder(env, owner, ca)
    a.send(occupied=False)
    assert c.c.post("/api/v1/public/card/reservations", json={"station_id": a.sid}, headers=h).status_code == 403
    owner.patch("/api/v1/org", {"cyclist_reserve": True})
    assert c.c.post("/api/v1/public/card/reservations", json={"station_id": b.sid}, headers=h).status_code == 409  # unbekannt
    r = c.c.post("/api/v1/public/card/reservations", json={"station_id": a.sid, "minutes": 15}, headers=h)
    assert r.status_code == 201 and r.json()["station_name"] == "Platz 1"
    assert c.c.post("/api/v1/public/card/reservations", json={"station_id": a.sid, "minutes": 60}, headers=h).status_code == 422
    b.send(occupied=False)
    assert c.c.post("/api/v1/public/card/reservations", json={"station_id": b.sid}, headers=h).status_code == 409  # nur eine
    assert tap(a, UID_B)["result"] == "reserved"  # andere Karte
    assert tap(a, UID_A)["result"] == "checked_in"  # erfüllt die Reservierung
    fremd = env.register(org="Fremd", email="f2@example.org", plan="school")
    fsid, _ = env.station(fremd)
    c2, h2, _ = holder(env, owner, cb)
    assert c2.c.post("/api/v1/public/card/reservations", json={"station_id": fsid}, headers=h2).status_code == 404


# ---------------------------------------------------------------------- Warteliste + Push
def browser_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    auth = os.urandom(16)
    return key, webpush.b64u(webpush.public_bytes(key.public_key())), webpush.b64u(auth), auth


def decrypt(body: bytes, ua_key, auth: bytes) -> bytes:
    """Gegenstück nach RFC 8291 (Browser-Seite) – beweist, dass die Verschlüsselung dem Standard folgt."""
    salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    ua_pub = webpush.public_bytes(ua_key.public_key())
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    hk = lambda salt_, info, n, ikm: HKDF(hashes.SHA256(), n, salt_, info).derive(ikm)  # noqa: E731
    ikm = hk(auth, b"WebPush: info\x00" + ua_pub + as_pub, 32, shared)
    cek = hk(salt, b"Content-Encoding: aes128gcm\x00", 16, ikm)
    nonce = hk(salt, b"Content-Encoding: nonce\x00", 12, ikm)
    plain = AESGCM(cek).decrypt(nonce, ct, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def test_webpush_encryption_and_vapid():
    ua_key, p256dh, auth_b64, auth = browser_keys()
    msg = b'{"title":"Platz frei!"}'
    assert decrypt(webpush.encrypt(msg, p256dh, auth_b64), ua_key, auth) == msg
    v = webpush.Vapid(ec.generate_private_key(ec.SECP256R1()), "mailto:ops@example.org")
    h = v.headers("https://push.example.net/send/abc", 1_700_000_000)["Authorization"]
    jwt = h.split("t=")[1].split(",")[0]
    head, claims, sig = jwt.split(".")
    c = json.loads(webpush.b64u_dec(claims))
    assert c == {"aud": "https://push.example.net", "exp": 1_700_000_000 + 43200, "sub": "mailto:ops@example.org"}
    raw = webpush.b64u_dec(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    v.key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # wirft bei Fehler
    assert h.endswith("k=" + v.public_key)


def test_push_endpoint_ssrf_is_rejected(env):
    push = env.app.state.push
    with pytest.raises(UrlRejected):
        webpush.Push.check_endpoint(push, "https://127.0.0.1/push")
    with pytest.raises(UrlRejected):
        webpush.Push.check_endpoint(push, "http://push.example.org/x")


def test_waitlist_offer_push_and_next_in_line(env):
    owner, (a, b) = school_with(env)
    site = owner.post("/api/v1/sites", {"name": "Schulhof", "station_ids": [a.sid, b.sid]}).json()
    assert owner.patch(f"/api/v1/sites/{site['id']}", {"waitlist_enabled": True, "hold_minutes": 10}).status_code == 200
    ids = {u: card(owner, u, u[-1]) for u in (UID_A, UID_B, UID_C)}
    extra = "04DD000004"
    card(owner, extra, "D")
    a.send(occupied=True)
    b.send(occupied=True)
    assert tap(a, extra)["result"] == "checked_in"
    cb, hb, _ = holder(env, owner, ids[UID_B])
    cc, hc, _ = holder(env, owner, ids[UID_C])
    ua_key, p256dh, auth_b64, auth = browser_keys()
    sub = {"endpoint": "https://push.example.net/send/abc", "keys": {"p256dh": p256dh, "auth": auth_b64}}
    assert cb.c.post("/api/v1/public/card/push", json=sub, headers=hb).status_code == 201
    assert cb.c.post("/api/v1/public/card/waitlist", json={"site_id": site["id"]}, headers=hb).json()["position"] == 1
    assert cc.c.post("/api/v1/public/card/waitlist", json={"site_id": site["id"]}, headers=hc).json()["position"] == 2
    assert cb.c.post("/api/v1/public/card/waitlist", json={"site_id": site["id"]}, headers=hb).status_code == 409
    # Platz 2 wird frei -> B bekommt 10 min Reservierung + Push
    b.send(occupied=False)
    home = cb.c.get("/api/v1/public/card", headers=hb).json()
    assert home["waitlist"]["status"] == "offered" and home["waitlist"]["station_name"] == "Platz 2"
    assert home["reservation"]["remaining_s"] == 600
    url, headers, body = env.pushes[-1]
    assert url == sub["endpoint"] and headers["Content-Encoding"] == "aes128gcm" and headers["TTL"] == "600"
    assert json.loads(decrypt(body, ua_key, auth))["station_name"] == "Platz 2"
    assert cc.c.get("/api/v1/public/card", headers=hc).json()["waitlist"]["position"] == 1
    assert tap(b, UID_C)["result"] == "reserved"  # C darf den für B gehaltenen Platz nicht nehmen
    # B lässt die Zeit ablaufen -> C ist dran
    env.clock.advance(601)
    owner.me()
    b.send(occupied=False)  # frische Messung: der Platz ist weiterhin sicher frei (veraltet wäre „unbekannt“)
    env.app.state.reservations.expire()
    assert cc.c.get("/api/v1/public/card", headers=hc).json()["waitlist"]["status"] == "offered"
    assert cb.c.get("/api/v1/public/card", headers=hb).json()["waitlist"] is None
    assert tap(b, UID_C)["result"] == "checked_in"
    assert env.core.db.scalar("SELECT status FROM waitlist WHERE card_id = ?", (ids[UID_C],)) == "done"
    # Push-Dienst meldet „abgelaufen“ -> Abo wird gelöscht
    env.app.state.push.transport = lambda u, h, b_: 410
    env.app.state.push.send_card(ids[UID_B], {"title": "x"})
    assert env.core.db.scalar("SELECT COUNT(*) FROM push_sub") == 0


def test_waitlist_only_when_full(env):
    owner, (a,) = school_with(env, 1)
    site = owner.post("/api/v1/sites", {"name": "S", "station_ids": [a.sid]}).json()
    owner.patch(f"/api/v1/sites/{site['id']}", {"waitlist_enabled": True})
    c, h, _ = holder(env, owner, card(owner, UID_A, "A"))
    a.send(occupied=False)
    r = c.c.post("/api/v1/public/card/waitlist", json={"site_id": site["id"]}, headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "stall_available"


# ---------------------------------------------------------------------- Status-Seite
def test_status_page_incidents_and_availability(env):
    owner, (a, b) = school_with(env)
    a.send(occupied=False)
    b.send(occupied=False)
    link = owner.post("/api/v1/org/status-page").json()
    tok = link["url"].split("#")[1]
    pub = lambda: env.client().c.get("/api/v1/public/status", headers={"X-Status-Token": tok}).json()  # noqa: E731
    s = pub()
    assert [x["state"] for x in s["stalls"]] == ["ok", "ok"] and s["open"] == []
    # Sensorfehler öffnet, gültige Messung beendet (Minutenschleife)
    a.send(occupied=None, state="error")
    assert pub()["stalls"][0]["state"] == "fault" and pub()["open"][0]["kind"] == "sensor_fault"
    env.clock.advance(20)
    a.send(occupied=False)
    env.app.state.incidents.tick()
    s = pub()
    assert s["open"] == [] and s["history"][0]["duration_s"] == 20 and s["stalls"][0]["state"] == "ok"
    # Gateway offline/online über die Ereignisse
    env.core.emit("gateway.offline", s and owner.me().json()["tenant"]["id"], {"station_id": b.sid, "device_id": "dev_x"})
    assert pub()["stalls"][1]["state"] == "fault"
    env.core.emit("gateway.online", owner.me().json()["tenant"]["id"], {"station_id": b.sid, "device_id": "dev_x"})
    # Wartung (nach einer halben Stunde Betrieb, damit die Verfügbarkeit aussagekräftig ist)
    env.clock.advance(1800)
    owner.me()
    owner.patch(f"/api/v1/stations/{a.sid}", {"maintenance": True})
    s = pub()
    assert s["stalls"][0]["state"] == "maintenance" and s["open"][0]["kind"] == "maintenance"
    assert s["stalls"][0]["availability"] == round(1 - 20 / (1800 + 20), 4)  # 20 s Sensorfehler, Wartung zählt nicht
    text = json.dumps(s)
    assert "station_id" not in text and "card" not in text and "session" not in text
    # Notiz und Abschalten
    inc = owner.get("/api/v1/incidents").json()["open"][0]
    assert owner.patch(f"/api/v1/incidents/{inc['id']}", {"note": "Hausmeister informiert"}).status_code == 200
    assert pub()["open"][0]["note"] == "Hausmeister informiert"
    owner.delete("/api/v1/org/status-page")
    assert env.client().c.get("/api/v1/public/status", headers={"X-Status-Token": tok}).status_code == 404
