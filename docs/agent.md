# Agent für Raspberry Pi – Installation, Kopplung, Betrieb

Der **Agent** läuft auf dem Raspberry Pi am Stellplatz. Er liest den Arduino über USB aus, überträgt
die Messungen verschlüsselt an die Plattform und wird zentral aus dem Portal verwaltet.
**Ab Version 1.4.0 kann ein Pi mehrere Stellplätze bedienen** (je Stellplatz ein Arduino) und erkennt
Arduinos, NFC-Leser und Kamera selbst – der Nutzer führt nur das Installationsskript aus.

```
Portal: "Gateway einrichten"  ──►  Kopplungscode (einmalig, 30 min)
                                        │
Pi:  curl …/install/agent.sh ─► Prüfsumme ─► sudo sh agent.sh --code XXXXX-XXXXX
                                        │
     Skript: Paket laden + SHA-256 prüfen ─► Benutzer bike-agent ─► Kopplung ─► systemd-Dienst
                                        │
Agent ◄──── Heartbeat (60 s): Konfiguration, Befehle, Updates ────► Plattform
      ─────  Messungen (Belegung, Vibration) ─────────────────────►
```

## Einrichtung (5 Minuten)

Voraussetzungen: Raspberry Pi mit **Raspberry Pi OS Bookworm** oder neuer (Python ≥ 3.11), Netzwerk,
Arduino mit dem Sketch aus `arduino/` per USB.

1. Ein Stellplatz: Portal → **Stellplätze** → Stellplatz → **Einstellungen** → **Gateway einrichten**.
   Mehrere Stellplätze an einem Pi: Portal → **Gateways** → **Ein Pi für mehrere Stellplätze** → Stellplätze ankreuzen.
   Hardware anschließen: je Stellplatz einen Arduino per USB, optional einen USB- oder PC/SC-Kartenleser.
2. Auf dem Pi die angezeigten Befehle ausführen. Bei selbst signiertem Plattform-Zertifikat (Standard auf der VM):
   ```bash
   curl -fsSk --pinnedpubkey 'sha256//<pin>' -o bike-ca.crt https://<plattform>/install/server.crt
   curl -fsSLO --cacert bike-ca.crt https://<plattform>/install/agent.sh
   echo '<prüfsumme>  agent.sh' | sha256sum -c -     # muss "OK" ausgeben
   sudo sh agent.sh --code XXXXX-XXXXX --ca-file bike-ca.crt
   ```
   Der erste Befehl lädt das Zertifikat nur, wenn dessen Schlüssel exakt zum Pin passt (sonst Fehler 90).
   Der Pin steht im Portal; zum Gegencheck zeigt das Portal auch den SHA-256-Fingerabdruck.
3. Nach etwa einer Minute steht das Gateway im Portal auf **online**. Am Ende zeigt das Skript die erkannte Hardware.
4. Später weitere Stellplätze an denselben Pi: neuen Code erzeugen und das Skript **erneut ausführen** – die
   vorhandenen Stellplätze bleiben, die neuen kommen dazu.

Optionen des Skripts: `--source simulator` (ohne Arduino testen), `--serial-port /dev/ttyUSB0` (nur bei einem Stellplatz; sonst automatisch), `--no-kiosk`,
`--ca-file ca.crt` (selbst signiertes Zertifikat der Plattform), `--name …`, `--no-systemd`,
`--prefix/--etc-dir/--state-dir`. Ohne `--code` wird der Code abgefragt oder aus `BIKE_ENROLL_CODE` gelesen.

Der Ein-Zeilen-Befehl (`curl … | sudo sh -s -- --code …`) ist bequemer, prüft das Skript aber nicht vorher.

## Ausgabe des Skripts

Das Skript zeigt einen Kopf mit Marke, nummerierte Schritte (`✓ [5/9] Agent 1.4.0 herunterladen und prüfen  SHA-256 … · 0,4 s`,
übersprungene mit `–`, Fehler mit `✗`) und am Ende eine Karte mit Stellplätzen, erkannter Hardware, Dienst-Status, lokaler
Anzeige und nächsten Schritten – Farben und Symbole wie im Portal, Zustände nie nur über Farbe.

- Ausgaben von apt, Download und Kopplung stehen im **Protokoll** `/var/log/bike-agent-install.log` (ohne systemd: im
  Temp-Verzeichnis). Bei einem Fehler zeigt ein roter Kasten Ursache, Hinweis und die letzten Protokollzeilen.
- Ohne Farben: `--no-color` oder `NO_COLOR=1`; ohne Terminal (z. B. in einer Pipe) und ohne UTF-8 schlicht in ASCII.

## Was das Skript tut

| Schritt | Details |
|---|---|
| Prüfungen | root, Python ≥ 3.11, HTTPS-URL (HTTP nur für `localhost` oder mit `--allow-http`) |
| Pakete | nur was die Hardware braucht: `python3-serial`, `ca-certificates`; bei PC/SC-Leser (ACS, SCM, Omnikey, Feitian) `pcscd python3-pyscard` + Sperre des Kernel-Treibers `pn533`; bei USB-Webcam ohne rpicam `fswebcam` |
| Benutzer | Systembenutzer `bike-agent` ohne Login, Gruppen `dialout` (Arduino), `input` (USB-Leser), `video` (Kamera) |
| Paket | `/install/agent.tar.gz` laden, **SHA-256 gegen den im Skript eingebauten Wert prüfen** |
| Ablage | `/opt/bike-agent/releases/<version>`, Symlink `/opt/bike-agent/current` |
| Kopplung | `agent.py enroll` – Code per Umgebungsvariable (nicht in der Prozessliste) |
| Zustand | `/var/lib/bike-agent/agent.json`, Rechte `0600`, enthält das Geräte-Token |
| Dienst | `bike-agent.service`, gehärtet (`NoNewPrivileges`, `ProtectSystem=strict`, keine Capabilities, nur tty-, input- und Kamera-Geräte); wird nach jeder Kopplung neu gestartet |
| Anzeige | Mit Desktop: Autostart-Eintrag für Chromium im Kiosk-Modus (`~/.config/autostart/bike-display.desktop` des aufrufenden Benutzers, bei Wayfire zusätzlich `wayfire.ini`) – abschaltbar mit `--no-kiosk` |
| Übersicht | `hardware.py`: erkannte Arduinos, Leser, Kamera |
| Hilfsbefehl | `bike-agent status` · `bike-agent rollback` · `bike-agent hardware` · `bike-agent doctor` (Diagnose: Kopplung aller Stellplätze, Arduinos, Leser, Kamera, Plattform/Zertifikat, Uhrzeit – ohne Nebenwirkung) |

Das Skript ist wiederholbar; mit einem neuen Code kommen Stellplätze dazu (gleiche Stellplätze werden neu gekoppelt).

## Hardware-Erkennung und Zuordnung (ab 1.4.0)

| Gerät | Erkennung | Zuordnung zum Stellplatz |
|---|---|---|
| Arduino (Uno/Nano, CH340, FTDI, CP210x) | `/dev/serial/by-id` (stabile Namen, auch nach Umstecken), Fallback `ttyACM*`/`ttyUSB*`; Firmware meldet sich mit `{"type":"hello","name":"bike-stall","fw":…,"nfc":…}` | in stabiler Reihenfolge, Arduinos mit erkannter Firmware zuerst; im Portal (Gateways) fest wählbar – ist der Port schon vergeben, werden die zwei Stellplätze getauscht |
| PN532 am Arduino | NFC-Zeilen bzw. `"nfc":true` im hello | gehört immer zum Stellplatz seines Arduinos |
| USB-Leser im Tastaturmodus | `/proc/bus/input/devices`: nur Geräte, deren Name nach Kartenleser aussieht (RFID, NFC, IC/ID-Reader …) oder bekannte IDs (`ffff:0035`, `08ff:0009`, `413d:2107`); eigene Namen über `hid_reader_names` in `agent.json`. **Normale Tastaturen werden nie übernommen.** Exklusiver Zugriff (EVIOCGRAB) | zuerst Stellplätze ohne eigenen PN532; im Portal wählbar |
| PC/SC-Leser (z. B. ACR122U) | `pcscd` + `pyscard`, UID per `FF CA 00 00 00`; eine liegende Karte zählt einmal | wie USB-Leser |
| Kamera | `rpicam-still`/`libcamera-still`, sonst `fswebcam` + `/dev/video*` | eine je Pi |

USB-Leser tippen die UID oft als Dezimalzahl. `hid_uid_format` in `agent.json`: `auto` (Standard: Hex bei A–F oder 8/14/20 Zeichen,
sonst 10-stellig dezimal mit umgekehrter Byte-Reihenfolge), `hex`, `dec`, `dec_rev`. Das Portal nutzt beim Anlegen einer Karte dieselbe Regel.
Die Erkennung läuft alle 30 s (Umstecken wird erkannt). **Identifizieren** im Portal lässt die LEDs des Stellplatzes 10 s blinken.

**Getestet** mit gefälschten `/dev`-/`/proc`-Daten, aufgezeichneten Tastenereignissen, einem simulierten PC/SC-Leser und
zwei simulierten Stellplätzen an einem Agent. **Nicht getestet:** echte USB-/PC-SC-Leser, mehrere echte Arduinos an einem Pi,
Chromium-Autostart auf echtem Pi.

## Display am Stellplatz (funktioniert auch offline)

Der Agent liefert ab Version 1.3.0 die Kiosk-Anzeige selbst aus: **http://127.0.0.1:8088/local** (nur lokal
erreichbar). Mit mehreren Stellplätzen zeigt `/local` eine Übersicht („2 von 3 Stellplätzen frei“), `/local?stall=<id>` einen Stellplatz. Solange die Plattform antwortet, zeigt sie deren Status (inkl. Reservierung, Öffnungszeiten,
Ein-/Auschecken). Ist die Plattform länger als 10 s nicht erreichbar, zeigt sie den Zustand **direkt vom Sensor**
mit dem Hinweis „OFFLINE – lokale Anzeige“. Auch dann gilt: keine gültige Messung seit 30 s oder Sensorfehler
→ STATUS UNBEKANNT, nie „frei“. Ein Anzeige-Token ist auf dem Pi nicht nötig.

Den Vollbildmodus richtet das Installationsskript ein (siehe oben). Von Hand:

```bash
chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://127.0.0.1:8088/local
```

Port ändern oder abschalten: `"local_display_port": 0` in `/var/lib/bike-agent/agent.json`, dann `sudo systemctl restart bike-agent`.
**Getestet:** Offline-Umschaltung, Zustandslogik und Auslieferung mit Tests und Simulator; **nicht getestet:** Chromium-Kiosk auf echtem Pi.

## Verwaltung im Portal

- **Gateways** (Navigation): je Pi eine Karte mit seinen Stellplätzen, Status, Version, erkannter Hardware;
  Arduino-Port und NFC-Leser je Stellplatz wählbar (automatisch oder fest), **Identifizieren**.
- **Lesegeräte**: alle NFC-Leser je Pi mit Stellplatz, Taps und Erfolgsquote (7 Tage) und **Karte anlernen**.
- **Zustand**: Arduino verbunden, simulierte Quelle, CPU-Temperatur, gepufferte Nachrichten,
  freier Speicher, Laufzeit, letzter Fehler. *Offline* nach 3 Minuten ohne Heartbeat.
- **Befehle** (werden beim nächsten Heartbeat ausgeführt): *Neu starten*, *Token erneuern*,
  *Aktualisieren*, *Identifizieren*. Es gibt bewusst **keine** Möglichkeit, beliebige Befehle auszuführen.
- **Sperren**: Token sofort ungültig, Gerät kann keine Daten mehr senden.
- **Updates automatisch einspielen** (je Station, Standard: an).
- **Konfiguration**: Der Agent meldet seine Konfigurationsversion; Änderungen gelangen mit dem nächsten Heartbeat zum Agenten. Jede Station ist genau ein Stellplatz – eine Platzzuordnung gibt es nicht mehr.

## Sicherheit

| Thema | Umsetzung |
|---|---|
| Kopplungscode | 10 Zeichen (~50 Bit), einmalig, 30 min gültig, nur gehasht gespeichert, Endpunkt rate-limitiert |
| Geräte-Token | 256 Bit, nur gehasht auf der Plattform, gilt nur für **eine** Station |
| Token-Rotation | automatisch alle 30 Tage und auf Befehl; altes Token bleibt 15 min gültig (keine Aussperrung bei Verbindungsabbruch) und wird ungültig, sobald das neue benutzt wurde |
| Transport | HTTPS mit Zertifikatsprüfung (eigene CA möglich); Downloads nur von der eigenen Plattform |
| Updates | Prüfsumme kommt über den authentifizierten Kanal; sicheres Entpacken (nur Dateien, keine Pfade/Links); Versionsprüfung; kein Downgrade |
| Rollback | startet eine neue Version 3-mal ohne erfolgreichen Heartbeat, schaltet der Agent auf die vorherige zurück; manuell: `bike-agent rollback` |
| Rechte | eigener Benutzer, schreibt nur in `/var/lib/bike-agent` und `/opt/bike-agent` |
| Paket | reproduzierbar gebaut (gleicher Inhalt → gleiche Prüfsumme), Prüfsummen unter `/install/agent.sha256` |

Bekannte Grenze: Die Prüfsumme sichert die Integrität gegenüber der Plattform ab. Eine zusätzliche
Signatur der Releases mit einem offline gehaltenen Schlüssel (z. B. Ed25519) wäre der nächste Schritt,
damit selbst eine kompromittierte Plattform keine Updates einschleusen kann.

## Neue Agent-Version ausrollen (Betreiber)

1. Änderungen in `pi-gateway/` vornehmen, Tests laufen lassen (`python3 -m pytest -q tests`).
2. Versionsnummer in `pi-gateway/VERSION` erhöhen.
3. Plattform neu starten – das Paket wird beim Start gebaut.
4. Agenten mit automatischen Updates aktualisieren sich beim nächsten Heartbeat; andere zeigen
   „Update verfügbar“ und lassen sich per Knopfdruck aktualisieren.

## Fehlersuche

| Symptom | Prüfen |
|---|---|
| Gateway bleibt offline | zuerst `sudo bike-agent doctor`; dann `systemctl status bike-agent`, `journalctl -u bike-agent -f`; Netz/Firewall zur Plattform (Port 443) |
| „Arduino ✗“ | USB-Kabel, `ls /dev/ttyACM* /dev/ttyUSB*`, Port in `bike-agent status`; neu koppeln mit `--serial-port` |
| „Token abgelehnt“ | Gerät im Portal gesperrt? Neu koppeln: neuen Code erzeugen, `sudo sh agent.sh --code …` |
| `curl: (60) … self-signed certificate` | Alten Befehl ohne Zertifikat benutzt → die Befehle aus dem Portal (mit `--pinnedpubkey`) verwenden |
| `curl: (60) … no alternative certificate subject name matches` bzw. Python `IP address mismatch` | Zertifikat enthält die IP nicht → auf der VM `sudo deploy/make-cert.sh --force 192.168.0.114 && sudo systemctl restart bike-api`, dann neue Befehle aus dem Portal |
| `curl: (90) public key does not match pinned public key` | Pin passt nicht (falscher Server oder Zertifikat erneuert) → Befehle im Portal neu anzeigen lassen |
| Kopplung schlägt fehl | Code abgelaufen/benutzt → neuen Code erzeugen; Uhrzeit des Pi ist unkritisch |
| Update hängt | `bike-agent rollback`, dann `sudo systemctl restart bike-agent` |

## Entwicklung ohne Pi

`scripts/dev.sh` startet Plattform + Demo-Kunde und koppelt einen lokalen Agenten mit Simulator.
Das Gerät erscheint im Portal unter *Gateways*; Befehle und Updates lassen sich dort ausprobieren.
`scripts/dev.sh --interactive` nutzt stattdessen den tastaturgesteuerten Simulator.
