# API and data format

All paths are below `/api/v1`. Browsers use the session cookie + `X-CSRF-Token` (from `/auth/me`
or the sign-in response). Gateways use `Authorization: Bearer <device token>`.
Errors: `{"detail": "<code>"}` or `{"detail": {"code": …}}`; the codes are listed in
`web/static/js/i18n.js` (`err.*`).

## Gateway → platform

`POST /measurements` or `POST /measurements/batch` (`{"measurements": […]}`, max. 200)

```json
{"station_id": "st_…", "slot_id": "A", "sequence": 1727517600123, "occupied": true,
 "vibration_score": 12, "sensor_state": "ok", "source": "live", "age_ms": 0}
```

- `station_id` must match the station of the device token (otherwise `403 station_mismatch`).
- `slot_id` is the slot key as used by the Arduino. A repeated `sequence` → `duplicate`.
- `age_ms`: time spent in the buffer; data sent late never triggers a live warning.

Arduino → Pi (JSON lines): `{"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}`.

## Agent (Raspberry Pi) – see also [agent.md](agent.md)

| Method/path | Purpose | Auth |
|---|---|---|
| `GET /install/agent.sh` · `/install/agent.tar.gz` · `/install/agent.sha256` | install script, package, checksums | public |
| `POST /agent/enroll` `{code, hostname, agent_version, os_info, source}` | pairing → `{device_id, token, station_id, slot_map, config_version}` | one-time code |
| `POST /agent/heartbeat` `{agent_version, serial_connected, buffer_len, cpu_temp_c, …}` | report health → `{slot_map, config_version, commands, update}` | device token |
| `POST /agent/rotate-token` | new token (old one valid for 15 min) | device token |
| `POST /stations/{id}/enrollments` · `GET` · `DELETE …/{eid}` | pairing codes (portal) | admin |
| `POST /stations/{id}/devices/{dev}/command` `{command: restart\|rotate_token\|update}` | remote command | admin |
| `GET /devices` | fleet overview | viewer |

## Authentication (`/auth`)

| Method/path | Purpose |
|---|---|
| `POST /auth/register` | create organisation + owner (always `202 check_email`) |
| `POST /auth/verify-email` `{token}` | confirm e-mail |
| `POST /auth/resend-verification` `{email}` | send again |
| `POST /auth/login` `{email,password}` | session or `{mfa_required, mfa_token}` |
| `POST /auth/login/mfa` `{mfa_token, code}` | TOTP or recovery code |
| `POST /auth/logout` | end session |
| `GET/PATCH /auth/me` | profile, tenant, plan, usage, CSRF token |
| `POST /auth/me/delete` `{password}` | delete own account |
| `POST /auth/password/forgot` · `/reset` · `/change` | password |
| `GET /auth/sessions` · `DELETE /auth/sessions/{id}` · `POST /auth/sessions/revoke-others` | sessions |
| `POST /auth/mfa/setup` · `/enable` · `/disable` · `/recovery-codes` | 2FA |
| `POST /auth/invite/info` · `/invite/accept` | accept an invitation |

## Organisation (`/org`)

| Method/path | Role |
|---|---|
| `GET /org` | viewer |
| `PATCH /org` `{name, mfa_required}` | admin (`mfa_required`: owner) |
| `GET /org/plans` · `POST /org/plan` | viewer · owner |
| `GET /org/users` · `PATCH/DELETE /org/users/{id}` | viewer · admin |
| `GET/POST /org/invitations` · `DELETE /org/invitations/{id}` | admin |
| `GET /org/audit` | admin (plan with audit log) |
| `GET /org/export` · `POST /org/delete` | owner |

## Stations and operation

| Method/path | Role |
|---|---|
| `GET /stations` (with short live status) · `POST /stations` | viewer · admin |
| `GET/PATCH/DELETE /stations/{id}` | viewer · admin |
| `POST /stations/{id}/slots` · `PATCH/DELETE /stations/{id}/slots/{slot}` | admin |
| `GET /stations/{id}/status` · `GET /stations/{id}/occupancy?hours=24` | viewer |
| `GET/POST /stations/{id}/devices` · `DELETE /stations/{id}/devices/{dev}` | admin |
| `POST /stations/{id}/display-link` | admin |
| `GET /events?station_id&open_only&include_shadow` · `POST /events/{id}/ack` | viewer · operator |
| `GET /public/display/status` (header `X-Display-Token`) | public, read only |

## Platform operator (`/platform`, platform admin with 2FA)

`GET /platform/tenants`, `PATCH /platform/tenants/{id}` `{status, plan}`, `GET /platform/stats`, `GET /platform/audit`.

## Other

`GET /meta` (product name, plans, password length), `GET /health`, `/.well-known/security.txt`, `/robots.txt`.

## Status response (shortened)

```json
{"free_count": 1, "known_count": 3, "total": 3, "recommendation": "B",
 "slots": [{"slot_id": "A", "label": "Space A", "state": "occupied", "unknown_reason": null,
            "alert": {"kind": "unusual_movement", "occurred_at": "…", "id": "evt_…", "detector": "rule"}}],
 "simulated_data": false,
 "ai": {"visible_detector": "rule", "plan_allows_ml": true, "model_available": true}}
```

`state`: `free` / `occupied` / `unknown` (`unknown_reason`: `no_data`, `stale`, `sensor_error`).

## Data model (SQLite, schema version 3, migrated automatically from 2)

`tenant` → `user`, `station` → `slot`, `device`, `measurement`, `event`; plus `session`, `auth_token`
(one-time tokens), `recovery_code`, `enrollment` (pairing codes), `audit_log`. Deleting a tenant
removes everything by cascade.
