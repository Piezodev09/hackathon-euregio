# Smarte Radstation – Hackathon Euregio

Kleine, vorführbare Fahrradstation für den Schulkontext: erkennt die Belegung einzelner
Stellplätze, zeigt freie Plätze an, empfiehlt einen freien Platz und meldet auffällige
Bewegungen als **Verdacht, nicht als Diebstahlnachweis**. Arduino, Raspberry Pi und
Proxmox sind jeweils mit einer klaren Aufgabe eingebunden.

> Leitsatz: Eine zuverlässig funktionierende Kette von Sensor bis Dashboard ist wichtiger
> als viele halb fertige Funktionen.

```
Sensoren ─► Arduino ──USB-Seriell──► Raspberry Pi ──HTTPS──► Debian-VM (Proxmox) ──HTTPS──► Browser
            entprellen,              validieren, puffern,    API + SQLite + KI            Dashboard
            LEDs                     Sequenznummern          + Dashboard                  DE/NL/EN
```

## Stand der Umsetzung

| Priorität | Bestandteil | Stand |
|---|---|---|
| P0 | Arduino-Firmware (Präsenz, Vibration, LEDs, JSON-Zeilen) | Sketch fertig, **Pins/Sensoren vor Ort prüfen**, nicht auf Hardware getestet |
| P0 | Pi-Gateway (Seriell, Validierung, Puffer, Watchdog, systemd) | fertig, getestet mit Simulator |
| P0 | API + SQLite (Status, Ereignisse, Quittieren, Health) | fertig, automatisierte Tests |
| P0 | Zustände frei / belegt / **unbekannt**, Zeitstempel, Timeout | fertig |
| P0 | Warnung: Baseline-Regel + KI (Isolation Forest) im Vergleich | fertig; Modell bisher nur mit **simulierten** Daten trainiert |
| P0 | Basisschutz: Geräte-/Admin-Token, Validierung, Rate Limit, Audit-Log, Security-Header | fertig |
| P1 | Auslastung je Stunde (Heatmap + Textzusammenfassung) | fertig |
| P1 | DE/NL/EN, Bedienung ohne Farbe und per Tastatur | fertig; Übersetzungen noch prüfen lassen |
| P1 | Empfehlung freier Platz (transparente Regel, keine KI) | fertig |
| P2 | Prognose, Solar, RFID, QR | bewusst nicht umgesetzt |

## Schnellstart ohne Hardware (Simulator)

Voraussetzung: Python ≥ 3.11.

```bash
pip install -r backend/requirements-dev.txt pyserial
scripts/dev.sh              # startet API + Simulator + Gateway
```

Dann <http://127.0.0.1:8000> öffnen. Im Terminal steuert man den Simulator:
`p A` (Platz A belegen/freigeben), `b A` (leicht anstoßen), `s A` (kräftig rütteln),
`e A` (Sensorfehler ein/aus), `q` (beenden). Alle Simulatordaten sind im Dashboard
deutlich als **simuliert** gekennzeichnet. Admin-Token für die Verwaltung:
`dev-admin-token-change-me` (nur lokal!).

KI-Modell für die Pipeline-Probe trainieren (simulierte Daten):

```bash
python3 ml/generate_synthetic.py
python3 ml/train.py ml/data/synthetic.csv    # schreibt ml/models/… und ml/report.md
```

## Tests

```bash
cd backend && python3 -m pytest -q          # API, Zustände, Sicherheit (T01–T13 soweit ohne Hardware)
cd pi-gateway && python3 -m pytest -q tests # Parser, Puffer, Watchdog, Sequenzen
```

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `arduino/` | Arduino-Sketch |
| `pi-gateway/` | Gateway, Simulator, Beispielkonfiguration |
| `backend/` | FastAPI-App, Konfiguration (`config.toml`), Tests |
| `dashboard/` | Weboberfläche (HTML/CSS/JS ohne Framework) |
| `ml/` | Datenexport, Training, Vergleich Regel vs. KI |
| `deploy/` | systemd-Units, Installationsskripte, Firewall-Beispiel, Backup, Löschen |
| `docs/` | Architektur, API, Hardware, Sicherheit/Datenschutz, KI-Steckbrief, Tests, Betrieb, Demo |

## Dokumentation

- [Architektur und Datenfluss](docs/architecture.md)
- [API und Datenformat](docs/api.md)
- [Hardware und Aufbau](docs/hardware.md) – Inventur vor der Verdrahtung ausfüllen!
- [Sicherheit und Datenschutz](docs/security-privacy.md)
- [KI-Steckbrief](docs/ki-steckbrief.md)
- [Testprotokoll](docs/test-report.md)
- [Betrieb: Installation, Neustart, Logs, Backup, Löschen](docs/betrieb.md)
- [Regieplan Abschlussdemo](docs/demo-script.md)

## Wichtige Grundsätze

- **Kein „frei“ bei unbekanntem Sensorstatus.** Fehlende, veraltete (> 30 s) oder fehlerhafte
  Daten ergeben „unbekannt“ – im Backend *und* zusätzlich im Browser bei Verbindungsverlust.
- **Keine KI-Behauptung ohne Test.** `ml/train.py` vergleicht Modell und Regel auf denselben
  Testläufen; die sichtbare Warnung kommt aus dem besseren Verfahren (`alert_source`).
- **Keine personenbezogene Diebstahlbehauptung.** Keine Kameras, keine Namen, kein RFID.
- **Keine Geheimnisse im Repository.** Tokens nur über Umgebungsvariablen/Dateien außerhalb Git.
