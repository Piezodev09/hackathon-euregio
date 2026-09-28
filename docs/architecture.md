# Architektur

Konkreter Vorschlag nach Projektplan Kapitel 3 – an die tatsächlich vorhandenen Geräte anpassen.

## Plattformen und Aufgaben

| Plattform | Aufgabe | Code |
|---|---|---|
| Arduino | Sensoren lesen, Belegung 2 s entprellen, LEDs setzen, JSON-Zeilen über USB-Seriell senden (bei Änderung, bei Vibration max. alle 0,5 s, Heartbeat alle 10 s) | `arduino/smart_bike_station/` |
| Raspberry Pi | Zeilen prüfen, Platz zuordnen, Sequenznummer vergeben, bis zu 500 Nachrichten puffern, per HTTPS senden; schweigt der Arduino > 15 s, alle Plätze als Sensorfehler melden; Netzstatus an Arduino zurück | `pi-gateway/gateway.py` |
| Debian-VM auf Proxmox | FastAPI: Messungen annehmen, Zustände ableiten, Regel + KI auswerten, SQLite, Dashboard ausliefern | `backend/`, `dashboard/` |
| Browser | Live-Belegung, Empfehlung, Warnungen, Zeitstempel, Verlauf; Verwaltung getrennt und nur mit Admin-Token | `dashboard/` |

## Datenfluss

```
Fahrrad einstellen
  -> Sensor misst Abstand/Präsenz
  -> Arduino entprellt (2 s) und meldet {"slot_id","presence","vibration","seq","state"}
  -> Pi prüft Format/Plausibilität, ordnet Stellplatz zu, vergibt Sequenznummer
       +-> API nicht erreichbar? -> puffern (begrenzt), später mit age_ms nachsenden
  -> API prüft Geräte-Token + Eingaben, setzt Server-Zeitstempel
       +-> SQLite: measurement (+ event bei Sensorfehler/Warnung)
       +-> belegt + Vibration: Merkmale berechnen -> Regel und KI bewerten
  -> Dashboard fragt alle 2 s den Status ab
```

## Zustände

```
[FREI] <-- Fahrrad erkannt / entfernt --> [BELEGT]
  | Messung unplausibel / Sensorausfall / keine Daten seit 30 s
  v
[UNBEKANNT] -- gültige Messungen --> neuer Zustand
[BELEGT] -- auffällige Vibration --> Warnereignis (Platz bleibt BELEGT)
```

Ein Alarm ist kein Belegungszustand. „Unbekannt“ wird an drei Stellen erzwungen:

1. **Arduino**: nach dem Start und nach 5 ungültigen Messungen in Folge `presence=-1`.
2. **Gateway**: kommt vom Arduino nichts mehr, meldet es `sensor_state="error"` für alle Plätze.
3. **API**: letzte Meldung älter als `stale_after_s` (30 s) → `unknown/stale`.
4. **Browser**: letzte erfolgreiche Antwort älter als 30 s → alle Plätze `unknown/connection`.

## Zeitwerte (Planungsannahmen, in Konfiguration)

| Wert | Default | Ort |
|---|---|---|
| Entprellung | 2 s | `STABLE_MS` im Sketch |
| Heartbeat | 10 s | `HEARTBEAT_MS` im Sketch, `timing.heartbeat_s` im Gateway |
| Arduino-Timeout | 15 s | `timing.arduino_timeout_s` im Gateway |
| „unbekannt/veraltet“ | 30 s | `timing.stale_after_s` in `backend/config.toml` |
| Dashboard-Abfrage | 2 s | `timing.ui_poll_interval_s` |
| Schonzeit nach Belegungswechsel | 15 s | `anomaly.grace_period_s` |

## Warum so einfach?

Kein Message-Broker, kein Kubernetes, eine Datenbankdatei, Polling statt WebSockets:
weniger Integrations- und Fehleraufwand bei wenigen Demo-Plätzen (Plan 3.2).
Gateway nutzt nur die Python-Standardbibliothek + pyserial, damit die Installation auch
in Netzen mit Proxy/eingeschränktem Internet klappt.
