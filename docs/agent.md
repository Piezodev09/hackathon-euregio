# Agent für Raspberry Pi – Installation, Kopplung, Betrieb

Der **Agent** läuft auf dem Raspberry Pi jeder Station. Er liest den Arduino über USB aus, überträgt
die Messungen verschlüsselt an die Plattform und wird zentral aus dem Portal verwaltet.

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

1. Portal → **Stellplätze** → Stellplatz → **Einstellungen** → **Gateway einrichten**.
2. Auf dem Pi die angezeigten Befehle ausführen. Bei selbst signiertem Plattform-Zertifikat (Standard auf der VM):
   ```bash
   curl -fsSk --pinnedpubkey 'sha256//<pin>' -o bike-ca.crt https://<plattform>/install/server.crt
   curl -fsSLO --cacert bike-ca.crt https://<plattform>/install/agent.sh
   echo '<prüfsumme>  agent.sh' | sha256sum -c -     # muss "OK" ausgeben
   sudo sh agent.sh --code XXXXX-XXXXX --ca-file bike-ca.crt
   ```
   Der erste Befehl lädt das Zertifikat nur, wenn dessen Schlüssel exakt zum Pin passt (sonst Fehler 90).
   Der Pin steht im Portal; zum Gegencheck zeigt das Portal auch den SHA-256-Fingerabdruck.
3. Nach etwa einer Minute steht das Gateway im Portal auf **online**.

Optionen des Skripts: `--source simulator` (ohne Arduino testen), `--serial-port /dev/ttyUSB0`,
`--ca-file ca.crt` (selbst signiertes Zertifikat der Plattform), `--name …`, `--no-systemd`,
`--prefix/--etc-dir/--state-dir`. Ohne `--code` wird der Code abgefragt oder aus `BIKE_ENROLL_CODE` gelesen.

Der Ein-Zeilen-Befehl (`curl … | sudo sh -s -- --code …`) ist bequemer, prüft das Skript aber nicht vorher.

## Was das Skript tut

| Schritt | Details |
|---|---|
| Prüfungen | root, Python ≥ 3.11, HTTPS-URL (HTTP nur für `localhost` oder mit `--allow-http`) |
| Pakete | `python3-serial`, `ca-certificates` (apt) |
| Benutzer | Systembenutzer `bike-agent` ohne Login, Gruppe `dialout` (USB-Seriell) |
| Paket | `/install/agent.tar.gz` laden, **SHA-256 gegen den im Skript eingebauten Wert prüfen** |
| Ablage | `/opt/bike-agent/releases/<version>`, Symlink `/opt/bike-agent/current` |
| Kopplung | `agent.py enroll` – Code per Umgebungsvariable (nicht in der Prozessliste) |
| Zustand | `/var/lib/bike-agent/agent.json`, Rechte `0600`, enthält das Geräte-Token |
| Dienst | `bike-agent.service`, gehärtet (`NoNewPrivileges`, `ProtectSystem=strict`, keine Capabilities, nur tty-Geräte) |
| Hilfsbefehl | `bike-agent status` · `bike-agent rollback` |

Das Skript ist wiederholbar; eine Neuinstallation mit neuem Code koppelt das Gerät neu.

## Verwaltung im Portal

- **Gateways** (Navigation): alle Geräte der Organisation mit Status, Version, Zustand.
- **Zustand**: Arduino verbunden, simulierte Quelle, CPU-Temperatur, gepufferte Nachrichten,
  freier Speicher, Laufzeit, letzter Fehler. *Offline* nach 3 Minuten ohne Heartbeat.
- **Befehle** (werden beim nächsten Heartbeat ausgeführt): *Neu starten*, *Token erneuern*,
  *Aktualisieren*. Es gibt bewusst **keine** Möglichkeit, beliebige Befehle auszuführen.
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
| Gateway bleibt offline | `systemctl status bike-agent`, `journalctl -u bike-agent -f`; Netz/Firewall zur Plattform (Port 443) |
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
