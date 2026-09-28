# Smart Bicycle Box – Hackathon Euregio

Ein **einzelner, vorne offener Fahrradstellplatz** für eine Schul-Demonstration. Ein Sensor erkennt,
ob ein Fahrrad im Stellplatz steht; ein Display oben an der Vorderseite zeigt gut lesbar
**FREI**, **BELEGT** oder **STATUS UNBEKANNT** – immer mit Text, Symbol und Farbe. Ein
Erschütterungssensor meldet ungewöhnliche Bewegungen als **Hinweis, nicht als Diebstahlnachweis**.
Ein NFC-Leser an der Seite ist vorgesehen; eine nach innen gerichtete Kamera ist nur eine optionale,
**nicht genehmigte** Idee.

> Grundsatz: Fehlen verlässliche, aktuelle Messungen, heißt der Zustand **STATUS UNBEKANNT** –
> niemals automatisch „frei“.

```
Sensoren ─► Arduino ──USB-Seriell──► Raspberry Pi ──HTTPS──► Edge-VM ──► App-VM (Proxmox) ──HTTPS──► Dashboard
Präsenz,    entprellt,               prüft, puffert,                     API + SQLite                  Portal + Display
Erschütt.   sendet JSON              Display lokal                       + Auswertung                  DE/NL/EN
```

## Stand – was vorhanden ist und was nicht

| Teil | Stand |
|---|---|
| Proxmox: Edge-VM und abgeschirmte App-VM | als Infrastruktur eingerichtet |
| Code in diesem Repository (Plattform, Gateway-Agent, Firmware, Oberflächen) | geschrieben und mit Tests/Simulator geprüft – **noch nicht auf den VMs installiert und nicht mit echter Hardware verbunden** |
| Sensoranbindung (Arduino ↔ Sensoren ↔ Pi) | geplant; Firmware noch nicht auf echter Hardware kompiliert/getestet |
| NFC-Leser | geplant, Funktion noch festzulegen, nicht angebunden |
| KI-Funktion (Bewegungserkennung, Vergleich mit Regel) | vorbereitet (`ml/`), bisher nur mit simulierten Daten trainiert |
| Kamera | nicht genehmigt, nicht Teil des Systems |

## Aufbau des Repositorys

| Pfad | Inhalt |
|---|---|
| `design/` | Design-Entwurf: Klick-Prototyp (`smart-bicycle-box-prototyp.html`, nur Demodaten), Stellplatz-Skizzen, Architektur, Design-Tokens |
| `arduino/smart_bicycle_box/` | Firmware für genau einen Stellplatz (Pins und Schwellwerte sind Platzhalter) |
| `pi-gateway/` | Agent für den Raspberry Pi (`agent.py`), Gateway, Simulator, `VERSION` |
| `backend/` | FastAPI-Plattform: Konten, Organisationen, Stellplätze, Telemetrie, Plattform-Admin, CLI, Tests |
| `web/` | Landingpage, Kundenportal, Kiosk-Anzeige – gestaltet nach dem Design-System (`static/css/tokens.css`, `components.css`) |
| `ml/` | Datenexport, Training, Vergleich Regel vs. KI |
| `deploy/` | systemd-Units, Installationsskripte, Firewall-Beispiel, Backup, Löschen |
| `docs/` | Architektur, API, Hardware, Sicherheit/Datenschutz, KI-Steckbrief, Tests, Betrieb, Demo |

## Die Plattform

Eine Organisation (z. B. eine Schule) verwaltet ihre Stellplätze. **Jede Station ist genau ein
Stellplatz** mit eigenem Gateway, eigenem Geräte-Token und eigenem Anzeige-Link.

| Bereich | Inhalt |
|---|---|
| **Landingpage** `/` | Funktionen, Ablauf, Sicherheit, Tarife, Registrierung, umschaltbares Status-Beispiel |
| **Kundenportal** `/app` | Übersicht aller Stellplätze; Live-Ansicht je Stellplatz mit Status-Karte, letzter Messung und Datenalter, technischen Warnungen, Belegung nach Uhrzeit, KI-Status; Einstellungen (Gateways, Anzeige-Link); Meldungen, Team und Rollen, Konto & Sicherheit (2FA), Organisation, Tarif, Audit-Log |
| **Kiosk-Anzeige** `/display#<token>` | Vollbild-Status für das Display am Stellplatz (DE/NL/EN), nur lesend |
| **Plattform** `/app#/platform` | Betreiber: Kunden, Tarife, Sperren, Kennzahlen |
| **Agent für Raspberry Pi** `/install/agent.sh` | Installation per Kopplungscode, Heartbeat, Fernbefehle, Token-Rotation, Selbst-Update mit Prüfsumme und Rollback – siehe [docs/agent.md](docs/agent.md) |

Rollen: **Inhaber** · **Administrator** · **Betreuer** (Live-Daten, Meldungen quittieren) · **Lesend**.
Sicherheit: siehe [docs/security-privacy.md](docs/security-privacy.md).
Noch **nicht** enthalten: Zahlungsanbindung, echte Datenschutzerklärung/Impressum (Platzhalter), externer Penetrationstest.

## Schnellstart ohne Hardware

Voraussetzung: Python ≥ 3.11.

```bash
pip install -r backend/requirements-dev.txt pyserial
scripts/dev.sh
```

Das Skript legt einen Demo-Kunden mit einem Stellplatz an, startet die Plattform und koppelt einen
lokalen Agenten mit eingebautem Simulator. Alle Werte sind dann **simuliert** und im Portal und auf
der Anzeige als „SIMULATION“ gekennzeichnet.

- Portal: <http://127.0.0.1:8000/app> – Login `demo@example.org` / `Fahrradplatz-Euregio-2026!`
- Kiosk-Anzeige: Link steht in der Konsole (`Kiosk-Anzeige: …`)
- Tastaturgesteuerter Simulator: `scripts/dev.sh --interactive`, dann `p` (einstellen/ausparken),
  `b` (anstoßen), `s` (rütteln), `e` (Sensorfehler), `q`
- Nur das Design ansehen: `design/smart-bicycle-box-prototyp.html` direkt im Browser öffnen

Echten Raspberry Pi einbinden: Portal → Stellplatz → Einstellungen → **Gateway einrichten** und die
angezeigten Befehle auf dem Pi ausführen ([docs/agent.md](docs/agent.md)).

Plattform-Admin anlegen: `cd backend && python3 -m app.cli create-platform-admin --email ops@example.org`

KI-Pipeline mit simulierten Daten: `python3 ml/generate_synthetic.py && python3 ml/train.py ml/data/synthetic.csv`

## Tests

```bash
cd backend && python3 -m pytest -q          # Abnahmetests, Auth, 2FA, CSRF, Mandantentrennung, Rollen, Tarife, Agent, Migration
cd pi-gateway && python3 -m pytest -q tests # Parser, Puffer, Watchdog, Sequenzen, Simulator, Agent (Updates, Rollback, Befehle)
```

## Dokumentation

- [Architektur und Datenfluss](docs/architecture.md)
- [Hardware und Aufbau des Stellplatzes](docs/hardware.md) – Inventur vor der Verdrahtung ausfüllen!
- [Agent für Raspberry Pi](docs/agent.md)
- [API und Datenformat](docs/api.md)
- [Sicherheit und Datenschutz](docs/security-privacy.md)
- [KI-Steckbrief](docs/ki-steckbrief.md)
- [Testprotokoll](docs/test-report.md)
- [Betrieb](docs/betrieb.md)
- [Regieplan Abschlussdemo](docs/demo-script.md)

## Wichtige Grundsätze

- **Kein „frei“ bei unbekanntem Zustand.** Fehlende, veraltete (> 30 s), in der Zukunft datierte
  oder fehlerhafte Messungen ergeben STATUS UNBEKANNT – in Firmware, Gateway, API *und* im Browser.
- **Zustand nie nur über Farbe.** Wort + Symbol + Farbe, bei UNBEKANNT zusätzlich Schraffur.
- **Keine KI-Behauptung ohne Test.** `ml/train.py` vergleicht Modell und Regel auf denselben Läufen.
- **Keine Diebstahlbehauptung, keine Personendaten.** Keine Kamera, keine Namen.
- **Geplantes nie als funktionierend darstellen.** Simulierte Daten sind immer gekennzeichnet.
- **Keine Geheimnisse im Repository.** Schlüssel nur über Umgebungsvariablen; Geräte-Tokens nur gehasht.
