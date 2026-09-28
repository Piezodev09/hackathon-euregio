# Architektur

Ein vorne offener Einzelstellplatz. Konkreter Vorschlag – an die tatsächlich vorhandenen Geräte anpassen.
Der visuelle Entwurf (Stellplatz-Skizzen, Dashboard, Diagramm) liegt in `design/`.

## Plattformen und Aufgaben

| Plattform | Aufgabe | Code | Stand |
|---|---|---|---|
| Sensoren | Präsenz (quer über den Stellplatz), Erschütterung (an der Radhalteschiene), NFC-Leser PN532 seitlich (Ein-/Auschecken) | – | geplant, nicht verdrahtet |
| Kamera (optional) | Einzelbilder am Pi (Kameramodul oder USB), nur nach Freigabe, standardmäßig aus | `pi-gateway/camera.py` | Code vorhanden, nur mit Testbild geprüft |
| Arduino | Sensoren lesen, Belegung 2 s entprellen, LEDs setzen, JSON-Zeilen über USB-Seriell senden (bei Änderung, bei Erschütterung max. alle 0,5 s, Heartbeat alle 10 s); optional NFC-Karten lesen (`NFC_ENABLED`) | `arduino/smart_bicycle_box/` | Code vorhanden, nicht auf Hardware getestet |
| Raspberry Pi | Zeilen prüfen, Sequenznummer vergeben, bis zu 500 Nachrichten puffern, per HTTPS (angeheftetes Zertifikat) senden; schweigt der Arduino > 15 s, Stellplatz als Sensorfehler melden; NFC-Taps sofort senden (max. 60 s Wiederholung) und Antwort an den Arduino; Kamerabild bei Warnung; Display lokal (Kiosk-Anzeige) | `pi-gateway/` | Code vorhanden, nicht auf echtem Pi getestet |
| Edge-VM (Proxmox) | Eingang aus dem Schulnetz, TLS-Terminierung/Weiterleitung zur App-VM | `deploy/` (Beispiele) | VM eingerichtet, Dienst nicht installiert |
| App-VM (Proxmox, abgeschirmt) | FastAPI: Messungen annehmen, Zustand ableiten, Regel + KI auswerten, Parkvorgänge und Gebühren, Lizenzabrechnung, Kamerabilder mit Löschfrist, SQLite, Portal ausliefern | `backend/`, `web/` | VM eingerichtet, Anwendung nicht installiert |
| Browser | Portal, Kiosk-Anzeige, Stellplatz-Ansicht (QR) | `web/` | mit Simulator geprüft |

## Datenfluss

```
Fahrrad einstellen
  -> Präsenzsensor misst quer über den Stellplatz
  -> Arduino entprellt (2 s) und meldet {"presence","vibration","seq","state"}
  -> Pi prüft Format/Plausibilität, vergibt Sequenznummer
       +-> API nicht erreichbar? -> puffern (begrenzt), später mit age_ms nachsenden
  -> API prüft Geräte-Token + Eingaben, setzt Server-Zeitstempel
       +-> SQLite: measurement (+ event bei Sensorfehler/Warnung)
       +-> belegt + Erschütterung: Merkmale berechnen -> Regel und KI bewerten
  -> Portal und Display fragen alle 2 s den Status ab

Karte an den NFC-Leser halten
  -> Arduino meldet {"type":"nfc","uid":…} (gleiche Karte max. alle 3 s)
  -> Pi sendet sofort POST /nfc/tap (bei Netzproblem bis 60 s wiederholen, dann verwerfen)
  -> API: Karte per HMAC finden -> einchecken / auschecken + Gebühr nach Tarif / ablehnen
  -> Antwort "NFC checked_in" usw. zurück an den Arduino (LED), Display zeigt kurz das Ergebnis

Erschütterungswarnung bei freigegebener Kamera
  -> Antwort auf /measurements/batch enthält capture=true
  -> Pi nimmt ein Einzelbild auf und lädt es hoch (JPEG ≤ 2 MB) -> Löschung nach Frist
```

## Zustände

```
[FREI] <-- Fahrrad erkannt / entfernt --> [BELEGT]
  | Messung unplausibel / Sensorausfall / keine Daten seit 30 s / Verbindungsabbruch
  v
[STATUS UNBEKANNT] -- gültige Messungen --> neuer Zustand
[BELEGT] -- auffällige Erschütterung --> Warnereignis (Zustand bleibt BELEGT)
```

Eine Warnung ist kein Belegungszustand. STATUS UNBEKANNT wird an vier Stellen erzwungen:

1. **Arduino**: nach dem Start und nach 5 ungültigen Messungen in Folge `presence=-1`.
2. **Gateway**: kommt vom Arduino nichts mehr, meldet es `sensor_state="error"`.
3. **API**: letzte Meldung älter als `stale_after_s` (30 s) oder in der Zukunft → `unknown/stale`.
4. **Browser**: letzte erfolgreiche Antwort älter als 30 s → `unknown/connection`.

## Zeitwerte (Planungsannahmen, in Konfiguration)

| Wert | Default | Ort |
|---|---|---|
| Entprellung | 2 s | `STABLE_MS` im Sketch |
| Heartbeat | 10 s | `HEARTBEAT_MS` im Sketch, `timing.heartbeat_s` im Gateway |
| Arduino-Timeout | 15 s | `timing.arduino_timeout_s` im Gateway |
| „unbekannt/veraltet“ | 30 s | `timing.stale_after_s` in `backend/config.toml` |
| Abfrage Portal/Display | 2 s | `timing.ui_poll_interval_s` |
| Schonzeit nach Belegungswechsel | 15 s | `anomaly.grace_period_s` |

## Warum so einfach?

Kein Message-Broker, kein Kubernetes, eine Datenbankdatei, Polling statt WebSockets: weniger
Integrations- und Fehleraufwand für einen Demo-Stellplatz. Das Gateway nutzt nur die
Python-Standardbibliothek + pyserial.
