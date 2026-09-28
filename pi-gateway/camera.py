"""Kamera am Raspberry Pi – nur Einzelbilder, nur wenn die Kamera für den Stellplatz im Portal freigegeben ist.

Erkennung in dieser Reihenfolge:
  rpicam-still / libcamera-still  -> Kameramodul am CSI-Anschluss (Raspberry Pi OS Bookworm)
  fswebcam                         -> USB-Webcam  (sudo apt install fswebcam)
  ffmpeg + /dev/video0             -> USB-Webcam  (sudo apt install ffmpeg)
  Simulator                        -> mitgeliefertes Testbild (sim-camera.jpg), eindeutig beschriftet

Bilder werden nicht auf dem Pi gespeichert (nur kurz in einer temporären Datei) und direkt hochgeladen.
Ein Arduino Uno kann keine Kamera sinnvoll betreiben; dafür ist der Pi zuständig.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("camera")
HERE = Path(__file__).resolve().parent
SIM_IMAGE = HERE / "sim-camera.jpg"
MAX_BYTES = 2 * 1024 * 1024
WIDTH, HEIGHT = 1280, 720


def _cmd(kind: str, out: str, device: str) -> list[str]:
    if kind in ("rpicam-still", "libcamera-still"):
        return [kind, "-n", "-t", "800", "--width", str(WIDTH), "--height", str(HEIGHT), "-q", "80", "-o", out]
    if kind == "fswebcam":
        return ["fswebcam", "-q", "-d", device, "-r", f"{WIDTH}x{HEIGHT}", "--no-banner", "--jpeg", "80", "-S", "5", out]
    if kind == "ffmpeg":
        return ["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-video_size", f"{WIDTH}x{HEIGHT}", "-i", device,
                "-frames:v", "1", "-q:v", "4", "-y", out]
    raise ValueError(kind)


def detect(simulated: bool = False, device: str = "/dev/video0") -> str:
    if simulated:
        return "simulator" if SIM_IMAGE.exists() else "none"
    for tool in ("rpicam-still", "libcamera-still"):
        if shutil.which(tool):
            return tool
    if os.path.exists(device):
        if shutil.which("fswebcam"):
            return "fswebcam"
        if shutil.which("ffmpeg"):
            return "ffmpeg"
    return "none"


class Camera:
    def __init__(self, simulated: bool = False, device: str = "/dev/video0", timeout_s: float = 20):
        self.device = device
        self.timeout_s = timeout_s
        self.kind = detect(simulated, device)

    @property
    def available(self) -> bool:
        return self.kind != "none"

    def capture(self) -> bytes | None:
        """Nimmt ein JPEG auf. None bei Fehler (wird geloggt, bricht nie die Messkette ab)."""
        if self.kind == "none":
            return None
        if self.kind == "simulator":
            return SIM_IMAGE.read_bytes()
        with tempfile.TemporaryDirectory(prefix="bike-cam-") as tmp:
            out = os.path.join(tmp, "snap.jpg")
            try:
                r = subprocess.run(_cmd(self.kind, out, self.device), capture_output=True, timeout=self.timeout_s, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.warning("Kamera (%s) fehlgeschlagen: %s", self.kind, exc)
                return None
            if r.returncode != 0 or not os.path.exists(out):
                log.warning("Kamera (%s) meldet Fehler %s: %s", self.kind, r.returncode, r.stderr[-300:].decode(errors="replace"))
                return None
            data = Path(out).read_bytes()
        if not data.startswith(b"\xff\xd8\xff") or len(data) > MAX_BYTES:
            log.warning("Kamerabild ungültig oder zu groß (%d Byte)", len(data))
            return None
        return data
