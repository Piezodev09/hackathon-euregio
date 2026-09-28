"""Statistiken: Werte gegen Hand-Rechnung, Tarif-Vorschau, Mandantentrennung, Tageskennzahlen."""

from __future__ import annotations

from conftest import Device
from test_features import UID_A, UID_B, tap


def setup(env, plan="school"):
    owner = env.register(plan=plan)
    sid, token = env.station(owner, name="Schulhof")
    return owner, sid, Device(owner, sid, token)


def test_stats_values_match_hand_calculation(env):
    owner, sid, dev = setup(env)
    owner.post("/api/v1/cards", {"uid": UID_A, "label": "A"})
    owner.put("/api/v1/billing/tariff", {"mode": "flat", "price_cents": 80, "free_minutes": 0})
    # Di 14.11.2023 23:13 Berlin: 60 s frei, dann 60 s belegt (Messung alle 20 s)
    for occ in (False, False, False, True, True, True):
        dev.send(occupied=occ)
        env.clock.advance(20)
    assert tap(dev, UID_A)["result"] == "checked_in"
    assert tap(dev, "04CC000003")["result"] == "unknown_card"
    env.clock.advance(20)
    dev.send(occupied=True)
    assert tap(dev, UID_A)["result"] == "checked_out"
    s = owner.get("/api/v1/stats?days=7").json()
    assert s["days"] == 7 and s["to"] == "2023-11-14" and s["from"] == "2023-11-08" and not s["preview"] and not s["simulated"]
    tot = s["totals"]
    assert tot["checkins"] == 1 and tot["fees_cents"] == 80 and tot["taps"] == 3 and tot["unknown_cards"] == 1
    assert tot["tap_success"] == round(2 / 3, 3)
    # 140 s mit Daten, davon 80 s belegt
    # Messungen 0–100 s alle 20 s, dann 40 s Pause (Messung gilt nur 30 s): 130 s mit Daten, davon 70 s belegt
    assert tot["occupancy"] == round(70 / 130, 3)
    assert tot["median_minutes"] == 0 and s["durations"][0]["count"] == 1
    today = [x for x in s["series"] if x["day"] == "2023-11-14"][0]
    assert today["checkins"] == 1 and today["fees_cents"] == 80 and today["taps"] == 3
    assert all(x["occupancy"] is None for x in s["series"] if x["day"] != "2023-11-14")  # keine Daten != frei
    cell = s["heatmap"][1][23]  # Dienstag, 23 Uhr
    assert cell["occupancy"] == round(70 / 130, 3) and cell["checkins"] == 1
    assert s["totals"]["peak"] == {"weekday": 1, "hour": 23, "occupancy": cell["occupancy"]}
    assert s["stations"][0]["name"] == "Schulhof" and s["stations"][0]["checkins"] == 1
    assert s["nfc"]["by_result"] == {"checked_in": 1, "checked_out": 1, "unknown_card": 1}
    assert s["nfc"]["readers"][0]["taps"] == 3
    one = owner.get(f"/api/v1/stats?days=30&station_id={sid}").json()
    assert one["station_id"] == sid and len(one["series"]) == 30
    assert owner.get("/api/v1/stats?days=12").status_code == 422
    t = owner.get("/api/v1/stats/today").json()
    assert t == {**t, "day": "2023-11-14", "checkins": 1, "checkouts": 1, "fees_cents": 80, "parked_now": 0, "taps": 3,
                 "unknown_cards": 1}


def test_today_uses_berlin_midnight(env):
    owner, sid, dev = setup(env)
    owner.post("/api/v1/cards", {"uid": UID_B, "label": "B"})
    dev.send(occupied=True)
    assert tap(dev, UID_B)["result"] == "checked_in"
    env.clock.advance(50 * 60)  # 00:03 Uhr Berlin am 15.11. (in UTC noch der 14.11.)
    owner.me()
    t = owner.get("/api/v1/stats/today").json()
    assert t["day"] == "2023-11-15" and t["checkins"] == 0 and t["parked_now"] == 1


def test_stats_free_preview_and_tenant_isolation(env):
    free, fsid, _ = setup(env, plan="free")
    s = free.get("/api/v1/stats?days=90").json()
    assert s["preview"] is True and s["days"] == 7
    other, osid, _ = setup(env)
    assert free.get(f"/api/v1/stats?station_id={osid}").status_code == 404
    assert other.get("/api/v1/stats?days=90").json()["days"] == 90
