# API und Datenformat

## Arduino → Pi (eine JSON-Zeile je Meldung, 115200 Baud)

```json
{"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}
```

| Feld | Werte | Bedeutung |
|---|---|---|
| `slot_id` | Kennung laut `slot_map` | Platz am Arduino |
| `presence` | `1` / `0` / `-1` | belegt / frei / kein gültiger Messwert |
| `vibration` | 0–1023 | Spitzenwert (analog) bzw. Impulse × Faktor (digital) im letzten Intervall |
| `seq` | Zahl | Zähler des Arduino (nur zur Diagnose; startet nach Reset neu) |
| `state` | `ok` / `error` | optional; `error` wie `presence=-1` |

Info-Zeilen wie `{"type":"hello","fw":"0.1.0"}` werden ignoriert. Pi → Arduino: `NET 1` / `NET 0`.

## Pi → API

`POST /api/v1/measurements` (einzeln) oder `POST /api/v1/measurements/batch` (`{"measurements":[…]}`, max. 200)

```json
{
  "station_id": "demo-01",
  "slot_id": "A",
  "sequence": 1727517600123,
  "occupied": true,
  "vibration_score": 12,
  "sensor_state": "ok",
  "source": "live",
  "age_ms": 0
}
```

- `sequence` vergibt das Gateway monoton steigend (auch über Neustarts). Die Kombination
  Station + Platz + Sequenz ist eindeutig → Wiederholungen werden als `duplicate` ignoriert.
- `occupied: null` nur mit `sensor_state: "error"`.
- `source: "simulated"` für Simulatordaten – wird überall sichtbar gekennzeichnet.
- `age_ms`: wie lange die Nachricht im Gateway-Puffer lag. Der Server setzt den Zeitstempel
  `jetzt − age_ms` (der Pi braucht keine exakte Uhr). Nachgesendete Daten älter als das
  Merkmalsfenster lösen **keinen** Live-Alarm aus.
- Unbekannte Felder, falsche Typen, Werte außerhalb der Grenzen → `422` (ohne Eingaben zu spiegeln).

## Endpunkte

| Methode/Pfad | Zweck | Berechtigung |
|---|---|---|
| `POST /api/v1/measurements` | Messung annehmen | Geräte-Token |
| `POST /api/v1/measurements/batch` | gepufferte Messungen | Geräte-Token |
| `GET /api/v1/stations/demo-01/status` | aktuelle Platzübersicht inkl. Empfehlung, aktiver Warnungen, KI-Status | lesend (rate-limitiert) |
| `GET /api/v1/occupancy/summary?hours=24` | zeitgewichtete Belegung je Stunde und Platz | lesend |
| `GET /api/v1/events?include_shadow=true` | Warnungen/Sensorfehler | Admin-Token |
| `POST /api/v1/events/{id}/ack` | Warnung quittieren (protokolliert) | Admin-Token |
| `GET /health` | Dienst + Datenbank erreichbar, KI-Modell geladen | intern/Monitoring |

Token als `Authorization: Bearer <token>`. Fehlend → `401`, falsches Recht → `403`,
zu viele Anfragen → `429`. Abgewiesene Versuche landen in `audit_log`.

## Statusantwort (gekürzt)

```json
{
  "free_count": 1, "known_count": 3, "total": 3,
  "recommendation": "B", "recommendation_rule": "first_free_by_position",
  "server_time": "2026-10-01T10:42:08+00:00", "stale_after_s": 30,
  "simulated_data": false,
  "slots": [
    {"slot_id": "A", "state": "occupied", "unknown_reason": null, "last_update": "…", "age_s": 1.2,
     "alert": {"kind": "unusual_movement", "occurred_at": "…", "detector": "rule", "simulated": false}},
    {"slot_id": "B", "state": "free", …},
    {"slot_id": "C", "state": "unknown", "unknown_reason": "stale", …}
  ],
  "ai": {"visible_detector": "rule", "model_available": true, "model_info": {…}, "model_error": null}
}
```

`unknown_reason`: `no_data`, `stale`, `sensor_error` (im Browser zusätzlich `connection`).

## Ereignisse

| `kind` | `severity` | Bedeutung |
|---|---|---|
| `unusual_movement` | `warning` | sichtbare Warnung des konfigurierten Verfahrens (`alert_source`) |
| `unusual_movement` | `shadow` | das jeweils andere Verfahren hätte gewarnt (nur Vergleich, nicht öffentlich) |
| `sensor_fault` | `info` | Übergang eines Platzes in den Sensorfehler |

`detail` enthält die Entscheidungen beider Verfahren und die Merkmale – Grundlage für Test T15.

## Datenbank (SQLite)

`station`, `slot`, `measurement` (Rohdaten, nach `retention.measurements_max_age_h` gelöscht),
`event`, `audit_log`. Keine Tabellen für Personen, Fahrradeigentümer oder Schülerkonten.
