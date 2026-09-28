"""Smart Bike Station agent for the Raspberry Pi (or a Home Assistant add-on / Docker container).

Modules:
  agent      pairing, heartbeat, remote configuration, token rotation, self-update (CLI entry point)
  gateway    Arduino lines -> validated measurements -> buffered HTTPS upload
  simulator  Arduino simulator for development and demos without hardware
  mqtt       optional local MQTT publisher with Home Assistant discovery
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    __version__ = (ROOT / "VERSION").read_text().strip()
except OSError:  # pragma: no cover - broken installation
    __version__ = "0.0.0"
