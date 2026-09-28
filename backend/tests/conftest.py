from __future__ import annotations

import base64
import re

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app

PASSWORD = "Sicheres-Passwort-2026!"


class Clock:
    def __init__(self, t: float = 1_700_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class Api:
    """Browser-ähnlicher Client: eigene Cookies, sendet CSRF-Token automatisch mit."""

    def __init__(self, app):
        self.c = TestClient(app, base_url="http://testserver")
        self.csrf: str | None = None

    def _h(self, headers):
        h = dict(headers or {})
        if self.csrf:
            h.setdefault("X-CSRF-Token", self.csrf)
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, headers=None, **kw):
        r = self.c.post(url, json=json, headers=self._h(headers), **kw)
        if r.status_code == 200 and isinstance(r.json(), dict) and "csrf_token" in r.json():
            self.csrf = r.json()["csrf_token"]
        return r

    def patch(self, url, json=None, headers=None):
        return self.c.patch(url, json=json, headers=self._h(headers))

    def delete(self, url, headers=None):
        return self.c.delete(url, headers=self._h(headers))

    def me(self):
        r = self.get("/api/v1/auth/me")
        if r.status_code == 200:
            self.csrf = r.json()["csrf_token"]
        return r


class Env:
    def __init__(self, app, clock):
        self.app = app
        self.clock = clock
        self.core = app.state.core
        self._n = 0

    @property
    def outbox(self):
        return self.core.mailer.outbox

    def last_token(self, to: str) -> str:
        for m in reversed(self.outbox):
            if m.to == to:
                found = re.search(r"token=([A-Za-z0-9_\-]+)", m.body)
                if found:
                    return found.group(1)
        raise AssertionError(f"keine Mail mit Token an {to}")

    def client(self) -> Api:
        return Api(self.app)

    def register(self, org="Schule A", email=None, password=PASSWORD, verify=True, login=True):
        self._n += 1
        email = email or f"owner{self._n}@example.org"
        api = self.client()
        r = api.post("/api/v1/auth/register", {"org_name": org, "name": "Olga Owner", "email": email,
                                                "password": password, "accept_terms": True})
        assert r.status_code == 202, r.text
        if verify:
            assert api.post("/api/v1/auth/verify-email", {"token": self.last_token(email)}).status_code == 200
        if login:
            r = api.post("/api/v1/auth/login", {"email": email, "password": password})
            assert r.status_code == 200, r.text
        api.email = email
        return api

    def set_plan(self, api: Api, plan: str):
        tid = api.me().json()["tenant"]["id"]
        self.core.db.execute("UPDATE tenant SET plan = ? WHERE id = ?", (plan, tid))

    def station(self, api: Api, name="Stellplatz 1"):
        r = api.post("/api/v1/stations", {"name": name})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        r = api.post(f"/api/v1/stations/{sid}/devices", {"name": "Pi"})
        assert r.status_code == 201, r.text
        return sid, r.json()["token"]

    def invite(self, admin: Api, email: str, role: str) -> Api:
        assert admin.post("/api/v1/org/invitations", {"email": email, "role": role}).status_code == 201
        api = self.client()
        r = api.post("/api/v1/auth/invite/accept", {"token": self.last_token(email), "name": "Tim Team", "password": PASSWORD})
        assert r.status_code == 200, r.text
        api.email = email
        return api


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BIKE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("BIKE_SCRYPT_N", "1024")
    monkeypatch.setenv("BIKE_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("BIKE_BASE_URL", "http://testserver")
    monkeypatch.setenv("BIKE_DATA_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    settings = load_settings()
    object.__setattr__(settings, "model_path", tmp_path / "missing.joblib")
    clock = Clock()
    app = create_app(settings, clock=clock)
    with TestClient(app):
        yield Env(app, clock)


class Device:
    def __init__(self, api: Api, station_id: str, token: str):
        self.api = api
        self.sid = station_id
        self.token = token
        self.seq = 0

    def send(self, occupied=True, vib=0, state="ok", token=..., station=None, **extra):
        self.seq += 1
        body = {"station_id": station or self.sid, "sequence": self.seq, "occupied": occupied,
                "vibration_score": vib, "sensor_state": state, **extra}
        tok = self.token if token is ... else token
        headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        return self.api.c.post("/api/v1/measurements", json=body, headers=headers)
