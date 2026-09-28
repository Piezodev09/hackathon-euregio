#!/usr/bin/env python3
"""Arduino-Simulator für EINEN Stellplatz – NUR für Entwicklung und Tests ohne Hardware.

Gibt Zeilen im selben Format wie der Arduino-Sketch auf stdout aus. Zusammen mit
`gateway.py --stdin --simulated` entsteht die komplette Kette bis zum Dashboard.
Simulierte Daten werden dort ausdrücklich als "simuliert" gekennzeichnet (Plan 15:
"Einen simulierten Ablauf niemals stillschweigend als echten Live-Sensor ausgeben").

Beispiel:
    python simulator.py | python gateway.py --stdin --simulated

Interaktive Befehle (Eingabe + Enter im Terminal):
    p    Fahrrad einstellen/ausparken (FREI <-> BELEGT)
    b    leichtes Anstoßen (ein Ausschlag)
    s    kräftiges, wiederholtes Rütteln (ca. 3 s)
    e    Sensorfehler ein/aus (-> STATUS UNBEKANNT)
    q    beenden
Mit --auto wechselt die Belegung zufällig (zum Füllen der Auslastungsanzeige).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time


class Simulator:
    def __init__(self, heartbeat_s: float, out=sys.stdout, present: bool = False):
        self.present = present
        self.error = False
        self.pending_vib: list[tuple[float, int]] = []  # (Zeitpunkt, Stärke)
        self.last_sent = 0.0
        self.heartbeat_s = heartbeat_s
        self.seq = 0
        self.out = out
        self.lock = threading.Lock()

    def emit(self, vib: int = 0) -> None:
        self.seq += 1
        msg = {
            "presence": -1 if self.error else int(self.present),
            "vibration": 0 if self.error else vib,
            "seq": self.seq,
            "state": "error" if self.error else "ok",
        }
        self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.out.flush()
        self.last_sent = time.monotonic()

    def command(self, cmd: str) -> bool:
        op = cmd.strip().lower()
        if not op:
            return True
        if op == "q":
            return False
        now = time.monotonic()
        with self.lock:
            if op == "p":
                # Beim Einstellen/Ausparken wackelt es kurz – realistisch für die Schonzeit.
                self.present = not self.present
                self.emit(vib=random.randint(150, 350))
            elif op == "b":
                self.pending_vib.append((now, random.randint(320, 450)))
            elif op == "s":
                self.pending_vib.extend((now + i * 0.4, random.randint(550, 950)) for i in range(8))
            elif op == "e":
                self.error = not self.error
                self.emit()
            else:
                print("? Befehl: p | b | s | e | q", file=sys.stderr)
        return True

    def tick(self) -> None:
        now = time.monotonic()
        with self.lock:
            due = [v for v in self.pending_vib if v[0] <= now]
            if due:
                self.pending_vib = [v for v in self.pending_vib if v[0] > now]
                self.emit(vib=max(v[1] for v in due))
            elif now - self.last_sent >= self.heartbeat_s:
                self.emit()

    def auto_step(self) -> None:
        with self.lock:
            if not self.error:
                self.present = not self.present
                self.emit(vib=random.randint(100, 300))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--heartbeat", type=float, default=10.0)
    ap.add_argument("--auto", type=float, default=0.0, metavar="SEK", help="alle SEK Sekunden zufälliger Belegungswechsel")
    ap.add_argument("--commands", default=None, help="Befehle aus Datei statt Terminal lesen")
    args = ap.parse_args()

    sim = Simulator(args.heartbeat)
    print(json.dumps({"type": "hello", "fw": "simulator"}), flush=True)
    sim.emit()

    running = threading.Event()
    running.set()

    def read_commands():
        try:
            src = open(args.commands) if args.commands else open("/dev/tty")
        except OSError:
            src = sys.stdin
        print("Simulator bereit. Befehle: p | b | s | e | q", file=sys.stderr)
        for line in src:
            if not sim.command(line):
                break
        running.clear()

    threading.Thread(target=read_commands, daemon=True).start()
    next_auto = time.monotonic() + args.auto if args.auto else None
    try:
        while running.is_set():
            sim.tick()
            if next_auto and time.monotonic() >= next_auto:
                sim.auto_step()
                next_auto = time.monotonic() + random.uniform(0.5, 1.5) * args.auto
            time.sleep(0.1)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
