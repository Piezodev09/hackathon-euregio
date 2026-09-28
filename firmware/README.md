# Firmware and hardware

`smart_bike_station/smart_bike_station.ino` runs on the Arduino of every station. It reads one
presence sensor and one vibration sensor per bike space, debounces the occupancy, drives two LEDs
per space and sends JSON lines over USB serial to the Raspberry Pi (agent).

> **Fill in before wiring (plan 4.2).** The pins in the sketch are placeholders.
> Never connect 5 V signals unchecked to the 3.3 V GPIOs of the Pi – that is why Arduino and Pi
> talk over USB serial, not GPIO. **Check pins and voltages on site** against the data sheets of
> the modules you actually have.

## Bill of materials (starter kit for 3 spaces)

| Part | Example | Qty | Note |
|---|---|---|---|
| Arduino | Uno R3 / Nano (5 V) + USB cable | 1 | USB also powers the Arduino |
| Raspberry Pi | Pi 4/5 (or Pi 3B+), power supply, microSD ≥ 16 GB | 1 | Raspberry Pi OS Bookworm |
| Presence sensor | HC-SR04 ultrasonic (5 V) – alternatively IR obstacle sensor | 3 | one per space |
| Vibration sensor | SW-420 module (digital) – alternatively piezo on an analog input | 3 | one per space |
| LEDs | green + red, 5 mm, with series resistors (220–330 Ω) | 3 + 3 | never colour alone: label the space |
| Wiring | breadboard / screw terminals, jumper wires, cable duct | – | secure cables against tripping |
| Optional | USB power meter, printed labels "free / occupied / fault" | – | energy measurement, accessibility |

A price estimate for the kit is on the landing page and is explicitly marked as an estimate.

## Inventory (fill in)

| Part | Model | Supply voltage | Signal type | Qty | Responsible |
|---|---|---|---|---|---|
| Arduino (+ USB cable) | | | | | |
| Raspberry Pi (+ power supply, SD) | | | | | |
| Presence sensor | | | digital / ultrasonic | | |
| Vibration sensor | | | digital (on/off) / analog | | |
| LEDs (+ series resistors) | | | | | |
| Breadboard / terminals / wires | | | | | |
| Monitor / tablet for the kiosk display | | | | | |
| Access to the Proxmox host | | | | | |
| Network connection (cable/Wi-Fi) | | | | | |
| Multimeter / USB power meter | | | | | |

## Sensor selection in the sketch

- `PRESENCE_TYPE`: `PRESENCE_ULTRASONIC` (e.g. HC-SR04, threshold `OCCUPIED_BELOW_CM`)
  or `PRESENCE_DIGITAL` (IR obstacle sensor / contact, `DIGITAL_ACTIVE_LOW`).
  **Prefer ultrasonic:** no echo (broken wire, dead sensor, out of range) is reported as `presence=-1`
  → "unknown". A digital input cannot tell a broken wire from "no obstacle" – the pull-up then reads
  HIGH, which an active-low sensor reports as **free**. If you must use a digital sensor, choose one
  (or wire a contact) that is *active-high* (`DIGITAL_ACTIVE_LOW = false`), so a broken wire reads
  "occupied" – never falsely free – and test it by pulling the signal wire (T07).
- `VIB_TYPE`: `VIB_DIGITAL` (e.g. SW-420, pulses only → `VIB_PULSE_SCALE`) or
  `VIB_ANALOG` (piezo on an analog input, `VIB_ANALOG_NOISE`).
- If the vibration sensor only delivers on/off, the features are based on the pulse count.
  Calibrate `anomaly.peak_threshold` in `server/config.toml` accordingly.

## Wiring (placeholders – verify on site)

| Space | Presence (echo/signal) | Trigger | Vibration | LED green | LED red |
|---|---|---|---|---|---|
| A | D2 | D3 | D4 | D5 | D6 |
| B | D7 | D8 | D9 | D10 | D11 |
| C | D12 | D13 | A0 | A1 | A2 |

One space (A) in detail – spaces B and C are wired the same way with their pins from the table:

```
Arduino Uno (5 V logic)                     modules of space A
  5V  ──────────────┬──────────────────────  HC-SR04 VCC
                    └──────────────────────  SW-420  VCC   (module accepts 3.3–5 V – check yours)
  D3  ─────────────────────────────────────  HC-SR04 Trig
  D2  ─────────────────────────────────────  HC-SR04 Echo  (5 V signal: fine for the Arduino,
                                                            never directly to a Pi GPIO)
  D4  ─────────────────────────────────────  SW-420  DO
  D5  ──[ 220–330 Ω ]──▶|── GND                green LED  "free"
  D6  ──[ 220–330 Ω ]──▶|── GND                red LED    "occupied" / blinking = fault
  GND ─────────────────────────────────────  HC-SR04 GND, SW-420 GND (common ground)
  USB ─────────────────────────────────────  Raspberry Pi (data + 5 V supply)
```

Rough power budget from typical data-sheet values (**estimate – measure with the USB power meter**):
Arduino ≈ 50 mA, per space HC-SR04 ≈ 15 mA + SW-420 ≈ 5–15 mA + one LED ≈ 10–15 mA → 3 spaces
≈ 150–200 mA in total, well below the 500 mA a Pi USB port provides. More spaces or brighter LEDs:
use a separate 5 V supply with common ground.

Typical module pins (check your modules!): HC-SR04 `VCC 5V · Trig · Echo · GND`; SW-420
`VCC 3.3–5V · DO · GND`. On the Arduino Uno D0/D1 are used by USB serial – do not use them.
D13 drives the on-board LED on many boards; if the trigger misbehaves, move it to another pin.

## Local display at the space

| State | LED green | LED red | Label at the space |
|---|---|---|---|
| free | on | off | "free / frei / vrij" + symbol |
| occupied | off | on | "occupied / belegt / bezet" |
| unknown | off | blinking | "fault – see display" |

Never colour alone: attach a printed label at the space (plan 9.2).

## Build check without hardware

```bash
sudo apt install gcc-avr avr-libc arduino-core-avr simavr   # Debian/Ubuntu
firmware/check.sh
```

Builds the sketch for an Arduino Uno (ATmega328P) in all four sensor configurations with
`-Wall -Wextra -Werror` (≈ 5.5 KB flash = 17 %, 318 B RAM = 16 %), runs it in the **simavr** simulator
without sensors and feeds the real serial output through the agent's parser: a hello line, then every
space as `"state":"error"` → *unknown*. This checks the protocol end to end, not the sensors, pins or
voltages – those still have to be verified on site. With the Arduino IDE: open
`smart_bike_station/smart_bike_station.ino`, board *Arduino Uno*, *Verify*; or
`arduino-cli compile --fqbn arduino:avr:uno firmware/smart_bike_station`.

## Serial protocol

Arduino → Pi, one JSON object per line, 115200 baud:

```json
{"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}
```

`presence`: 1 occupied, 0 free, -1 no valid reading (→ "unknown"). `vibration`: 0..1023.
Pi → Arduino: `NET 1` / `NET 0` (network status LED, optional `NET_LED_PIN`).
The slot ids (`A`, `B`, `C`) must match the slot keys of the station in the portal.

## Calibration

1. Empty space, demo object, real bike: note the distances → `OCCUPIED_BELOW_CM`.
2. Check sunlight/position (IR) and bike shapes (frame, wheel).
3. Vibration: normal parking, a bump, the neighbouring space, shaking – read the values in the
   log (`journalctl -u bike-agent -f` or the serial monitor) → `peak_threshold`, `min_peaks`.

## Mechanics

Secure cables against tripping (cable duct), mount the sensors so that normal parking and leaving
remain possible, no exposed contacts, check the power supply before public operation.

## Energy (plan 11)

| Device | measured (W) | data sheet (W) | Note |
|---|---|---|---|
| Arduino + sensors + LEDs | | | |
| Raspberry Pi | | | |
| Share of the Proxmox host | | | estimated, state the uncertainty |

The LED brightness can be reduced with `LED_BRIGHTNESS` (PWM pins). Do not claim a blanket CO₂ saving.
