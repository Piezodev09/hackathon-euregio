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

## Die Plattform (SaaS)

Kunden (Schulen, Unternehmen, Kommunen) registrieren sich selbst, legen eine Organisation an und
verwalten darin Stationen, Stellplätze, Gateways und ihr Team. Der Betreiber sieht alle Kunden
in der Plattform-Ansicht.

| Bereich | Inhalt |
|---|---|
| **Landingpage** `/` | Funktionen, Ablauf, Sicherheit, Tarife (Free · Schule · Pro), Registrierung |
| **Kundenportal** `/app` | Übersicht mit Kennzahlen, Live-Ansicht je Station (Belegung, Empfehlung, Warnungen, Heatmap, KI-Status), Stationsverwaltung (Plätze, Geräte-Tokens, Anzeige-Link, Gateway-Konfiguration), Meldungen mit Quittieren, Team mit Rollen und Einladungen, Konto & Sicherheit (2FA, Sitzungen, Passwort), Organisation (2FA-Pflicht, Export, Löschung), Tarif & Nutzung, Audit-Log |
| **Kiosk-Anzeige** `/display#<token>` | öffentliche Nur-Lese-Anzeige für Bildschirme an der Station (DE/NL/EN) |
| **Plattform** `/app#/platform` | Betreiber: Kunden, Tarife, Sperren, MRR, Kennzahlen |
| **Agent für Raspberry Pi** `/install/agent.sh` | Installation per Kopplungscode aus dem Portal, Heartbeat mit Zustand, Konfiguration aus der Cloud, Fernbefehle, Token-Rotation, Selbst-Update mit Prüfsumme und Rollback – siehe [docs/agent.md](docs/agent.md) |

Rollen: **Inhaber** (alles inkl. Tarif/Export/Löschung) · **Administrator** (Stationen, Geräte, Team) ·
**Betreuer** (Live-Daten, Meldungen quittieren) · **Lesend**.

Sicherheit (Details: [docs/security-privacy.md](docs/security-privacy.md)): scrypt-Passwörter mit
Richtlinie, E-Mail-Bestätigung, TOTP-2FA mit Wiederherstellungscodes (verschlüsselt gespeichert,
per Organisation erzwingbar), Kontosperre und Ratenbegrenzung, serverseitige Sessions mit
`__Host-`/HttpOnly/Secure/SameSite=Strict-Cookie, CSRF-Token + Origin-Prüfung, strikte
Mandantentrennung, gehashte Geräte-Tokens je Station, strenge CSP und Sicherheits-Header,
Trusted Hosts, Größenlimits, Audit-Log, Datenexport und Löschung, Secure-by-default-Prüfung
für `production`.

Noch **nicht** enthalten: Zahlungsanbindung (Abrechnung manuell), echte Datenschutzerklärung/Impressum
(Platzhalter), externer Penetrationstest.

## Schnellstart ohne Hardware

Voraussetzung: Python ≥ 3.11.

```bash
pip install -r backend/requirements-dev.txt pyserial
scripts/dev.sh
```

Das Skript legt beim ersten Start einen Demo-Kunden mit Station an, startet die Plattform und
koppelt einen lokalen Agenten (wie auf dem Pi) mit eingebautem Simulator. Dann:

- Portal: <http://127.0.0.1:8000/app> – Login `demo@example.org` / `Fahrradplatz-Euregio-2026!`
- Kiosk-Link: steht in der Konsole (`Kiosk-Anzeige: …`)
- Eigene Registrierung: <http://127.0.0.1:8000/app#/register> – der Bestätigungslink erscheint im Log
- Gateway im Portal unter **Gateways**: Status, Neustart, Token erneuern, Updates
- Tastaturgesteuerter Simulator: `scripts/dev.sh --interactive`, dann `p A` (belegen/freigeben), `b A` (anstoßen), `s A` (rütteln), `e A` (Sensorfehler), `q`

Echten Raspberry Pi einbinden: Portal → Station → Einstellungen → **Gateway einrichten** und die
angezeigten drei Befehle auf dem Pi ausführen ([docs/agent.md](docs/agent.md)).

Plattform-Admin anlegen: `cd backend && python3 -m app.cli create-platform-admin --email ops@example.org`

KI-Modell für die Pipeline-Probe (simulierte Daten): `python3 ml/generate_synthetic.py && python3 ml/train.py ml/data/synthetic.csv`

## Tests

```bash
cd backend && python3 -m pytest -q          # 54 Tests: Abnahmetests, Auth, 2FA, CSRF, Mandantentrennung, Rollen, Tarife, Header, Agent-Verwaltung
cd pi-gateway && python3 -m pytest -q tests # 33 Tests: Parser, Puffer, Watchdog, Sequenzen, Agent (Zustand, Updates, Rollback, Befehle)
```

## Verzeichnisse

| Pfad | Inhalt |
|---|---|
| `arduino/` | Arduino-Sketch |
| `pi-gateway/` | Agent (`agent.py`), Gateway, Simulator, `VERSION` des Agent-Pakets |
| `backend/` | FastAPI-Plattform (Auth, Mandanten, Stationen, Telemetrie, Plattform-Admin), CLI, Tests |
| `web/` | Landingpage, Kundenportal, Kiosk-Anzeige (HTML/CSS/JS-Module ohne Framework) |
| `ml/` | Datenexport, Training, Vergleich Regel vs. KI |
| `deploy/` | systemd-Units, Installationsskripte, Firewall-Beispiel, Backup, Löschen |
| `docs/` | Architektur, API, Hardware, Sicherheit/Datenschutz, KI-Steckbrief, Tests, Betrieb, Demo |

## Dokumentation

- [Architektur und Datenfluss](docs/architecture.md)
- [Agent für Raspberry Pi: Installation, Kopplung, Updates](docs/agent.md)
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
- **Keine Geheimnisse im Repository.** Datenschlüssel und SMTP-Passwort nur über Umgebungsvariablen; Geräte-Tokens entstehen im Portal und werden nur gehasht gespeichert.
