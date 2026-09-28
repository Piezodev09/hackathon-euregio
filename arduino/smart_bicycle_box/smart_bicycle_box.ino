/*
 * Smart Bicycle Box – Arduino-Firmware für EINEN vorne offenen Stellplatz
 *
 * Aufgaben: Präsenz- und Erschütterungssensor lesen, Belegung entprellen, lokale LEDs setzen
 * und Messungen als JSON-Zeilen über USB-Seriell an den Raspberry Pi senden.
 * Keine Nutzerdaten, keine KI auf dem Arduino. Der NFC-Leser ist geplant und hier noch nicht
 * angebunden.
 *
 * Ausgabe je Zeile (115200 Baud):
 *   {"presence":1,"vibration":12,"seq":1042,"state":"ok"}
 *   presence: 1 belegt, 0 frei, -1 kein gültiger Messwert (-> "STATUS UNBEKANNT")
 *   vibration: 0..1023 (Spitzenwert bzw. skalierte Impulszahl im letzten Intervall)
 *
 * Eingabe vom Pi:  "NET 1" / "NET 0"  -> Netzstatus-LED
 *
 * !!! ACHTUNG – VOR DER VERDRAHTUNG PRÜFEN !!!
 *   Alle Pinnummern, Sensortypen und Schwellwerte unten sind PLATZHALTER.
 *   Sensormodelle, Betriebsspannung und Signalart erst anhand der Datenblätter
 *   klären und in docs/hardware.md eintragen. LEDs nur mit Vorwiderstand.
 */

// ---------------------------------------------------------------- Konfiguration
// Präsenzsensor (innen an der linken Wand, ca. 40 cm hoch, misst quer über den Stellplatz):
//   PRESENCE_ULTRASONIC: HC-SR04 o. ä. (Trigger + Echo), belegt wenn Abstand < Schwelle
//   PRESENCE_DIGITAL:    IR-Lichtschranke/Kontakt, digitaler Pegel
// Ein ToF-Laser-Distanzsensor (z. B. VL53L1X, I2C) ist die empfohlene Alternative, braucht aber
// eine Bibliothek und ist hier noch nicht umgesetzt.
#define PRESENCE_ULTRASONIC 1
#define PRESENCE_DIGITAL    2
#define PRESENCE_TYPE PRESENCE_ULTRASONIC

// Erschütterungssensor (an der Radhalteschiene):
//   VIB_DIGITAL: z. B. SW-420 (liefert nur Ein/Aus-Impulse) -> Impulse zählen
//   VIB_ANALOG:  z. B. Piezo an Analogeingang -> Spitzenwert
#define VIB_DIGITAL 1
#define VIB_ANALOG  2
#define VIB_TYPE VIB_DIGITAL

// PLATZHALTER – vor Ort anpassen!
const uint8_t PRESENCE_PIN    = 2;   // Echo-Pin (Ultraschall) oder Signal-Pin (digital)
const uint8_t TRIGGER_PIN     = 3;   // nur Ultraschall
const uint8_t VIB_PIN         = 4;   // digital: D-Pin, analog: A-Pin
const uint8_t LED_FREE_PIN    = 5;   // grüne LED
const uint8_t LED_OCCUPIED_PIN = 6;  // rote LED
const int8_t  NET_LED_PIN     = -1;  // -1 = keine Netzstatus-LED

// Schwellwerte / Zeiten (im Test mit echten Fahrrädern kalibrieren)
const uint16_t OCCUPIED_BELOW_CM   = 60;     // Innenbreite ca. 75 cm: näher als 60 cm = Fahrrad (Vorschlag)
const uint16_t MAX_VALID_CM        = 300;    // weiter/kein Echo = kein gültiger Wert
const bool     DIGITAL_ACTIVE_LOW  = true;   // viele IR-Module ziehen bei Hindernis auf LOW
const unsigned long STABLE_MS      = 2000;   // Zustandswechsel erst nach 2 s stabiler Messung
const unsigned long HEARTBEAT_MS   = 10000;  // Lebenszeichen
const unsigned long VIB_REPORT_MS  = 500;    // Vibration höchstens alle 0,5 s melden
const uint8_t  INVALID_LIMIT       = 5;      // so viele ungültige Messungen in Folge -> Fehler
const uint16_t VIB_PULSE_SCALE     = 64;     // digital: Score = Impulse * Faktor (max 1023)
const uint16_t VIB_ANALOG_NOISE    = 20;     // analog: darunter = 0
const uint8_t  LED_BRIGHTNESS      = 255;    // nur bei PWM-Pins wirksam

// ---------------------------------------------------------------- Zustand
// Nach dem Start gilt nichts als bekannt: stable = -1 ("STATUS UNBEKANNT"), nie automatisch frei.
int8_t stable = -1;          // 1 / 0 / -1 (unbekannt)
int8_t candidate = -1;
unsigned long candidateSince = 0;
uint8_t invalidCount = 0;
uint16_t vibPeak = 0;
uint16_t vibPulses = 0;
bool lastVibLevel = false;
unsigned long lastSent = 0;
unsigned long lastVibSent = 0;
unsigned long seq = 0;
bool netOk = false;
bool netKnown = false;
String rxLine;

// ---------------------------------------------------------------- Sensoren
// Liefert 1 (belegt), 0 (frei) oder -1 (ungültig)
int8_t readPresence() {
#if PRESENCE_TYPE == PRESENCE_ULTRASONIC
  digitalWrite(TRIGGER_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIGGER_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIGGER_PIN, LOW);
  unsigned long us = pulseIn(PRESENCE_PIN, HIGH, 25000UL);  // ~4 m Timeout
  if (us == 0) return -1;
  unsigned long cm = us / 58UL;
  if (cm == 0 || cm > MAX_VALID_CM) return -1;
  return cm < OCCUPIED_BELOW_CM ? 1 : 0;
#else
  bool level = digitalRead(PRESENCE_PIN);
  return (DIGITAL_ACTIVE_LOW ? !level : level) ? 1 : 0;
#endif
}

void sampleVibration() {
#if VIB_TYPE == VIB_DIGITAL
  bool level = digitalRead(VIB_PIN);
  if (level && !lastVibLevel) vibPulses++;
  lastVibLevel = level;
#else
  uint16_t v = analogRead(VIB_PIN);
  if (v < VIB_ANALOG_NOISE) v = 0;
  if (v > vibPeak) vibPeak = v;
#endif
}

uint16_t takeVibrationScore() {
#if VIB_TYPE == VIB_DIGITAL
  uint32_t s = (uint32_t)vibPulses * VIB_PULSE_SCALE;
  vibPulses = 0;
  return s > 1023 ? 1023 : (uint16_t)s;
#else
  uint16_t s = vibPeak;
  vibPeak = 0;
  return s;
#endif
}

uint16_t peekVibration() {
#if VIB_TYPE == VIB_DIGITAL
  return vibPulses;
#else
  return vibPeak;
#endif
}

// ---------------------------------------------------------------- Ausgabe
void sendMeasurement(uint16_t vib) {
  seq++;
  Serial.print(F("{\"presence\":"));
  Serial.print(stable);
  Serial.print(F(",\"vibration\":"));
  Serial.print(stable < 0 ? 0 : vib);
  Serial.print(F(",\"seq\":"));
  Serial.print(seq);
  Serial.print(F(",\"state\":\""));
  Serial.print(stable < 0 ? F("error") : F("ok"));
  Serial.println(F("\"}"));
  lastSent = millis();
}

void setLed(uint8_t pin, bool on) {
  if (LED_BRIGHTNESS < 255 && digitalPinHasPWM(pin)) analogWrite(pin, on ? LED_BRIGHTNESS : 0);
  else digitalWrite(pin, on ? HIGH : LOW);
}

void updateLeds(unsigned long now) {
  // Nur lokale Zusatzanzeige; der Zustand steht als Wort + Symbol auf dem Display.
  if (stable < 0) {
    bool blink = (now / 500) % 2;  // unbekannt: rot blinkend, grün aus
    setLed(LED_FREE_PIN, false);
    setLed(LED_OCCUPIED_PIN, blink);
  } else {
    setLed(LED_FREE_PIN, stable == 0);
    setLed(LED_OCCUPIED_PIN, stable == 1);
  }
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
#if PRESENCE_TYPE == PRESENCE_ULTRASONIC
  pinMode(TRIGGER_PIN, OUTPUT);
  pinMode(PRESENCE_PIN, INPUT);
#else
  pinMode(PRESENCE_PIN, INPUT_PULLUP);
#endif
  pinMode(VIB_PIN, INPUT);
  pinMode(LED_FREE_PIN, OUTPUT);
  pinMode(LED_OCCUPIED_PIN, OUTPUT);
  if (NET_LED_PIN >= 0) pinMode(NET_LED_PIN, OUTPUT);
  Serial.println(F("{\"type\":\"hello\",\"fw\":\"0.2.0\"}"));
}

unsigned long lastPresenceRead = 0;

void loop() {
  unsigned long now = millis();
  readCommands();
  sampleVibration();

  // Präsenz ca. alle 100 ms lesen (Ultraschall braucht Zeit).
  if (now - lastPresenceRead >= 100) {
    lastPresenceRead = now;
    int8_t raw = readPresence();
    bool skip = false;
    if (raw < 0) {
      if (invalidCount < 255) invalidCount++;
      skip = invalidCount < INVALID_LIMIT;  // einzelne Aussetzer ignorieren
    } else {
      invalidCount = 0;
    }
    if (!skip) {
      if (raw != candidate) {
        candidate = raw;
        candidateSince = now;
      } else if (raw != stable && now - candidateSince >= STABLE_MS) {
        stable = raw;  // Entprellung: erst nach STABLE_MS übernehmen
        sendMeasurement(takeVibrationScore());
      }
    }
  }

  if (peekVibration() > 0 && now - lastVibSent >= VIB_REPORT_MS) {
    lastVibSent = now;
    sendMeasurement(takeVibrationScore());
  } else if (now - lastSent >= HEARTBEAT_MS) {
    sendMeasurement(takeVibrationScore());
  }
  updateLeds(now);
}
