#!/usr/bin/env bash
# Builds the sketch for an Arduino Uno (ATmega328P, 16 MHz) in all four sensor configurations with
# -Wall -Wextra, then runs it in the simavr simulator without sensors and checks the serial output
# with the agent's parser: a hello line, then every space as "error" (-> unknown, never free).
#   sudo apt install gcc-avr avr-libc arduino-core-avr simavr     # Debian/Ubuntu packages
#   firmware/check.sh [seconds in the simulator, default 25 - covers one heartbeat round]
# With the Arduino IDE / arduino-cli instead: arduino-cli compile --fqbn arduino:avr:uno firmware/smart_bike_station
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKETCH="$ROOT/firmware/smart_bike_station/smart_bike_station.ino"
CORE=/usr/share/arduino/hardware/arduino/avr/cores/arduino
VARIANT=/usr/share/arduino/hardware/arduino/avr/variants/standard
SIM_SECONDS="${1:-25}"
command -v avr-g++ >/dev/null && [ -d "$CORE" ] || { echo "Missing: sudo apt install gcc-avr avr-libc arduino-core-avr" >&2; exit 2; }

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
FLAGS=(-mmcu=atmega328p -DF_CPU=16000000L -DARDUINO=10806 -DARDUINO_AVR_UNO -DARDUINO_ARCH_AVR -Os
       -ffunction-sections -fdata-sections -flto -fno-fat-lto-objects -I"$CORE" -I"$VARIANT")
CXX=(-std=gnu++11 -fpermissive -fno-exceptions -fno-threadsafe-statics)

# Arduino core (as the IDE builds it; warnings of the core itself are not ours)
mkdir -p "$BUILD/core"
for f in "$CORE"/*.c; do avr-gcc "${FLAGS[@]}" -w -std=gnu11 -c "$f" -o "$BUILD/core/$(basename "$f").o"; done
for f in "$CORE"/*.cpp; do avr-g++ "${FLAGS[@]}" -w "${CXX[@]}" -c "$f" -o "$BUILD/core/$(basename "$f").o"; done
for f in "$CORE"/*.S; do avr-gcc "${FLAGS[@]}" -x assembler-with-cpp -c "$f" -o "$BUILD/core/$(basename "$f").o"; done
avr-gcc-ar rcs "$BUILD/core.a" "$BUILD"/core/*.o

for variant in "PRESENCE_ULTRASONIC VIB_DIGITAL" "PRESENCE_ULTRASONIC VIB_ANALOG" "PRESENCE_DIGITAL VIB_DIGITAL" "PRESENCE_DIGITAL VIB_ANALOG"; do
  read -r presence vib <<<"$variant"
  name="${presence#PRESENCE_}-${vib#VIB_}"
  { echo '#include <Arduino.h>'
    sed "s/^#define PRESENCE_TYPE .*/#define PRESENCE_TYPE $presence/; s/^#define VIB_TYPE .*/#define VIB_TYPE $vib/" "$SKETCH"
  } > "$BUILD/$name.cpp"
  avr-g++ "${FLAGS[@]}" "${CXX[@]}" -Wall -Wextra -Werror -c "$BUILD/$name.cpp" -o "$BUILD/$name.o"
  avr-gcc -mmcu=atmega328p -Os -flto -fuse-linker-plugin -Wl,--gc-sections "$BUILD/$name.o" "$BUILD/core.a" -lm -o "$BUILD/$name.elf"
  echo "built $name: $(avr-size --mcu=atmega328p -C "$BUILD/$name.elf" | awk '/Program|Data/ {printf "%s %s %s  ", $1, $2, $4 $5}')"
done

if ! command -v simavr >/dev/null; then
  echo "simavr not installed - skipping the simulator run"
  exit 0
fi
timeout "$SIM_SECONDS" simavr -m atmega328p -f 16000000 "$BUILD/ULTRASONIC-DIGITAL.elf" > "$BUILD/sim.out" 2>&1 || true
PYTHONPATH="$ROOT/agent" python3 - "$BUILD/sim.out" <<'PY'
import json, re, sys
from bikeagent.gateway import parse_line

raw = open(sys.argv[1], errors="replace").read()
lines = [re.sub(r"\x1b\[[0-9;]*m", "", l).rstrip(".").strip() for l in raw.splitlines() if "{" in l]
assert lines and json.loads(lines[0])["type"] == "hello", "no hello line"
readings = [parse_line(l, {"A": "A", "B": "B", "C": "C"}) for l in lines[1:]]
seqs = [json.loads(l)["seq"] for l in lines[1:]]
assert len(readings) >= 3, "no readings"
assert {r["slot_id"] for r in readings} == {"A", "B", "C"}
assert all(r["sensor_state"] == "error" and r["occupied"] is None for r in readings), "a space without sensor was not 'unknown'"
assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "sequence numbers not increasing"
print(f"simulator: {len(readings)} readings without sensors, all 'unknown' (never free), seq {seqs[0]}..{seqs[-1]}")
PY
