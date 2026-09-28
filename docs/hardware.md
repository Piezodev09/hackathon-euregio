# Hardware und Aufbau

> **Vor der Verdrahtung ausfüllen (Plan 4.2).** Die Pins im Sketch sind Platzhalter.
> 5-V-Signale nie ungeprüft an 3,3-V-GPIOs des Pi anschließen – deshalb verbindet
> USB-Seriell Arduino und Pi, nicht GPIO.

## Inventur

| Bauteil | Modell | Betriebsspannung | Signalart | Stück | verantwortlich |
|---|---|---|---|---|---|
| Arduino (+ USB-Kabel) | | | | | |
| Raspberry Pi (+ Netzteil, SD) | | | | | |
| Präsenzsensor | | | digital / Ultraschall | | |
| Vibrationssensor | | | digital (Ein/Aus) / analog | | |
| LEDs (+ Vorwiderstände) | | | | | |
| Steckbrett / Klemmen / Leitungen | | | | | |
| Monitor / Tablet für Anzeige | | | | | |
| Zugang Proxmox-Host | | | | | |
| Netzwerkanschluss (Kabel/WLAN) | | | | | |
| Messgerät / USB-Leistungsmesser | | | | | |

## Sensorwahl im Sketch

In `arduino/smart_bike_station/smart_bike_station.ino`:

- `PRESENCE_TYPE`: `PRESENCE_ULTRASONIC` (z. B. HC-SR04, Schwelle `OCCUPIED_BELOW_CM`)
  oder `PRESENCE_DIGITAL` (IR-Hindernissensor/Kontakt, `DIGITAL_ACTIVE_LOW`).
- `VIB_TYPE`: `VIB_DIGITAL` (z. B. SW-420, nur Impulse → `VIB_PULSE_SCALE`) oder
  `VIB_ANALOG` (Piezo am Analogeingang, `VIB_ANALOG_NOISE`).
- Liefert der Vibrationssensor nur Ein/Aus, basieren die Merkmale auf der Impulszahl.
  `anomaly.peak_threshold` in `backend/config.toml` entsprechend kalibrieren.

## Pinbelegung (geprüft eintragen)

| Platz | Präsenz (Echo/Signal) | Trigger | Vibration | LED grün | LED rot |
|---|---|---|---|---|---|
| A | | | | | |
| B | | | | | |
| C | | | | | |

Beim Arduino Uno sind D0/D1 durch USB-Seriell belegt – nicht verwenden.

## Lokale Anzeige am Platz

| Zustand | LED grün | LED rot | Beschriftung am Platz |
|---|---|---|---|
| frei | an | aus | „frei / vrij / available“ + Symbol |
| belegt | aus | an | „belegt / bezet / occupied“ |
| unbekannt | aus | blinkt | „Störung – bitte Anzeige beachten“ |

Farbe nie allein: Beschriftung am Platz anbringen (Plan 9.2).

## Kalibrierung

1. Leerer Platz, Demo-Objekt, echtes Fahrrad: Abstandswerte notieren → `OCCUPIED_BELOW_CM`.
2. Sonnenlicht/Position prüfen (IR), Fahrradformen (Rahmen, Reifen) prüfen.
3. Vibration: normales Einstellen, Anstoßen, Nachbarplatz, Rütteln – Werte im Log
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
