# API und Datenformat

Alle Pfade unter `/api/v1`. Browser nutzen das Session-Cookie + `X-CSRF-Token` (aus `/auth/me`
bzw. der Login-Antwort). Gateways nutzen `Authorization: Bearer <Geräte-Token>`.
Fehler: `{"detail": "<code>"}` oder `{"detail": {"code": …}}`; Codes siehe `web/static/js/i18n.js` (`err.*`).

## Gateway → Plattform

`POST /measurements` bzw. `POST /measurements/batch` (`{"measurements": […]}`, max. 200)

```json
{"station_id": "st_…", "sequence": 1727517600123, "occupied": true,
 "vibration_score": 12, "sensor_state": "ok", "source": "live", "age_ms": 0}
```

- Eine Station ist genau ein Stellplatz. `station_id` muss zur Station des Geräte-Tokens passen (sonst `403 station_mismatch`).
- `slot_id` entfällt; ältere Gateways dürfen noch `"slot_id": "A"` senden, alles andere → `422 unknown_slot`.
- Doppelte `sequence` → `duplicate`. `age_ms`: Pufferalter; nachgesendete Daten lösen keinen Live-Alarm aus.

Die Antwort auf `/measurements/batch` enthält `"capture": true` und `"event_id"`, wenn eine Erschütterungswarnung
entstand **und** die Kamera für den Stellplatz freigegeben ist. Der Agent nimmt dann ein Einzelbild auf.

Arduino → Pi (JSON-Zeilen): `{"presence":1,"vibration":12,"seq":1042,"state":"ok"}`,
mit NFC-Leser zusätzlich `{"type":"nfc","uid":"04A1B2C3D4"}`. Pi → Arduino: `NFC checked_in` usw. (LED-Rückmeldung).

### NFC-Karte an den Leser gehalten

`POST /nfc/tap` (Geräte-Token)

```json
{"station_id": "st_…", "sequence": 1727517600456, "uid": "04A1B2C3D4", "age_ms": 0, "source": "live"}
```

Antwort `{"result": …, "session": {…}, "amount_cents": 50}` mit `result`:
`checked_in` · `checked_out` (mit Betrag) · `unknown_card` (Karte wird als „wartet auf Freigabe“ angelegt) ·
`blocked` · `occupied_by_other` (anderes Fahrrad ist eingecheckt) · `open_elsewhere` (Karte an anderem
Stellplatz eingecheckt) · `maintenance` · `expired` (Vorgang älter als 60 s, z. B. aus dem Puffer) ·
`duplicate` · `feature_disabled` (Tarif ohne NFC).
Die Roh-UID wird **nicht** gespeichert, nur `HMAC-SHA256(Datenschlüssel, "nfc:<Mandant>:<UID>")`.

## Agent (Raspberry Pi) – siehe auch [agent.md](agent.md)

| Methode/Pfad | Zweck | Auth |
|---|---|---|
| `GET /install/agent.sh` · `/install/agent.tar.gz` · `/install/agent.sha256` | Installationsskript, Paket, Prüfsummen | öffentlich |
| `POST /agent/enroll` `{code, hostname, agent_version, os_info, source}` | Kopplung → `{device_id, token, station_id, config_version}` | Einmal-Code |
| `POST /agent/heartbeat` `{agent_version, serial_connected, buffer_len, cpu_temp_c, …}` | Zustand melden → `{config_version, commands, update}` | Geräte-Token |
| `POST /agent/rotate-token` | neues Token (altes 15 min gültig) | Geräte-Token |
| `GET /agent/whoami` | Prüfung ohne Nebenwirkung (für `agent.py doctor`) | Geräte-Token |
| `POST /agent/snapshot?reason=manual\|alert&event_id=…` (Body: JPEG ≤ 2 MB) | Kamerabild hochladen; `403` wenn Kamera aus, `415`/`422` bei ungültigem Bild | Geräte-Token |
| `GET /install/server.crt` | Serverzertifikat zum Anheften (Pin im Portal) | öffentlich |
| `GET /agent/install-info` | Installationsbefehle inkl. Pin/Fingerabdruck | Admin |
| `POST /stations/{id}/enrollments` · `GET` · `DELETE …/{eid}` | Kopplungscodes (Portal) | Admin |
| `POST /stations/{id}/devices/{dev}/command` `{command: restart\|rotate_token\|update\|snapshot}` | Fernbefehl | Admin |
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

## Stellplätze (Stationen) und Betrieb

| Methode/Pfad | Rolle |
|---|---|
| `GET /stations` (mit Live-Kurzstatus) · `POST /stations` `{name, location}` | Lesend · Admin |
| `GET/PATCH/DELETE /stations/{id}` | Lesend · Admin |
| `GET /stations/{id}/status` · `GET /stations/{id}/occupancy?hours=24` | Lesend |
| `GET/POST /stations/{id}/devices` · `DELETE /stations/{id}/devices/{dev}` | Admin |
| `POST /stations/{id}/display-link` | Admin |
| `GET /events?station_id&open_only&include_shadow` · `POST /events/{id}/ack` | Lesend · Betreuer |
| `PATCH /stations/{id}` zusätzlich `{maintenance, stall_view_enabled}` | Admin |
| `POST /stations/{id}/stall-link` | Admin |
| `GET /public/display/status` (Header `X-Display-Token`) | öffentlich, nur lesend |
| `GET /public/stall/status` (Header `X-Stall-Token`) | öffentlich: Status, Tarif, laufender Vorgang nach Tap (< 2 min), Kamera-Hinweis |
| `POST /public/stall/report` `{category, text}` (Header `X-Stall-Token`) | öffentlich, max. 3 je 10 min je IP → Ereignis `user_report` |

## NFC-Karten, Parkvorgänge und Parkgebühren (Organisation → Radfahrende)

| Methode/Pfad | Rolle |
|---|---|
| `GET /cards` · `POST /cards` `{uid, label}` · `PATCH /cards/{id}` `{label, status}` · `DELETE /cards/{id}` | Lesend · Admin |
| `GET /parking/sessions?station_id&status&month&limit` | Lesend |
| `POST /parking/sessions/{id}/close` (Gebühr wird berechnet) · `POST …/cancel` (ohne Gebühr) | Betreuer · Admin |
| `GET /billing/tariff` · `PUT /billing/tariff` `{mode, price_cents, free_minutes, daily_cap_cents}` | Lesend · Admin |
| `PUT /stations/{id}/tariff` `{tariff: {…} \| null}` (null = Tarif der Organisation) | Admin |
| `GET /billing/statements?month=2026-09&format=json\|csv` | Lesend |
| `POST /billing/statements/{card}/{month}/paid` `{paid}` | Betreuer |
| `GET /org/license?month=` (Lizenz, Stellplatz-Tage, Hochrechnung, eigene Rechnungen) | Lesend |

Tarif-Modi: `free`, `flat` (je Vorgang), `per_hour` (je angefangene Stunde), `per_day` (je angefangenen
Kalendertag, Europe/Berlin). Freiminuten gelten für den ganzen Vorgang; `daily_cap_cents` begrenzt je Tag.
Beträge immer in Cent. Tarifmerkmale werden serverseitig geprüft (`402 plan_feature`).

## Kamera (opt-in)

| Methode/Pfad | Rolle |
|---|---|
| `PUT /stations/{id}/camera` `{enabled, retention_h, approved_by}` (`approved_by` Pflicht beim Einschalten) | Admin |
| `POST /stations/{id}/camera/snapshot` (Testbild anfordern) | Admin |
| `GET /stations/{id}/snapshots` · `GET /snapshots/{id}` (JPEG, jeder Abruf im Audit-Log) · `DELETE /snapshots/{id}` | Admin |

## Plattform-Betreiber (`/platform`, Plattform-Admin mit 2FA)

`GET /platform/tenants`, `PATCH /platform/tenants/{id}` `{status, plan}`, `GET /platform/stats`, `GET /platform/audit`.

Lizenzen und Rechnungen (Plattform → Organisation):
`PUT /platform/tenants/{id}/license` `{valid_until, price_per_stall_day_cents, base_month_cents, notes}` (Vertragspreis, beendet die Testphase),
`GET /platform/invoices?month=&format=json|csv` (Vorschau je Kunde + festgeschriebene Rechnungen),
`POST /platform/invoices` `{tenant_id, month}` (Rechnung `SBB-YYYYMM-NNNN` festschreiben),
`PATCH /platform/invoices/{id}` `{status: open|paid|void}`.

## Sonstiges

`GET /meta` (Produktname, Tarife, Passwortlänge), `GET /health`, `/.well-known/security.txt`, `/robots.txt`.

## Statusantwort (gekürzt)

```json
{"station_id": "st_…", "display_name": "Stellplatz Schulhof", "location": "Haupteingang",
 "state": "occupied", "unknown_reason": null, "last_update": "…", "age_s": 1.4,
 "stale_after_s": 30, "poll_interval_s": 2,
 "alert": {"kind": "unusual_movement", "occurred_at": "…", "id": "evt_…", "detector": "rule", "simulated": false},
 "simulated_data": false,
 "ai": {"visible_detector": "rule", "plan_allows_ml": true, "model_available": true}}
```

`state`: `free` / `occupied` / `unknown` (`unknown_reason`: `no_data`, `stale`, `sensor_error`).
Zusätzlich: `maintenance` (Anzeige „AUSSER BETRIEB“), `camera_active`, `session` (laufender Parkvorgang:
Kartenbezeichnung, seit, laufender Betrag) und `last_tap` (letzter NFC-Vorgang für die kurze Rückmeldung).
Die öffentliche Anzeige erhält dieselbe Antwort ohne `ai` und ohne Ereignis-ID.

`GET /stations/{id}/occupancy?hours=24` liefert je Stunde `occupancy` (Anteil belegt an der Zeit mit
gültiger Messung, `null` ohne Daten) und `known_s`.

## Datenmodell (SQLite, Schema-Version 5, Migration von 2, 3 und 4 automatisch)

`tenant` → `user`, `station` → `slot` (genau ein Eintrag = der Stellplatz; Migration 3→4 entfernt überzählige Plätze), `device`, `measurement`, `event`; dazu `session`, `auth_token`
(Einmal-Tokens), `recovery_code`, `enrollment` (Kopplungscodes), `audit_log`.
Neu in Version 5: `card` (nur UID-HMAC), `parking_session` (mit Tarif-Schnappschuss), `license`, `usage_day`
(Stellplätze je Tag), `invoice`, `snapshot` (Datei im Datenverzeichnis `snapshots/`, `0600`); an `station`:
`camera_enabled`, `camera_retention_h`, `camera_approved_by`, `stall_token_hash`, `stall_view_enabled`,
`maintenance`, `tariff`. Löschen eines Mandanten entfernt alles per Kaskade, Kamerabilder auch von der Platte.
