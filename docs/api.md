# API und Datenformat

Alle Pfade unter `/api/v1`. Browser nutzen das Session-Cookie + `X-CSRF-Token` (aus `/auth/me`
bzw. der Login-Antwort). Gateways nutzen `Authorization: Bearer <Geräte-Token>`.
Fehler: `{"detail": "<code>"}` oder `{"detail": {"code": …}}`; Codes siehe `web/static/js/i18n.js` (`err.*`).

## Gateway → Plattform

`POST /measurements` bzw. `POST /measurements/batch` (`{"measurements": […]}`, max. 200)

```json
{"station_id": "st_…", "slot_id": "A", "sequence": 1727517600123, "occupied": true,
 "vibration_score": 12, "sensor_state": "ok", "source": "live", "age_ms": 0}
```

- `station_id` muss zur Station des Geräte-Tokens passen (sonst `403 station_mismatch`).
- `slot_id` ist die Platzkennung (`key`) wie im Arduino. Doppelte `sequence` → `duplicate`.
- `age_ms`: Pufferalter; nachgesendete Daten lösen keinen Live-Alarm aus.

Arduino → Pi (JSON-Zeilen): `{"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}`.

## Agent (Raspberry Pi) – siehe auch [agent.md](agent.md)

| Methode/Pfad | Zweck | Auth |
|---|---|---|
| `GET /install/agent.sh` · `/install/agent.tar.gz` · `/install/agent.sha256` | Installationsskript, Paket, Prüfsummen | öffentlich |
| `POST /agent/enroll` `{code, hostname, agent_version, os_info, source}` | Kopplung → `{device_id, token, station_id, slot_map, config_version}` | Einmal-Code |
| `POST /agent/heartbeat` `{agent_version, serial_connected, buffer_len, cpu_temp_c, …}` | Zustand melden → `{slot_map, config_version, commands, update}` | Geräte-Token |
| `POST /agent/rotate-token` | neues Token (altes 15 min gültig) | Geräte-Token |
| `POST /stations/{id}/enrollments` · `GET` · `DELETE …/{eid}` | Kopplungscodes (Portal) | Admin |
| `POST /stations/{id}/devices/{dev}/command` `{command: restart\|rotate_token\|update}` | Fernbefehl | Admin |
| `GET /devices` | Flottenübersicht | Lesend |

## Authentifizierung (`/auth`)

| Methode/Pfad | Zweck |
|---|---|
| `POST /auth/register` | Organisation + Inhaber anlegen (immer `202 check_email`) |
| `POST /auth/verify-email` `{token}` | E-Mail bestätigen |
| `POST /auth/resend-verification` `{email}` | erneut senden |
| `POST /auth/login` `{email,password}` | Session oder `{mfa_required, mfa_token}` |
| `POST /auth/login/mfa` `{mfa_token, code}` | TOTP- oder Wiederherstellungscode |
| `POST /auth/logout` | Session beenden |
| `GET/PATCH /auth/me` | Profil, Mandant, Tarif, Nutzung, CSRF-Token |
| `POST /auth/me/delete` `{password}` | eigenes Konto löschen |
| `POST /auth/password/forgot` · `/reset` · `/change` | Passwort |
| `GET /auth/sessions` · `DELETE /auth/sessions/{id}` · `POST /auth/sessions/revoke-others` | Sitzungen |
| `POST /auth/mfa/setup` · `/enable` · `/disable` · `/recovery-codes` | 2FA |
| `POST /auth/invite/info` · `/invite/accept` | Einladung annehmen |

## Organisation (`/org`)

| Methode/Pfad | Rolle |
|---|---|
| `GET /org` | Lesend |
| `PATCH /org` `{name, mfa_required}` | Admin (`mfa_required`: Inhaber) |
| `GET /org/plans` · `POST /org/plan` | Lesend · Inhaber |
| `GET /org/users` · `PATCH/DELETE /org/users/{id}` | Lesend · Admin |
| `GET/POST /org/invitations` · `DELETE /org/invitations/{id}` | Admin |
| `GET /org/audit` | Admin (Tarif mit Audit-Log) |
| `GET /org/export` · `POST /org/delete` | Inhaber |

## Stationen und Betrieb

| Methode/Pfad | Rolle |
|---|---|
| `GET /stations` (mit Live-Kurzstatus) · `POST /stations` | Lesend · Admin |
| `GET/PATCH/DELETE /stations/{id}` | Lesend · Admin |
| `POST /stations/{id}/slots` · `PATCH/DELETE /stations/{id}/slots/{slot}` | Admin |
| `GET /stations/{id}/status` · `GET /stations/{id}/occupancy?hours=24` | Lesend |
| `GET/POST /stations/{id}/devices` · `DELETE /stations/{id}/devices/{dev}` | Admin |
| `POST /stations/{id}/display-link` | Admin |
| `GET /events?station_id&open_only&include_shadow` · `POST /events/{id}/ack` | Lesend · Betreuer |
| `GET /public/display/status` (Header `X-Display-Token`) | öffentlich, nur lesend |

## Plattform-Betreiber (`/platform`, Plattform-Admin mit 2FA)

`GET /platform/tenants`, `PATCH /platform/tenants/{id}` `{status, plan}`, `GET /platform/stats`, `GET /platform/audit`.

## Sonstiges

`GET /meta` (Produktname, Tarife, Passwortlänge), `GET /health`, `/.well-known/security.txt`, `/robots.txt`.

## Statusantwort (gekürzt)

```json
{"free_count": 1, "known_count": 3, "total": 3, "recommendation": "B",
 "slots": [{"slot_id": "A", "label": "Platz A", "state": "occupied", "unknown_reason": null,
            "alert": {"kind": "unusual_movement", "occurred_at": "…", "id": "evt_…", "detector": "rule"}}],
 "simulated_data": false,
 "ai": {"visible_detector": "rule", "plan_allows_ml": true, "model_available": true}}
```

`state`: `free` / `occupied` / `unknown` (`unknown_reason`: `no_data`, `stale`, `sensor_error`).

## Datenmodell (SQLite, Schema-Version 3, Migration von 2 automatisch)

`tenant` → `user`, `station` → `slot`, `device`, `measurement`, `event`; dazu `session`, `auth_token`
(Einmal-Tokens), `recovery_code`, `enrollment` (Kopplungscodes), `audit_log`. Löschen eines Mandanten entfernt alles per Kaskade.
