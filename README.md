# Smart Bicycle Box – Hackathon Euregio

Ein **einzelner, vorne offener Fahrradstellplatz** für eine Schul-Demonstration. Ein Sensor erkennt,
ob ein Fahrrad im Stellplatz steht; ein Display oben an der Vorderseite zeigt gut lesbar
**FREI**, **BELEGT** oder **STATUS UNBEKANNT** – immer mit Text, Symbol und Farbe. Ein
Erschütterungssensor meldet ungewöhnliche Bewegungen als **Hinweis, nicht als Diebstahlnachweis**.
Mit einer Karte am **NFC-Leser** an der Seite checken Radfahrende ein und aus. Die Organisation kann dafür
Parkgebühren festlegen. Eine nach innen gerichtete **Kamera** am Raspberry Pi ist optional, im Portal
standardmäßig aus und nur nach Freigabe nutzbar. Eine Genehmigung liegt bisher **nicht** vor.

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
| NFC-Ein-/Auschecken | Software fertig (Sketch mit PN532, Gateway, Plattform, Portal) und mit Simulator getestet – **Leser noch nicht angeschlossen, Sketch nicht kompiliert** |
| Parkgebühren und Lizenzabrechnung | umgesetzt **ohne Zahlungsanbieter**: Beträge, Monatsaufstellungen, Rechnungen, CSV; „bezahlt“ wird von Hand gesetzt |
| Kamera am Raspberry Pi | optional, standardmäßig aus; Software fertig, nur mit dem Testbild des Simulators getestet; **Genehmigung liegt nicht vor** |
| Zertifikat für die Pi-Einrichtung | selbst signiert mit IP-Adresse, Pi vertraut per angeheftetem Schlüssel (Pinning) – lokal Ende-zu-Ende getestet |
| KI-Funktion (Bewegungserkennung, Vergleich mit Regel) | vorbereitet (`ml/`), bisher nur mit simulierten Daten trainiert |

## Aufbau des Repositorys

| Pfad | Inhalt |
|---|---|
| `design/` | Design-Entwurf: Klick-Prototyp (`smart-bicycle-box-prototyp.html`, nur Demodaten), Stellplatz-Skizzen, Architektur, Design-Tokens |
| `arduino/smart_bicycle_box/` | Firmware für genau einen Stellplatz, optional NFC-Leser PN532 (Pins und Schwellwerte sind Platzhalter) |
| `pi-gateway/` | Agent für den Raspberry Pi (`agent.py` inkl. `doctor`), Gateway, Kamera (`camera.py`), Simulator, `VERSION` |
| `backend/` | FastAPI-Plattform: Konten, Organisationen, Stellplätze, Telemetrie, NFC-Karten, Parkgebühren, Lizenzen/Rechnungen, Kamerabilder, Plattform-Admin, CLI, Tests |
| `web/` | Landingpage, Kundenportal, Kiosk-Anzeige, Stellplatz-Ansicht – gestaltet nach dem Design-System (`static/css/tokens.css`, `components.css`) |
| `ml/` | Datenexport, Training, Vergleich Regel vs. KI |
| `deploy/` | systemd-Units, Installation (`install-vm.sh`), Zertifikat (`make-cert.sh`), Update mit Rollback (`update.sh`), Firewall-Beispiel, Backup, Löschen |
| `scripts/` | `dev.sh` (lokal mit Simulator), `demo-reset.sh` (frische Demo), `check.sh` (alle Prüfungen) |
| `docs/` | Architektur, API, Hardware, Sicherheit/Datenschutz, KI-Steckbrief, Tests, Betrieb, Demo |

## Die Plattform

Eine Organisation (z. B. eine Schule) verwaltet ihre Stellplätze. **Jede Station ist genau ein
Stellplatz** mit eigenem Gateway, eigenem Geräte-Token und eigenem Anzeige-Link.

| Bereich | Inhalt |
|---|---|
| **Landingpage** `/` | Funktionen, Ein-/Auschecken, Parkgebühren, Lizenzmodell mit Kostenrechner, Kamera (opt-in), Einrichtung in 3 Schritten, FAQ, ehrlicher „Stand der Umsetzung“, Registrierung |
| **Kundenportal** `/app` | Übersicht mit Kennzahlen (gerade geparkt, Check-ins und Gebühren heute); Live-Ansicht je Stellplatz mit Status-Karte, laufendem Parkvorgang, Warnungen (mit Kamerabild, falls freigegeben), Belegung nach Uhrzeit, KI-Status; Einstellungen (Wartungsmodus, Tarif, Gateways, Stellplatz-Ansicht mit QR-Aufkleber, Anzeige-Link, Kamera); **Karten** (anlernen, benennen, sperren); **Parkvorgänge**; **Parkgebühren** (Tarif, Monatsaufstellung, CSV); Tarif & Nutzung mit Lizenz und Rechnungen; Meldungen, Team und Rollen, Konto & Sicherheit (2FA), Organisation, Audit-Log |
| **Kiosk-Anzeige** `/display#<token>` | Vollbild-Status für das Display am Stellplatz (DE/NL/EN), nur lesend; kurze Rückmeldung beim Ein-/Auschecken, „AUSSER BETRIEB“, Kamera-Hinweis |
| **Stellplatz-Ansicht** `/s#<token>` | für die Person am Stellplatz (per QR-Code): Status, Preise, Anleitung, laufender Betrag nach dem Einchecken, „Problem melden“ (DE/NL/EN) |
| **Plattform** `/app#/platform` | Betreiber: Kunden, Tarife, Sperren, Kennzahlen; **Rechnungen**: Stellplatz-Tage × Tagespreis + Grundgebühr, Vertragspreise, Rechnungen festschreiben, CSV |
| **Agent für Raspberry Pi** `/install/agent.sh` | Installation per Kopplungscode, Heartbeat, Fernbefehle, Token-Rotation, Selbst-Update mit Prüfsumme und Rollback – siehe [docs/agent.md](docs/agent.md) |

Rollen: **Inhaber** · **Administrator** · **Betreuer** (Live-Daten, Meldungen quittieren) · **Lesend**.
Sicherheit: siehe [docs/security-privacy.md](docs/security-privacy.md).
Abrechnung: [docs/abrechnung.md](docs/abrechnung.md) – Schule 9 € / Monat + 0,20 € je Stellplatz und Tag,
Pro 29 € + 0,15 €, jeweils 30 Tage kostenlos testen (Richtwerte für die Demo).
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
- Kiosk-Anzeige und Stellplatz-Ansicht: Links stehen in der Konsole (`Kiosk-Anzeige: …`, `Stellplatz-Ansicht (QR): …`)
- Die Demo enthält die freigegebene Demo-Karte `04A1B2C3D4`, einen Parktarif (0,50 € je Tag, 15 Freiminuten)
  und 7 Tage simulierte Parkhistorie. Der Simulator-Agent tappt die Karte gelegentlich selbst.
- Tastaturgesteuerter Simulator: `scripts/dev.sh --interactive`, dann `p` (einstellen/ausparken),
  `b` (anstoßen), `s` (rütteln), `e` (Sensorfehler), `n` (Demo-Karte an den Leser), `n 04AABBCCDD` (unbekannte Karte), `q`
- Alles zurücksetzen: `scripts/demo-reset.sh --yes`
- Nur das Design ansehen: `design/smart-bicycle-box-prototyp.html` direkt im Browser öffnen

Echten Raspberry Pi einbinden: Portal → Stellplatz → Einstellungen → **Gateway einrichten** und die
angezeigten Befehle auf dem Pi ausführen ([docs/agent.md](docs/agent.md)). Diagnose auf dem Pi: `sudo bike-agent doctor`.

**Fehler `curl: (60) … self-signed certificate` bei der Pi-Einrichtung?** Das alte Zertifikat der VM enthält die
IP-Adresse nicht. Auf der VM einmalig:

```bash
sudo /opt/smart-bike-station/deploy/make-cert.sh --force 192.168.0.114
sudo systemctl restart bike-api
```

Danach im Portal *Gateway einrichten* neu öffnen und die **neuen** Befehle nutzen: Der Pi lädt das Zertifikat nur,
wenn dessen Schlüssel zum angezeigten Pin passt (`curl --pinnedpubkey`), und vertraut danach genau diesem Zertifikat.

Plattform-Admin anlegen: `cd backend && python3 -m app.cli create-platform-admin --email ops@example.org`

KI-Pipeline mit simulierten Daten: `python3 ml/generate_synthetic.py && python3 ml/train.py ml/data/synthetic.csv`

## Tests

```bash
scripts/check.sh                            # alles: Tests, JS-Syntax, Übersetzungen DE/NL/EN, Shell-Syntax
cd backend && python3 -m pytest -q          # Abnahmetests, Auth, 2FA, CSRF, Mandantentrennung, Rollen, Tarife, Agent, Migration,
                                            # NFC, Gebühren, Lizenz/Rechnungen, Kamera, Stellplatz-Ansicht
cd pi-gateway && python3 -m pytest -q tests # Parser (inkl. NFC), Puffer, Watchdog, Sequenzen, Simulator, Agent (Updates, Rollback, Befehle, Kamera, doctor)
```

## Dokumentation

- [Architektur und Datenfluss](docs/architecture.md)
- [Hardware und Aufbau des Stellplatzes](docs/hardware.md) – Inventur vor der Verdrahtung ausfüllen!
- [Agent für Raspberry Pi](docs/agent.md)
- [API und Datenformat](docs/api.md)
- [Abrechnung: Parkgebühren und Lizenzmodell](docs/abrechnung.md)
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
- **Keine Diebstahlbehauptung, keine Personendaten.** Karten nur mit Bezeichnung, UID nur als HMAC gespeichert.
  Kamera standardmäßig aus, nur mit eingetragener Freigabe, nur Einzelbilder, automatische Löschung.
- **Geplantes nie als funktionierend darstellen.** Simulierte Daten sind immer gekennzeichnet.
- **Keine Geheimnisse im Repository.** Schlüssel nur über Umgebungsvariablen; Geräte-Tokens nur gehasht.

## 15 Funktionsvorschläge

✔ = in diesem Stand umgesetzt (Software, mit Tests/Simulator geprüft) · ○ = Vorschlag, noch nicht umgesetzt

| Nr. | Funktion | Stand |
|---|---|---|
| 1 | NFC-Ein-/Auschecken mit Karten-Anlernen und Sperren | ✔ |
| 2 | Parkgebühren je Tag/Stunde/pauschal mit Freiminuten und Tageshöchstbetrag | ✔ |
| 3 | Lizenzabrechnung pro Stellplatz und Tag, Vertragspreise, Rechnungen, CSV | ✔ |
| 4 | Kamera-Einzelbild bei Erschütterung (opt-in, Freigabe-Vermerk, automatische Löschung) | ✔ |
| 5 | Stellplatz-Ansicht per QR-Code mit laufendem Betrag und „Problem melden“ | ✔ |
| 6 | Druckbarer QR-Aufkleber für den Stellplatz | ✔ |
| 7 | `bike-agent doctor`: Diagnose auf dem Pi (Arduino, Kamera, Zertifikat, Token, Uhrzeit) | ✔ |
| 8 | Reservierung eines Stellplatzes für X Minuten (Status „RESERVIERT“) | ○ |
| 9 | Wartungsmodus (Display „AUSSER BETRIEB“, Karten werden abgelehnt) | ✔ |
| 10 | E-Mail-Benachrichtigung bei Warnung oder Offline-Gateway (SMTP ist vorhanden) | ○ |
| 11 | Wochenbericht für die Schule (Belegung, Parkvorgänge, Warnungen) als PDF/CSV | ○ |
| 12 | Guthaben-/Prepaid-Konto je Karte statt Monatsaufstellung | ○ |
| 13 | Öffnungs- und Sperrzeiten je Stellplatz (z. B. Ferien, nachts) | ○ |
| 14 | Offline-Anzeige am Pi: Display zeigt den Status auch ohne Verbindung zur Plattform | ○ |
| 15 | Webhooks und API-Schlüssel für die Schul-IT (z. B. Anbindung an die Schul-App) | ○ |
