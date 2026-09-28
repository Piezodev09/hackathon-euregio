/*
 * Smart Bike Station - Arduino firmware (project plan 3.1, 5)
 *
 * Responsibilities: read the sensors, debounce occupancy, drive the local LEDs and
 * send measurements as JSON lines over USB serial to the Raspberry Pi.
 * No user data, no AI on the Arduino.
 *
 * Output, one line each (115200 baud):
 *   {"slot_id":"A","presence":1,"vibration":12,"seq":1042,"state":"ok"}
 *   presence: 1 occupied, 0 free, -1 no valid reading (-> "unknown")
 *   vibration: 0..1023 (peak value or scaled pulse count in the last interval)
 *
 * Input from the Pi:  "NET 1" / "NET 0"  -> network status LED
 *
 * !!! CHECK BEFORE WIRING (project plan 4.1) !!!
 *   All pin numbers, sensor types and thresholds below are PLACEHOLDERS.
 *   Confirm sensor models, supply voltage and signal type with the data sheets first
 *   and record them in firmware/README.md. LEDs only with a series resistor.
 */

// ---------------------------------------------------------------- configuration
#define NUM_SLOTS 3

// Presence sensor type per station (same for all spaces):
//   PRESENCE_ULTRASONIC: HC-SR04 or similar (trigger + echo), occupied if distance < threshold.
//                        Recommended: a missing echo (broken wire, dead sensor) is detected -> "unknown".
//   PRESENCE_DIGITAL:    IR obstacle sensor / contact, digital level. A digital line CANNOT reveal a
//                        broken wire: the input pull-up then reads "no obstacle". With an active-low
//                        sensor that means "free" - prefer a sensor/wiring where a broken wire reads
//                        "occupied" (DIGITAL_ACTIVE_LOW = false), see firmware/README.md.
#define PRESENCE_ULTRASONIC 1
#define PRESENCE_DIGITAL    2
#define PRESENCE_TYPE PRESENCE_ULTRASONIC

// Vibration sensor type:
//   VIB_DIGITAL: e.g. SW-420 (on/off pulses only) -> count pulses
//   VIB_ANALOG:  e.g. piezo on an analog input -> peak value
#define VIB_DIGITAL 1
#define VIB_ANALOG  2
#define VIB_TYPE VIB_DIGITAL

struct SlotPins {
  const char* id;       // must match the slot key in the portal
  uint8_t presencePin;  // echo pin (ultrasonic) or signal pin (digital)
  uint8_t triggerPin;   // ultrasonic only
  uint8_t vibPin;       // digital: D pin, analog: A pin
  uint8_t ledFree;      // green LED
  uint8_t ledOccupied;  // red LED
};

// PLACEHOLDERS - adapt on site!
SlotPins SLOTS[NUM_SLOTS] = {
  {"A", 2, 3, 4, 5, 6},
  {"B", 7, 8, 9, 10, 11},
  {"C", 12, 13, A0, A1, A2},
};
const int8_t NET_LED_PIN = -1;  // -1 = no network status LED

// Thresholds / timing (plan 5.3; calibrate during tests)
const uint16_t OCCUPIED_BELOW_CM   = 40;     // ultrasonic: closer = occupied
const uint16_t MAX_VALID_CM        = 300;    // farther / no echo = no valid reading
const bool     DIGITAL_ACTIVE_LOW  = true;   // many IR modules pull LOW on an obstacle
const unsigned long STABLE_MS      = 2000;   // state change only after 2 s of stable readings
const unsigned long HEARTBEAT_MS   = 10000;  // heartbeat per space
const unsigned long VIB_REPORT_MS  = 500;    // report vibration at most every 0.5 s
const uint8_t  INVALID_LIMIT       = 5;      // this many invalid readings in a row -> error
const uint16_t VIB_PULSE_SCALE     = 64;     // digital: score = pulses * factor (max 1023)
const uint16_t VIB_ANALOG_NOISE    = 20;     // analog: below = 0
const uint8_t  LED_BRIGHTNESS      = 255;    // PWM pins only; saves energy (plan 11)

// ---------------------------------------------------------------- state
struct SlotState {
  int8_t stable;          // 1 / 0 / -1 (unknown)
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

// ---------------------------------------------------------------- sensors
// Returns 1 (occupied), 0 (free) or -1 (invalid)
int8_t readPresence(uint8_t i) {
#if PRESENCE_TYPE == PRESENCE_ULTRASONIC
  digitalWrite(SLOTS[i].triggerPin, LOW);
  delayMicroseconds(2);
  digitalWrite(SLOTS[i].triggerPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(SLOTS[i].triggerPin, LOW);
  unsigned long us = pulseIn(SLOTS[i].presencePin, HIGH, 25000UL);  // ~4 m timeout
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

// ---------------------------------------------------------------- output
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

// Defined before use so the sketch also builds outside the Arduino IDE (PlatformIO, plain avr-g++).
void analogOrDigital(uint8_t pin, bool on) {
  if (LED_BRIGHTNESS < 255 && digitalPinHasPWM(pin)) analogWrite(pin, on ? LED_BRIGHTNESS : 0);
  else digitalWrite(pin, on ? HIGH : LOW);
}

void updateLeds(uint8_t i, unsigned long now) {
  // Local signal in addition to the printed label at the space (never colour alone - plan 9.2).
  if (state[i].stable < 0) {
    bool blink = (now / 500) % 2;  // unknown: red blinking, green off
    analogOrDigital(SLOTS[i].ledFree, false);
    analogOrDigital(SLOTS[i].ledOccupied, blink);
  } else {
    analogOrDigital(SLOTS[i].ledFree, state[i].stable == 0);
    analogOrDigital(SLOTS[i].ledOccupied, state[i].stable == 1);
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
    // on = network OK, blinking = network down, off = not known yet
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
    // After a start nothing is known (plan 10.3 "power loss").
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

  // Read presence about every 100 ms (ultrasonic needs time; never ping sensors simultaneously).
  if (now - lastPresenceRead >= 100) {
    lastPresenceRead = now;
    for (uint8_t i = 0; i < NUM_SLOTS; i++) {
      int8_t raw = readPresence(i);
      if (raw < 0) {
        if (state[i].invalidCount < 255) state[i].invalidCount++;
        if (state[i].invalidCount < INVALID_LIMIT) continue;  // ignore single dropouts
      } else {
        state[i].invalidCount = 0;
      }
      if (raw != state[i].candidate) {
        state[i].candidate = raw;
        state[i].candidateSince = now;
      } else if (raw != state[i].stable && now - state[i].candidateSince >= STABLE_MS) {
        state[i].stable = raw;  // debounce: accept only after STABLE_MS
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
