/*
 * Smarte Radstation – Arduino-Firmware (Plan 3.1, 5)
 *
 * Aufgaben: Sensoren lesen, Belegung entprellen, lokale LEDs setzen und
 * Messungen als JSON-Zeilen über USB-Seriell an den Raspberry Pi senden.
 * Keine Nutzerdaten, keine KI auf dem Arduino.
 *
 * Ausgabe je Zeile (115200 Baud):
 *   {"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}
 *   presence: 1 belegt, 0 frei, -1 kein gültiger Messwert (-> "unbekannt")
 *   vibration: 0..1023 (Spitzenwert bzw. skalierte Impulszahl im letzten Intervall)
 *
 * Eingabe vom Pi:  "NET 1" / "NET 0"  -> Netzstatus-LED
 *
 * !!! ACHTUNG – VOR DER VERDRAHTUNG PRÜFEN (Plan 4.1) !!!
 *   Alle Pinnummern, Sensortypen und Schwellwerte unten sind PLATZHALTER.
 *   Sensormodelle, Betriebsspannung und Signalart erst anhand der Datenblätter
 *   klären und in docs/hardware.md eintragen. LEDs nur mit Vorwiderstand.
 */

// ---------------------------------------------------------------- Konfiguration
#define NUM_SLOTS 3

// Präsenzsensor-Typ je Station (für alle Plätze gleich):
//   PRESENCE_ULTRASONIC: HC-SR04 o. ä. (Trigger + Echo), Belegt wenn Abstand < Schwelle
//   PRESENCE_DIGITAL:    IR-Hindernissensor/Kontakt, digitaler Pegel
#define PRESENCE_ULTRASONIC 1
#define PRESENCE_DIGITAL    2
#define PRESENCE_TYPE PRESENCE_ULTRASONIC

// Vibrationssensor-Typ:
//   VIB_DIGITAL: z. B. SW-420 (liefert nur Ein/Aus-Impulse) -> Impulse zählen
//   VIB_ANALOG:  z. B. Piezo an Analogeingang -> Spitzenwert
#define VIB_DIGITAL 1
#define VIB_ANALOG  2
#define VIB_TYPE VIB_DIGITAL

struct SlotPins {
  const char* id;       // muss zu slot_map im Gateway passen
  uint8_t presencePin;  // Echo-Pin (Ultraschall) oder Signal-Pin (digital)
  uint8_t triggerPin;   // nur Ultraschall
  uint8_t vibPin;       // digital: D-Pin, analog: A-Pin
  uint8_t ledFree;      // grüne LED
  uint8_t ledOccupied;  // rote LED
};

// PLATZHALTER – vor Ort anpassen!
SlotPins SLOTS[NUM_SLOTS] = {
  {"A", 2, 3, 4, 5, 6},
  {"B", 7, 8, 9, 10, 11},
  {"C", 12, 13, A0, A1, A2},
};
const int8_t NET_LED_PIN = -1;  // -1 = keine Netzstatus-LED

// Schwellwerte / Zeiten (Plan 5.3; im Test kalibrieren)
const uint16_t OCCUPIED_BELOW_CM   = 40;     // Ultraschall: näher = belegt
const uint16_t MAX_VALID_CM        = 300;    // weiter/kein Echo = kein gültiger Wert
const bool     DIGITAL_ACTIVE_LOW  = true;   // viele IR-Module ziehen bei Hindernis auf LOW
const unsigned long STABLE_MS      = 2000;   // Zustandswechsel erst nach 2 s stabiler Messung
const unsigned long HEARTBEAT_MS   = 10000;  // Lebenszeichen je Platz
const unsigned long VIB_REPORT_MS  = 500;    // Vibration höchstens alle 0,5 s melden
const uint8_t  INVALID_LIMIT       = 5;      // so viele ungültige Messungen in Folge -> Fehler
const uint16_t VIB_PULSE_SCALE     = 64;     // digital: Score = Impulse * Faktor (max 1023)
const uint16_t VIB_ANALOG_NOISE    = 20;     // analog: darunter = 0
const uint8_t  LED_BRIGHTNESS      = 255;    // nur bei PWM-Pins wirksam; Energie sparen (Plan 11)

// ---------------------------------------------------------------- Zustand
struct SlotState {
  int8_t stable;          // 1 / 0 / -1 (unbekannt)
  int8_t candidate;
  unsigned long candidateSince;
  uint8_t invalidCount;
  uint16_t vibPeak;
  uint16_t vibPulses;
  bool lastVibLevel;
  unsigned long lastSent;
  unsigned long lastVibSent;
};

SlotState state[NUM_SLOTS];
unsigned long seq = 0;
bool netOk = false;
bool netKnown = false;
String rxLine;

// ---------------------------------------------------------------- Sensoren
// Liefert 1 (belegt), 0 (frei) oder -1 (ungültig)
int8_t readPresence(uint8_t i) {
#if PRESENCE_TYPE == PRESENCE_ULTRASONIC
  digitalWrite(SLOTS[i].triggerPin, LOW);
  delayMicroseconds(2);
  digitalWrite(SLOTS[i].triggerPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(SLOTS[i].triggerPin, LOW);
  unsigned long us = pulseIn(SLOTS[i].presencePin, HIGH, 25000UL);  // ~4 m Timeout
  if (us == 0) return -1;
  unsigned long cm = us / 58UL;
  if (cm == 0 || cm > MAX_VALID_CM) return -1;
  return cm < OCCUPIED_BELOW_CM ? 1 : 0;
#else
  bool level = digitalRead(SLOTS[i].presencePin);
  return (DIGITAL_ACTIVE_LOW ? !level : level) ? 1 : 0;
#endif
}

void sampleVibration(uint8_t i) {
#if VIB_TYPE == VIB_DIGITAL
  bool level = digitalRead(SLOTS[i].vibPin);
  if (level && !state[i].lastVibLevel) state[i].vibPulses++;
  state[i].lastVibLevel = level;
#else
  uint16_t v = analogRead(SLOTS[i].vibPin);
  if (v < VIB_ANALOG_NOISE) v = 0;
  if (v > state[i].vibPeak) state[i].vibPeak = v;
#endif
}

uint16_t takeVibrationScore(uint8_t i) {
#if VIB_TYPE == VIB_DIGITAL
  uint32_t s = (uint32_t)state[i].vibPulses * VIB_PULSE_SCALE;
  state[i].vibPulses = 0;
  return s > 1023 ? 1023 : (uint16_t)s;
#else
  uint16_t s = state[i].vibPeak;
  state[i].vibPeak = 0;
  return s;
#endif
}

uint16_t peekVibration(uint8_t i) {
#if VIB_TYPE == VIB_DIGITAL
  return state[i].vibPulses;
#else
  return state[i].vibPeak;
#endif
}

// ---------------------------------------------------------------- Ausgabe
void sendSlot(uint8_t i, uint16_t vib) {
  seq++;
  Serial.print(F("{\"slot_id\":\""));
  Serial.print(SLOTS[i].id);
  Serial.print(F("\",\"presence\":"));
  Serial.print(state[i].stable);
  Serial.print(F(",\"vibration\":"));
  Serial.print(state[i].stable < 0 ? 0 : vib);
  Serial.print(F(",\"seq\":"));
  Serial.print(seq);
  Serial.print(F(",\"state\":\""));
  Serial.print(state[i].stable < 0 ? F("error") : F("ok"));
  Serial.println(F("\"}"));
  state[i].lastSent = millis();
}

void updateLeds(uint8_t i, unsigned long now) {
  // Lokales Signal zusätzlich zur Beschriftung am Platz (nicht nur Farbe – Plan 9.2).
  if (state[i].stable < 0) {
    bool blink = (now / 500) % 2;  // unbekannt: rot blinkend, grün aus
    analogOrDigital(SLOTS[i].ledFree, false);
    analogOrDigital(SLOTS[i].ledOccupied, blink);
  } else {
    analogOrDigital(SLOTS[i].ledFree, state[i].stable == 0);
    analogOrDigital(SLOTS[i].ledOccupied, state[i].stable == 1);
  }
}

void analogOrDigital(uint8_t pin, bool on) {
  if (LED_BRIGHTNESS < 255 && digitalPinHasPWM(pin)) analogWrite(pin, on ? LED_BRIGHTNESS : 0);
  else digitalWrite(pin, on ? HIGH : LOW);
}

void readCommands() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      rxLine.trim();
      if (rxLine == "NET 1") { netOk = true; netKnown = true; }
      else if (rxLine == "NET 0") { netOk = false; netKnown = true; }
      rxLine = "";
    } else if (rxLine.length() < 32) {
      rxLine += c;
    }
  }
  if (NET_LED_PIN >= 0) {
    // an = Netz OK, blinkend = Netz weg, aus = noch unbekannt
    bool on = netKnown && (netOk || (millis() / 250) % 2);
    digitalWrite(NET_LED_PIN, on ? HIGH : LOW);
  }
}

// ---------------------------------------------------------------- Setup / Loop
void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < NUM_SLOTS; i++) {
#if PRESENCE_TYPE == PRESENCE_ULTRASONIC
    pinMode(SLOTS[i].triggerPin, OUTPUT);
    pinMode(SLOTS[i].presencePin, INPUT);
#else
    pinMode(SLOTS[i].presencePin, INPUT_PULLUP);
#endif
    pinMode(SLOTS[i].vibPin, INPUT);
    pinMode(SLOTS[i].ledFree, OUTPUT);
    pinMode(SLOTS[i].ledOccupied, OUTPUT);
    // Nach dem Start gilt nichts als bekannt (Plan 10.3 "Strom aus").
    state[i] = {-1, -1, 0, 0, 0, 0, false, 0, 0};
  }
  if (NET_LED_PIN >= 0) pinMode(NET_LED_PIN, OUTPUT);
  Serial.println(F("{\"type\":\"hello\",\"fw\":\"0.1.0\"}"));
}

unsigned long lastPresenceRead = 0;

void loop() {
  unsigned long now = millis();
  readCommands();

  for (uint8_t i = 0; i < NUM_SLOTS; i++) sampleVibration(i);

  // Präsenz ca. alle 100 ms lesen (Ultraschall braucht Zeit, Sensoren nicht gleichzeitig pingen).
  if (now - lastPresenceRead >= 100) {
    lastPresenceRead = now;
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
      int8_t raw = readPresence(i);
      if (raw < 0) {
        if (state[i].invalidCount < 255) state[i].invalidCount++;
        if (state[i].invalidCount < INVALID_LIMIT) continue;  // einzelne Aussetzer ignorieren
      } else {
        state[i].invalidCount = 0;
      }
      if (raw != state[i].candidate) {
        state[i].candidate = raw;
        state[i].candidateSince = now;
      } else if (raw != state[i].stable && now - state[i].candidateSince >= STABLE_MS) {
        state[i].stable = raw;  // Entprellung: erst nach STABLE_MS übernehmen
        sendSlot(i, takeVibrationScore(i));
      }
    }
  }

  for (uint8_t i = 0; i < NUM_SLOTS; i++) {
    if (peekVibration(i) > 0 && now - state[i].lastVibSent >= VIB_REPORT_MS) {
      state[i].lastVibSent = now;
      sendSlot(i, takeVibrationScore(i));
    } else if (now - state[i].lastSent >= HEARTBEAT_MS) {
      sendSlot(i, takeVibrationScore(i));
    }
    updateLeds(i, now);
  }
}
