# Smart Bicycle Box – Hackathon Euregio

> **Der Fahrradstellplatz, der ehrlich sagt, ob er frei ist.** Sensor, Display und Portal in einem – mit
> Ein-/Auschecken per Karte, Parkgebühren oder Guthaben, Reservierungen, Öffnungszeiten, Berichten, E-Mail-Benachrichtigungen
> und Schnittstellen für die Schul-IT. Registrieren, Start-Tour, Beispiel-Stellplatz: in 2 Minuten ausprobiert, ohne Hardware.
> Produktbeschreibung und Vermarktung: [docs/produkt.md](docs/produkt.md)

Ein **einzelner, vorne offener Fahrradstellplatz** für eine Schul-Demonstration. Ein Sensor erkennt,
ob ein Fahrrad im Stellplatz steht; ein Display oben an der Vorderseite zeigt gut lesbar
**FREI**, **BELEGT**, **RESERVIERT** oder **STATUS UNBEKANNT** – immer mit Text, Symbol und Farbe. Ein
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
| Parkgebühren, Guthaben und Lizenzabrechnung | umgesetzt **ohne Zahlungsanbieter**: Beträge, Monatsaufstellungen oder Guthaben je Karte, Rechnungen, CSV; „bezahlt“/„aufgeladen“ wird von Hand gebucht |
| Reservierungen, Öffnungs-/Sperrzeiten, Wartungsmodus | umgesetzt und automatisch getestet |
| E-Mail-Benachrichtigungen, Tages-/Wochenberichte (PDF/CSV) | umgesetzt und getestet – **E-Mail nur im Konsolen-Modus geprüft**, kein echter SMTP-Server |
| API-Schlüssel und Webhooks für die Schul-IT | umgesetzt und getestet (Test-Empfänger), keine echte Schul-App angebunden |
| Registrierung ohne E-Mail-Bestätigung, Start-Tour, Beispiel-Stellplatz (Server-Simulation) | umgesetzt, im Browser Ende-zu-Ende geprüft |
| Offline-Anzeige am Pi | Agent liefert die Anzeige lokal aus; mit Tests/Simulator geprüft – **Chromium-Kiosk auf echtem Pi nicht getestet** |
| Kamera am Raspberry Pi | optional, standardmäßig aus; Software fertig, nur mit dem Testbild des Simulators getestet; **Genehmigung liegt nicht vor** |
| Zertifikat für die Pi-Einrichtung | selbst signiert mit IP-Adresse, Pi vertraut per angeheftetem Schlüssel (Pinning) – lokal Ende-zu-Ende getestet |
| KI-Funktion (Bewegungserkennung, Vergleich mit Regel) | vorbereitet (`ml/`), bisher nur mit simulierten Daten trainiert |

## Aufbau des Repositorys

| Pfad | Inhalt |
|---|---|
| `design/` | Design-Entwurf: Klick-Prototyp (`smart-bicycle-box-prototyp.html`, nur Demodaten), Stellplatz-Skizzen, Architektur, Design-Tokens |
| `arduino/smart_bicycle_box/` | Firmware für genau einen Stellplatz, optional NFC-Leser PN532 (Pins und Schwellwerte sind Platzhalter) |
| `pi-gateway/` | Agent für den Raspberry Pi (`agent.py` inkl. `doctor` und lokaler Offline-Anzeige), Gateway, Kamera (`camera.py`), Simulator, `VERSION` |
| `backend/` | FastAPI-Plattform: Konten, Organisationen, Stellplätze, Telemetrie, NFC-Karten, Parkgebühren/Guthaben, Reservierungen, Öffnungszeiten, E-Mails, Berichte (PDF/CSV), API/Webhooks, Beispiel-Simulation, Lizenzen/Rechnungen, Kamerabilder, Plattform-Admin, CLI, Tests |
| `web/` | Landingpage, Kundenportal, Kiosk-Anzeige, Stellplatz-Ansicht – gestaltet nach dem Design-System (`static/css/tokens.css`, `components.css`) |
| `ml/` | Datenexport, Training, Vergleich Regel vs. KI |
| `deploy/` | systemd-Units, Installation (`install-vm.sh`), Zertifikat (`make-cert.sh`), Update mit Rollback (`update.sh`), Firewall-Beispiel, Backup, Löschen |
| `scripts/` | `dev.sh` (lokal mit Simulator), `demo-reset.sh` (frische Demo), `check.sh` (alle Prüfungen) |
| `docs/` | Produkt & Vermarktung, Architektur, API, Abrechnung, Hardware, Sicherheit/Datenschutz, KI-Steckbrief, Tests, Betrieb, Demo |

## Die Plattform

Eine Organisation (z. B. eine Schule) verwaltet ihre Stellplätze. **Jede Station ist genau ein
Stellplatz** mit eigenem Gateway, eigenem Geräte-Token und eigenem Anzeige-Link.

| Bereich | Inhalt |
|---|---|
| **Landingpage** `/` | Nutzenversprechen, Vorteile, 12 Funktionen, Zielgruppen (Schulen, Gemeinden, Unternehmen), „Ohne Hardware ausprobieren“, Ablauf, Einrichtung in 3 Schritten, Sicherheit, Preise mit Kostenrechner, FAQ, ehrlicher „Stand der Umsetzung“ |
| **Registrierung** `/app#/register` | ohne E-Mail-Bestätigung sofort angemeldet, Tarifwahl (30 Tage Testphase), danach startet die **Start-Tour** (17 Schritte, jederzeit über „Tour starten“) und die Checkliste **Erste Schritte** mit **Beispiel-Stellplatz** |
| **Kundenportal** `/app` | Übersicht mit Kennzahlen (gerade geparkt, Check-ins und Gebühren heute); Live-Ansicht je Stellplatz mit Status-Karte, laufendem Parkvorgang, Warnungen (mit Kamerabild, falls freigegeben), Belegung nach Uhrzeit, KI-Status; Einstellungen (Wartungsmodus, Tarif, **Öffnungs- und Sperrzeiten**, Gateways, Stellplatz-Ansicht mit QR-Aufkleber, Anzeige-Link, Kamera); **Reservierungen**; **Berichte** (Tag/Woche/Monat, PDF/CSV, per E-Mail); **Karten** (anlernen, benennen, sperren, **Guthaben aufladen**, Buchungen); **Parkvorgänge**; **Parkgebühren** (Tarif, Monatsaufstellung oder Guthaben, CSV); **Integrationen** (API-Schlüssel, Webhooks); Tarif & Nutzung mit Lizenz und Rechnungen; Meldungen, Team und Rollen, Konto & Sicherheit (2FA, **E-Mail-Benachrichtigungen**), Organisation, Audit-Log |
| **Kiosk-Anzeige** `/display#<token>` | Vollbild-Status für das Display am Stellplatz (DE/NL/EN), nur lesend; RESERVIERT mit Restzeit, „Geschlossen – öffnet …“, Rückmeldung beim Ein-/Auschecken mit Guthaben, „AUSSER BETRIEB“, Kamera-Hinweis. Am Pi auch lokal: `http://127.0.0.1:8088/local` (bei Netzausfall „OFFLINE“ mit Sensorzustand) |
| **Stellplatz-Ansicht** `/s#<token>` | für die Person am Stellplatz (per QR-Code): Status, Preise, Öffnungszeiten, Anleitung, laufender Betrag/Guthaben nach dem Einchecken, „Problem melden“ (DE/NL/EN) |
| **Externe API** `/api/v1/ext/…` | mit API-Schlüssel: Status, Ereignisse, Karten, Berichte lesen, Reservierungen anlegen/beenden; Webhooks mit HMAC-Signatur ([docs/api.md](docs/api.md)) |
| **Plattform** `/app#/platform` | Betreiber: Kunden, Tarife, Sperren, Kennzahlen; **Rechnungen**: Stellplatz-Tage × Tagespreis + Grundgebühr, Vertragspreise, Rechnungen festschreiben, CSV |
| **Agent für Raspberry Pi** `/install/agent.sh` | Installation per Kopplungscode, Heartbeat, Fernbefehle, Token-Rotation, Selbst-Update mit Prüfsumme und Rollback – siehe [docs/agent.md](docs/agent.md) |

Rollen: **Inhaber** · **Administrator** · **Betreuer** (Live-Daten, Meldungen quittieren, reservieren, Guthaben aufladen) · **Lesend**.
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
- **Oder wie ein Neukunde:** <http://127.0.0.1:8000/> → „30 Tage kostenlos testen“ → sofort angemeldet, Start-Tour,
  „Beispiel-Stellplatz anlegen“ (Server-Simulation, als SIMULATION gekennzeichnet)
- Offline-Anzeige wie am Pi: <http://127.0.0.1:8088/local> (vom Agenten ausgeliefert)
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
                                            # NFC, Gebühren, Guthaben, Lizenz/Rechnungen, Kamera, Stellplatz-Ansicht, Reservierung,
                                            # Öffnungszeiten, E-Mails, Berichte (PDF), API/Webhooks, Beispiel-Simulation, Onboarding
cd pi-gateway && python3 -m pytest -q tests # Parser (inkl. NFC), Puffer, Watchdog, Sequenzen, Simulator, Agent (Updates, Rollback, Befehle, Kamera, doctor, Offline-Anzeige)
```

## Dokumentation

- [Produktbeschreibung und Vermarktung](docs/produkt.md) – Pitch, Zielgruppen, Pakete, Demo-Ablauf, Einwände
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

✔ = umgesetzt (Software, mit Tests/Simulator geprüft; Hardware-Grenzen siehe „Stand“ oben). Alle 15 Vorschläge sind umgesetzt.

| Nr. | Funktion | Stand |
|---|---|---|
| 1 | NFC-Ein-/Auschecken mit Karten-Anlernen und Sperren | ✔ |
| 2 | Parkgebühren je Tag/Stunde/pauschal mit Freiminuten und Tageshöchstbetrag | ✔ |
| 3 | Lizenzabrechnung pro Stellplatz und Tag, Vertragspreise, Rechnungen, CSV | ✔ |
| 4 | Kamera-Einzelbild bei Erschütterung (opt-in, Freigabe-Vermerk, automatische Löschung) | ✔ |
| 5 | Stellplatz-Ansicht per QR-Code mit laufendem Betrag und „Problem melden“ | ✔ |
| 6 | Druckbarer QR-Aufkleber für den Stellplatz | ✔ |
| 7 | `bike-agent doctor`: Diagnose auf dem Pi (Arduino, Kamera, Zertifikat, Token, Uhrzeit) | ✔ |
| 8 | Reservierung eines Stellplatzes für 5–240 Minuten (Status „RESERVIERT“, optional für eine Karte, auch per API) | ✔ |
| 9 | Wartungsmodus (Display „AUSSER BETRIEB“, Karten werden abgelehnt, keine Warnungen) | ✔ |
| 10 | E-Mail-Benachrichtigung bei Warnung, gemeldetem Problem, Sensorfehler, Gateway offline/wieder online (je Person einstellbar, gedrosselt) | ✔ |
| 11 | Tages-/Wochen-/Monatsbericht (Auslastung, Check-ins, Gebühren, Warnungen) als PDF/CSV, automatisch per E-Mail | ✔ |
| 12 | Guthaben-/Prepaid-Konto je Karte statt Monatsaufstellung | ✔ |
| 13 | Öffnungs- und Sperrzeiten je Stellplatz (z. B. Ferien, nachts) | ✔ |
| 14 | Offline-Anzeige am Pi: Display zeigt den Status auch ohne Verbindung zur Plattform | ✔ |
| 15 | Webhooks und API-Schlüssel für die Schul-IT (z. B. Anbindung an die Schul-App) | ✔ |
