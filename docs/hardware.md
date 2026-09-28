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
| NFC-Leser (Vorschlag: PN532-Modul, 13,56 MHz) | | 3,3–5 V | I2C | | |
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
| | | | | | |

Beim Arduino Uno sind D0/D1 durch USB-Seriell belegt – nicht verwenden.

## NFC-Leser PN532 (Ein-/Auschecken)

> Software (Sketch, Gateway, Plattform) ist fertig und mit dem Simulator getestet.
> **Mit echtem PN532 noch nicht getestet, Sketch noch nicht kompiliert.**

1. Bibliothek installieren: Arduino IDE → Bibliotheksverwalter → „Adafruit PN532“ (inkl. „Adafruit BusIO“).
2. Am Modul die DIP-Schalter auf **I2C** stellen (meist SEL0 = ON, SEL1 = OFF – Aufdruck prüfen).
3. Verdrahtung (Arduino Uno): VCC → 5 V (bzw. 3,3 V je nach Modul), GND → GND, SDA → A4, SCL → A5,
   IRQ → D7, RSTO → D8 (Platzhalter `PN532_IRQ_PIN`/`PN532_RESET_PIN` im Sketch; nicht mit Präsenz-/LED-Pins doppelt belegen).
4. Im Sketch `#define NFC_ENABLED` einkommentieren und hochladen. Beim Start meldet der Arduino
   `{"type":"info","nfc":"ok"}` oder `"missing"` (im Log des Gateways sichtbar).
5. Test: Karte an den Leser halten → Arduino sendet `{"type":"nfc","uid":"…"}` → Portal → **Karten**:
   Karte erscheint als „wartet auf Freigabe“ → benennen und freigeben → erneut halten = eingecheckt.

Die LEDs zeigen kurz die Antwort (grün = ein-/ausgecheckt, rot = abgelehnt). Ohne Leser bleibt der Sketch wie bisher.
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
| 3 | NFC-Leser (PN532, Software fertig, Hardware ungetestet) | außen rechts vorne, ca. 100 cm hoch, hinter max. 3 mm Kunststoff, nicht hinter Metall |
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
