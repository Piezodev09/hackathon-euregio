# Hardware und Aufbau

> **Vor der Verdrahtung ausfüllen (Plan 4.2).** Die Pins im Sketch sind Platzhalter.
> 5-V-Signale nie ungeprüft an 3,3-V-GPIOs des Pi anschließen – deshalb verbindet
> USB-Seriell Arduino und Pi, nicht GPIO.

## Inventur

| Bauteil | Modell | Betriebsspannung | Signalart | Stück | verantwortlich |
|---|---|---|---|---|---|
| Arduino (+ USB-Kabel) | | | | | |
| Raspberry Pi (+ Netzteil, SD) | | | | | |
| Präsenzsensor (Vorschlag: ToF-Distanzsensor, Budget: Ultraschall) | | | I2C / Ultraschall / digital | | |
| Erschütterungssensor | | | digital (Ein/Aus) / analog | | |
| NFC-Leser (RC522/MFRC522, 13,56 MHz) | RC522 | 3,3 V | SPI (D9–D13) | | |
| Kamera (optional, nur nach Freigabe): Pi-Kameramodul (CSI) oder USB-Webcam | | | CSI / USB am Pi | | |
| Display für den Kopfträger (Vorschlag 13–15,6″) | | | HDMI am Pi | | |
| LEDs (+ Vorwiderstände) | | | | | |
| Steckbrett / Klemmen / Leitungen | | | | | |
| Monitor / Tablet für Anzeige | | | | | |
| Zugang Proxmox-Host | | | | | |
| Netzwerkanschluss (Kabel/WLAN) | | | | | |
| Messgerät / USB-Leistungsmesser | | | | | |

## Sensorwahl im Sketch

In `arduino/smart_bicycle_box/smart_bicycle_box.ino`:

- `PRESENCE_TYPE`: `PRESENCE_ULTRASONIC` (z. B. HC-SR04, Schwelle `OCCUPIED_BELOW_CM`)
  oder `PRESENCE_DIGITAL` (IR-Hindernissensor/Kontakt, `DIGITAL_ACTIVE_LOW`).
- `VIB_TYPE`: `VIB_DIGITAL` (z. B. SW-420, nur Impulse → `VIB_PULSE_SCALE`) oder
  `VIB_ANALOG` (Piezo am Analogeingang, `VIB_ANALOG_NOISE`).
- Liefert der Vibrationssensor nur Ein/Aus, basieren die Merkmale auf der Impulszahl.
  `anomaly.peak_threshold` in `backend/config.toml` entsprechend kalibrieren.

## Pinbelegung (geprüft eintragen)

| Präsenz (Echo/Signal) | Trigger | Erschütterung | LED grün | LED rot | Netz-LED |
|---|---|---|---|---|---|
| D5 | D6 | D4 | **D3** | **D2** | – |

Standard-Pinbelegung im Sketch (Aufbau station1). **D9–D13 sind für den RC522 (SPI) reserviert** und dürfen nicht
doppelt belegt werden. Beim Arduino Uno sind D0/D1 durch USB-Seriell belegt – nicht verwenden. Präsenz/Trigger/
Erschütterung sind optional (ohne Sensor meldet die Station „Status unbekannt“).

## NFC-Leser RC522 (Ein-/Auschecken)

> Getestet an station1 mit Arduino Nano 33 IoT und echtem RC522 (VersionReg 0x92): Lesen, Tap und
> „wartet auf Freigabe“ im Portal funktionieren. Der Sketch nutzt die Bibliothek **MFRC522** (SPI).

Der RC522 (MFRC522) hängt am **SPI** (nicht I²C):

| RC522 | Nano 33 IoT | Sketch |
|---|---|---|
| SDA/SS | D10 | `RC522_SS_PIN` |
| SCK | D13 | (SPI fest) |
| MOSI | D11 | (SPI fest) |
| MISO | D12 | (SPI fest) |
| RST | D9 | `RC522_RST_PIN` |
| 3.3V | 3V3 | – |
| GND | GND | – |

1. **Firmware flashen – installiert Toolchain (arduino-cli), Board-Core und die MFRC522-Bibliothek automatisch:**
   ```sh
   bash arduino/flash.sh                       # Standard-Board Arduino Nano 33 IoT
   FQBN=arduino:avr:uno bash arduino/flash.sh   # anderes Board
   ```
2. Beim Start meldet der Arduino `{"type":"info","nfc":"ok"}` bzw. `"missing"` (im Gateway-Log sichtbar).
3. Test: Karte an den Leser halten → Arduino sendet `{"type":"nfc","uid":"…"}` → Portal → **Karten**:
   Karte erscheint als „wartet auf Freigabe“ → benennen und freigeben → erneut halten = eingecheckt.

Die LEDs zeigen kurz die Antwort (**grün D3** = ein-/ausgecheckt, **rot D2** = abgelehnt). Ohne Leser bleibt der Sketch
lauffähig (`nfc:"missing"`). Ein 5-V-RC522 funktioniert am 3,3-V-Nano nur, wenn das Modul 3,3 V verträgt (die meisten tun das).
Einfacher als „wartet auf Freigabe“: Portal → **Lesegeräte** → **Karte anlernen** (Bezeichnung eingeben, Karte innerhalb 60 s an einen Leser halten).

### Andere Kartenleser (ohne Löten, am Pi)

- **USB-Leser im Tastaturmodus** (13,56 MHz/125 kHz, „tippt“ die Nummer): einfach am Pi einstecken. Der Agent erkennt ihn
  am Namen, liest ihn exklusiv und rechnet die Dezimalnummer in die Hex-UID um (siehe [agent.md](agent.md)).
- **PC/SC-Leser** (z. B. ACR122U): einstecken, Installationsskript (erneut) ausführen – es installiert `pcscd` nur dann.
- Mehrere Stellplätze an einem Pi: Leser werden in Reihenfolge zugeordnet, im Portal (Gateways) änderbar.
- **Nicht mit echten Geräten getestet** (nur mit aufgezeichneten Eingaben bzw. simuliertem Leser).

### Sketch 0.4.0

Erste Zeile nach dem Verbinden: `{"type":"hello","name":"bike-stall","fw":"0.4.0","nfc":true|false}` – daran erkennt der Pi
den Arduino und ob er einen NFC-Leser hat. Befehl `IDENT` vom Pi lässt beide LEDs 10 s abwechselnd blinken
(Portal → Gateways → **Identifizieren**). **Sketch nicht kompiliert und nicht auf Hardware getestet.**
Zum Testen ohne Hardware: `scripts/dev.sh --interactive`, dann `n` (Demo-Karte) oder `n 04AABBCCDD`.

## Kamera am Raspberry Pi (optional, opt-in)

> Ein Arduino Uno kann keine Kamera sinnvoll betreiben – die Kamera hängt am Pi.
> **Mit echter Kamera noch nicht getestet**; getestet sind Erkennung, Upload und Portal mit dem Testbild des Simulators.

- Pi-Kameramodul (CSI): Raspberry Pi OS Bookworm bringt `rpicam-still` mit (ältere: `libcamera-still`).
- USB-Webcam: `sudo apt install fswebcam` (alternativ `ffmpeg`), Gerät `/dev/video0`.
- Der Agent erkennt die Kamera selbst (`sudo bike-agent doctor` zeigt sie an) und meldet sie im Heartbeat.
- Aufnahmen gibt es **nur**, wenn im Portal (Stellplatz → Einstellungen → Kamera) eingeschaltet und die
  Freigabe eingetragen ist: bei einer Erschütterungswarnung oder per „Testbild aufnehmen“. Höchstens ein Bild je 10 s,
  keine Videos, keine Speicherung auf dem Pi, Löschung nach 1–72 h.
- Hinweisschild am Stellplatz anbringen; Display und Stellplatz-Ansicht zeigen „Kamera aktiv“.

## Aufbau des Stellplatzes (Vorschlag – Maße erst nach dem Messen festlegen)

Skizzen: `design/smart-bicycle-box-prototyp.html`, Abschnitt „Stellplatz-Konzept“.

| Nr. | Teil | Position (Vorschlag) |
|---|---|---|
| – | Rahmen | zwei Seitenwände + Rückwand, vorne offen; ca. 80 × 200 × 150 cm (B × T × H), lichte Höhe ca. 125 cm |
| 1 | Display | im Kopfträger oben vorne, mittig; über HDMI am Pi (Kiosk-Anzeige) |
| 2 | Präsenzsensor | innen an der linken Wand, ca. 40 cm hoch, misst quer zur gegenüberliegenden Wand |
| 3 | NFC-Leser (RC522/SPI, getestet an station1) | außen rechts vorne, ca. 100 cm hoch, hinter max. 3 mm Kunststoff, nicht hinter Metall |
| 4 | Erschütterungssensor | an der Radhalteschiene |
| 5 | Elektronikgehäuse IP54 | außen rechts hinten oben: Arduino, Pi, geprüftes Netzteil |
| 6 | Kabelkanal | Oberkante rechte Wand → Kopfträger → Innenkante linke Wand; Bodenkabel unter der Schiene |
| 7 | Radhalteschiene | an der Rückwand, sorgt für definierte Position |
| 8 | Kamera (optional) | im Kopfträger, **nach innen** auf den Stellplatz gerichtet; nur nach Freigabe durch Schulleitung und Datenschutz, im Portal standardmäßig aus |

## Lokale Anzeige

| Zustand | Display | LED grün | LED rot |
|---|---|---|---|
| frei | „FREI“ + Häkchen, heller grüner Grund | an | aus |
| belegt | „BELEGT“ + Fahrrad, dunkelroter Grund | aus | an |
| unbekannt | „STATUS UNBEKANNT“ + Fragezeichen, grau schraffiert | aus | blinkt |

Farbe nie allein: Das Display zeigt immer Wort und Symbol. LEDs sind nur eine Zusatzanzeige.

## Kalibrierung

1. Leerer Stellplatz, Demo-Objekt, echte Fahrräder (dünne Rahmen, Carbon, Kinderrad): Abstandswerte notieren → `OCCUPIED_BELOW_CM` (Vorschlag 60 cm bei ca. 75 cm Innenbreite).
2. Sonnenlicht/Position prüfen (IR), Fahrradformen (Rahmen, Reifen) prüfen.
3. Erschütterung: normales Einstellen, Anstoßen, Anlehnen, Wind, Rütteln – Werte im Log
   (`journalctl -u bike-gateway -f` bzw. Seriellmonitor) → `peak_threshold`, `min_peaks`.

## Mechanik

Kabel gegen Stolpern sichern (Kabelkanal), Sensoren so befestigen, dass normales Ein- und
Ausparken möglich bleibt, keine freiliegenden Kontakte, Stromversorgung vor Publikumsbetrieb prüfen.

## Energie (Plan 11)

| Gerät | gemessen (W) | Datenblatt (W) | Bemerkung |
|---|---|---|---|
| Arduino + Sensoren + LEDs | | | |
| Raspberry Pi | | | |
| Anteil VM/Proxmox-Host | | | geschätzt, Unsicherheit angeben |

LED-Helligkeit über `LED_BRIGHTNESS` (PWM-Pins) reduzierbar. Keine pauschale CO₂-Einsparung behaupten.
