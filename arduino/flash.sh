#!/usr/bin/env bash
# Flasht die Smart-Bicycle-Box-Firmware auf einen angeschlossenen Arduino und
# installiert bei Bedarf die komplette Toolchain (arduino-cli, Board-Core, MFRC522-Bibliothek).
# Idempotent: mehrfach ausführbar. Standard-Board: Arduino Nano 33 IoT (SAMD).
#
#   bash arduino/flash.sh                 # aus einem Repo-Checkout
#   FQBN=arduino:avr:uno bash arduino/flash.sh   # anderes Board
#   PORT=/dev/ttyACM0 bash arduino/flash.sh      # Port fest vorgeben
#
# Der RC522-NFC-Leser hängt am SPI: SCK=D13, MISO=D12, MOSI=D11, SS=D10, RST=D9, 3V3, GND.
set -euo pipefail

FQBN="${FQBN:-arduino:samd:nano_33_iot}"
CORE="${CORE:-$(printf '%s' "$FQBN" | cut -d: -f1-2)}"   # z. B. arduino:samd
HERE="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$HERE/smart_bicycle_box"
BINDIR="${BINDIR:-$HOME/.local/bin}"

if [ ! -f "$SKETCH/smart_bicycle_box.ino" ]; then
  echo "FEHLER: Sketch nicht gefunden unter $SKETCH – bitte aus einem Repo-Checkout ausführen." >&2
  exit 1
fi

# arduino-cli bereitstellen
if command -v arduino-cli >/dev/null 2>&1; then
  CLI="$(command -v arduino-cli)"
else
  CLI="$BINDIR/arduino-cli"
  if [ ! -x "$CLI" ]; then
    echo ">> Installiere arduino-cli nach $BINDIR"
    mkdir -p "$BINDIR"
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR="$BINDIR" sh
  fi
fi

echo ">> Board-Core und Bibliotheken sicherstellen"
"$CLI" core update-index
"$CLI" core list 2>/dev/null | awk '{print $1}' | grep -qx "$CORE" || "$CLI" core install "$CORE"
"$CLI" lib list 2>/dev/null | awk '{print $1}' | grep -qix "MFRC522" || "$CLI" lib install "MFRC522"

# Dienst anhalten, damit der serielle Port frei ist (falls vorhanden)
STOPPED=0
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet bike-agent 2>/dev/null; then
  echo ">> Stoppe bike-agent (gibt den seriellen Port frei)"
  sudo systemctl stop bike-agent && STOPPED=1
fi

# Port bestimmen
if [ -z "${PORT:-}" ]; then
  PORT="$("$CLI" board list 2>/dev/null | awk '/serial/{print $1; exit}')"
  PORT="${PORT:-/dev/ttyACM0}"
fi

echo ">> Kompiliere ($FQBN)"
"$CLI" compile --fqbn "$FQBN" "$SKETCH"
echo ">> Lade hoch auf $PORT"
"$CLI" upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"

if [ "$STOPPED" = 1 ]; then
  echo ">> Starte bike-agent wieder"
  sudo systemctl start bike-agent || true
fi

echo "Fertig: Firmware auf $PORT geflasht ($FQBN)."
echo "Tipp: Bricht der Upload mit 'No device found' ab, den RESET-Knopf EINMAL kurz drücken (kein Doppel-Tipp) und erneut ausführen."
