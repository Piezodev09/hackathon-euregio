"""Demo mode: a complete demo organisation with two stations and 7 days of SIMULATED history.

    python -m app.cli demo --reset

Everything created here is labelled ``source = simulated`` – the portal, the kiosk and the landing page
show that label. The first station gets a public display link that the landing page shows live.
"""

from __future__ import annotations

import json
import math
import random
import secrets
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import Core
from .schemas import MeasurementIn
from .security import hash_password, hash_token, new_id, new_token

DEMO_TENANT_SETTING = "demo_tenant_id"
LANDING_SETTING = "landing_demo_display_token"
LIVE_SETTING = "demo_live"              # "1": the platform itself keeps the demo stations alive
PROFILE_SETTING = "demo_profiles"       # JSON {station_id: "school" | "town_hall"}
STEP_S = 25          # one reading every 25 s (< stale_after_s, so the history has no gaps)
SEQ_START = 1_000_000


@dataclass
class DemoResult:
    tenant_id: str
    station_id: str
    second_station_id: str
    email: str
    password: str | None
    display_url: str
    measurements: int
    events: int


# Occupancy profiles: probability that a space is occupied at a local hour (weekday, weekend).
def _school(hour: float, weekday: int) -> float:
    if weekday >= 5:
        return 0.08 + 0.10 * math.exp(-((hour - 14) ** 2) / 8)
    if hour < 7:
        return 0.03
    if hour < 8:
        return 0.2 + 0.7 * (hour - 7)
    if hour < 13:
        return 0.93
    if hour < 16:
        return 0.93 - 0.25 * (hour - 13)
    if hour < 18:
        return 0.18
    return 0.05


def _town_hall(hour: float, weekday: int) -> float:
    if weekday == 6:
        return 0.05
    if weekday == 5:  # Saturday market in the morning
        return 0.75 * math.exp(-((hour - 10.5) ** 2) / 4) + 0.05
    base = 0.55 * math.exp(-((hour - 10) ** 2) / 10) + 0.35 * math.exp(-((hour - 15) ** 2) / 6)
    return min(0.95, base + (0.1 if weekday == 3 and 16 <= hour < 19 else 0.0) + 0.04)


PROFILES = {"school": _school, "town_hall": _town_hall}


def _slot_timeline(rng: random.Random, start: float, end: float, tz: ZoneInfo, profile, bias: float):
    """Occupancy intervals of one space as a list of (from, to, occupied).

    5-minute Markov chain whose equilibrium follows the profile p: arrivals with 0.5·p,
    departures with 0.35·(1-p) per step. Busy hours therefore stay full for hours, nights stay empty.
    """
    step = 300.0
    t = start
    occupied = False
    seg_start = start
    out = []
    while t < end:
        local = datetime.fromtimestamp(t, tz)
        p = min(0.97, max(0.02, profile(local.hour + local.minute / 60, local.weekday()) * bias))
        change = rng.random() < (0.35 * (1 - p) if occupied else 0.5 * p)
        nxt = min(end, t + step)
        if change:
            # the change happens somewhere inside this step
            at = t + rng.uniform(0, nxt - t)
            out.append((seg_start, at, occupied))
            occupied = not occupied
            seg_start = at
        t = nxt
    out.append((seg_start, end, occupied))
    return [seg for seg in out if seg[1] > seg[0]]


def _readings(rng: random.Random, timeline, sensor_fault: tuple[float, float] | None):
    """Readings every STEP_S seconds; small vibration when parking, a sensor fault window if given."""
    rows = []
    for (a, b, occ) in timeline:
        t = a
        first = True
        while t < b:
            fault = sensor_fault is not None and sensor_fault[0] <= t < sensor_fault[1]
            if fault:
                rows.append((t, None, 0, "error"))
            else:
                vib = rng.randint(120, 320) if first and occ else (rng.randint(5, 60) if rng.random() < 0.01 else 0)
                rows.append((t, 1 if occ else 0, vib, "ok"))
            first = False
            t += STEP_S
    return rows


def remove_demo(core: Core) -> bool:
    tid = core.get_setting(DEMO_TENANT_SETTING)
    removed = False
    if tid and core.db.one("SELECT 1 FROM tenant WHERE id = ?", (tid,)):
        core.db.execute("DELETE FROM tenant WHERE id = ?", (tid,))  # cascade: users, stations, data
        removed = True
    for key in (DEMO_TENANT_SETTING, LANDING_SETTING, LIVE_SETTING, PROFILE_SETTING):
        core.set_setting(key, None)
    return removed


def create_demo(core: Core, email: str = "demo@example.org", password: str | None = None, days: int = 7,
                seed: int = 2026, reset: bool = False, live: bool = True) -> DemoResult:
    email = email.strip().lower()
    if reset:
        remove_demo(core)
    elif core.get_setting(DEMO_TENANT_SETTING):
        raise ValueError("demo already exists - use --reset")
    if core.db.one("SELECT 1 FROM user WHERE email = ?", (email,)):
        raise ValueError(f"e-mail address {email} is already used by another account")
    generated = password is None
    password = password or ("Demo-" + secrets.token_urlsafe(12))
    rng = random.Random(seed)
    tz = ZoneInfo(core.s.timezone)
    now = core.clock()
    end = now - 5  # live data (the agent) continues from here
    start = end - days * 86400
    tid, uid = new_id("org"), new_id("usr")
    stations = [
        (new_id("st"), "Schoolyard – main entrance", "Euregio School, bike shed A", "ABCDEF", "school"),
        (new_id("st"), "Town hall", "Market square", "ABCD", "town_hall"),
    ]
    display = new_token("bsp_")
    n_meas = n_events = 0
    with core.db.tx() as c:
        c.execute("INSERT INTO tenant (id, name, plan, status, created_at) VALUES (?,?,?,?,?)",
                  (tid, "Demo – Euregio School", "pro", "active", start))
        c.execute(
            "INSERT INTO user (id, tenant_id, email, name, role, password_hash, email_verified_at, locale, created_at, "
            "password_changed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uid, tid, email, "Demo Owner", "owner", hash_password(password, core.s.scrypt_n), now, "en", start, now))
        for idx, (sid, name, location, keys, profile_name) in enumerate(stations):
            profile = PROFILES[profile_name]
            c.execute("INSERT INTO station (id, tenant_id, name, location, display_token_hash, display_enabled, created_at) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (sid, tid, name, location, hash_token(display) if idx == 0 else None, 1 if idx == 0 else 0, start))
            for pos, key in enumerate(keys, start=1):
                slot_id = new_id("sl")
                c.execute("INSERT INTO slot (id, station_id, key, label, position) VALUES (?,?,?,?,?)",
                          (slot_id, sid, key, f"Space {key}", pos))
                bias = 0.8 + 0.4 * rng.random()
                fault = None
                if idx == 0 and key == "E":  # one sensor fault on day 3 for ~40 minutes
                    fault_start = start + 2 * 86400 + 11 * 3600
                    fault = (fault_start, fault_start + 2400)
                    c.execute("INSERT INTO event (id, tenant_id, station_id, slot_id, kind, severity, occurred_at, source) "
                              "VALUES (?,?,?,?,?,?,?,?)",
                              (new_id("evt"), tid, sid, slot_id, "sensor_fault", "info", fault_start, "simulated"))
                    n_events += 1
                timeline = _slot_timeline(rng, start, end, tz, profile, bias)
                rows = _readings(rng, timeline, fault)
                c.executemany(
                    "INSERT INTO measurement (station_id, slot_id, sequence, server_time, occupied, vibration_score, "
                    "sensor_state, source) VALUES (?,?,?,?,?,?,?, 'simulated')",
                    [(sid, slot_id, SEQ_START + i, t, occ, vib, state) for i, (t, occ, vib, state) in enumerate(rows)])
                n_meas += len(rows)
                # A few unusual-movement warnings during the day at occupied spaces (clearly simulated;
                # every other one acknowledged): two at the school, one at the town hall.
                occupied_times = [a + 600 for (a, b, occ) in timeline
                                  if occ and b - a > 1800 and 8 <= datetime.fromtimestamp(a + 600, tz).hour < 18]
                wanted = (1 if pos in (2, 5) else 0) if idx == 0 else (1 if pos == 1 else 0)
                for k, t in enumerate(rng.sample(occupied_times, k=min(len(occupied_times), wanted))):
                    detail = json.dumps({"rule": True, "ml": None, "features": {"n_peaks": 5.0, "n_active": 6.0,
                                         "max_score": 870.0, "mean_active_score": 610.0, "since_change_s": 600.0}})
                    ack = t + 420 if (k + pos) % 2 == 0 else None
                    c.execute("INSERT INTO event (id, tenant_id, station_id, slot_id, kind, severity, detector, detail, "
                              "occurred_at, acknowledged_at, acknowledged_by, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                              (new_id("evt"), tid, sid, slot_id, "unusual_movement", "warning", "rule", detail, t, ack,
                               email if ack else None, "simulated"))
                    n_events += 1
    core.set_setting(DEMO_TENANT_SETTING, tid)
    core.set_setting(LANDING_SETTING, display)
    core.set_setting(PROFILE_SETTING, json.dumps({sid: prof for sid, _, _, _, prof in stations}))
    core.set_setting(LIVE_SETTING, "1" if live else None)
    core.audit("demo_created", tenant_id=tid, actor="cli", target=email, detail={"days": days, "measurements": n_meas})
    return DemoResult(tid, stations[0][0], stations[1][0], email, password if generated else None,
                      f"{core.s.base_url}/display#{display}", n_meas, n_events)



class DemoFeeder:
    """Keeps the demo stations alive without hardware: a simulated reading per space every ``interval_s``.

    Runs inside the platform (see ``main.py``) while the setting ``demo_live`` is "1". All readings are
    labelled ``simulated``. A station whose paired agent sent data within the last 2 minutes is left
    alone, so a real (or simulated) agent always takes precedence. Now and then a space is "shaken" so
    the movement warning can be seen - as a clearly simulated event.
    """

    SHAKE_EVERY_S = 20 * 60

    def __init__(self, core: Core, monitoring, interval_s: float = 10.0, seed: int | None = None):
        self.core = core
        self.monitoring = monitoring
        self.interval_s = interval_s
        self.rng = random.Random(seed)
        self.state: dict[str, bool] = {}

    def active(self) -> bool:
        return self.core.get_setting(LIVE_SETTING) == "1" and bool(self.core.get_setting(DEMO_TENANT_SETTING))

    def step(self) -> int:
        """One round. Returns the number of readings sent."""
        if not self.active():
            return 0
        core = self.core
        now = core.clock()
        tz = ZoneInfo(core.s.timezone)
        profiles = json.loads(core.get_setting(PROFILE_SETTING) or "{}")
        tid = core.get_setting(DEMO_TENANT_SETTING)
        sent = 0
        for st in core.db.all("SELECT * FROM station WHERE tenant_id = ? ORDER BY created_at, name", (tid,)):
            if core.db.scalar("SELECT 1 FROM device WHERE station_id = ? AND revoked_at IS NULL AND last_seen_at > ?",
                              (st["id"], now - 120)):
                continue  # a paired agent is feeding this station
            profile = PROFILES.get(profiles.get(st["id"], "school"), _school)
            local = datetime.fromtimestamp(now, tz)
            p = min(0.97, max(0.02, profile(local.hour + local.minute / 60, local.weekday())))
            slots = self.monitoring.slots(st["id"])
            shake_at = self.rng.randrange(len(slots)) if slots and self.rng.random() < self.interval_s / self.SHAKE_EVERY_S else -1
            for i, sl in enumerate(slots):
                occ = self.state.get(sl["id"])
                if occ is None:
                    last = core.db.one("SELECT occupied FROM measurement WHERE slot_id = ? AND occupied IS NOT NULL "
                                       "ORDER BY server_time DESC LIMIT 1", (sl["id"],))
                    occ = bool(last["occupied"]) if last else self.rng.random() < p
                rate = (0.35 * (1 - p) if occ else 0.5 * p) * self.interval_s / 300
                changed = self.rng.random() < rate
                if changed:
                    occ = not occ
                self.state[sl["id"]] = occ
                seq = int(now * 1000) * 10
                vib = self.rng.randint(150, 320) if changed and occ else 0
                self._send(st, sl["key"], seq, occ, vib, 0)
                sent += 1
                if i == shake_at and occ and not changed:
                    # Controlled simulated shaking: four strong peaks within three seconds.
                    for k in range(4):
                        self._send(st, sl["key"], seq + 1 + k, True, self.rng.randint(600, 900), (3 - k) * 900)
                    sent += 4
        return sent

    def _send(self, st, key: str, seq: int, occ: bool, vib: int, age_ms: int) -> None:
        self.monitoring.ingest(st, MeasurementIn(station_id=st["id"], slot_id=key, sequence=seq, occupied=occ,
                                                 vibration_score=vib, sensor_state="ok", source="simulated",
                                                 age_ms=age_ms))
