#!/usr/bin/env python3
"""Arduino-Simulator – NUR für Entwicklung und Tests ohne Hardware.

Gibt Zeilen im selben Format wie der Arduino-Sketch auf stdout aus. Zusammen mit
`gateway.py --stdin --simulated` entsteht die komplette Kette bis zum Dashboard.
Simulierte Daten werden dort ausdrücklich als "simuliert" gekennzeichnet (Plan 15:
"Einen simulierten Ablauf niemals stillschweigend als echten Live-Sensor ausgeben").

Beispiel:
    python simulator.py | python gateway.py --stdin --simulated

Interaktive Befehle (Eingabe + Enter im Terminal):
    p A    Platz A belegen/freigeben
    b A    leichtes Anstoßen an Platz A (ein Ausschlag)
    s A    kräftiges, wiederholtes Rütteln an Platz A (ca. 3 s)
    e A    Sensorfehler an Platz A ein/aus
    q      beenden
Mit --auto wechseln Belegungen zufällig (zum Füllen der Auslastungsanzeige).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time


class SimSlot:
    def __init__(self, sid: str, present: bool):
        self.id = sid
        self.present = present
        self.error = False
        self.pending_vib: list[tuple[float, int]] = []  # (Zeitpunkt, Stärke)
        self.last_sent = 0.0


class Simulator:
    def __init__(self, slots: list[str], heartbeat_s: float, out=sys.stdout):
        self.slots = {s: SimSlot(s, present=(i == 0)) for i, s in enumerate(slots)}
        self.heartbeat_s = heartbeat_s
        self.seq = 0
        self.out = out
        self.lock = threading.Lock()

    def emit(self, slot: SimSlot, vib: int = 0) -> None:
        self.seq += 1
        msg = {
            "slot_id": slot.id,
            "presence": -1 if slot.error else int(slot.present),
            "vibration": 0 if slot.error else vib,
            "seq": self.seq,
            "state": "error" if slot.error else "ok",
        }
        self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
        self.out.flush()
        slot.last_sent = time.monotonic()

    def command(self, cmd: str) -> bool:
        parts = cmd.strip().split()
        if not parts:
            return True
        if parts[0] == "q":
            return False
        if len(parts) != 2 or parts[1].upper() not in self.slots:
            print(f"? Befehl: p|b|s|e <{'/'.join(self.slots)}> oder q", file=sys.stderr)
            return True
        op, slot = parts[0], self.slots[parts[1].upper()]
        now = time.monotonic()
        with self.lock:
            if op == "p":
                # Beim Einstellen/Ausparken wackelt es kurz – realistisch für die Schonzeit.
                slot.present = not slot.present
                self.emit(slot, vib=random.randint(150, 350))
            elif op == "b":
                slot.pending_vib.append((now, random.randint(320, 450)))
            elif op == "s":
                slot.pending_vib.extend((now + i * 0.4, random.randint(550, 950)) for i in range(8))
            elif op == "e":
                slot.error = not slot.error
                self.emit(slot)
        return True

    def tick(self) -> None:
        now = time.monotonic()
        with self.lock:
            for slot in self.slots.values():
                due = [v for v in slot.pending_vib if v[0] <= now]
                if due:
                    slot.pending_vib = [v for v in slot.pending_vib if v[0] > now]
                    self.emit(slot, vib=max(v[1] for v in due))
                elif now - slot.last_sent >= self.heartbeat_s:
                    self.emit(slot)

    def auto_step(self) -> None:
        with self.lock:
            slot = random.choice(list(self.slots.values()))
            if not slot.error:
                slot.present = not slot.present
                self.emit(slot, vib=random.randint(100, 300))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slots", default="A,B,C", help="Arduino-Platzkennungen, kommagetrennt")
    ap.add_argument("--heartbeat", type=float, default=10.0)
    ap.add_argument("--auto", type=float, default=0.0, metavar="SEK", help="alle SEK Sekunden zufälliger Belegungswechsel")
    ap.add_argument("--commands", default=None, help="Befehle aus Datei statt Terminal lesen")
    args = ap.parse_args()

    sim = Simulator([s.strip().upper() for s in args.slots.split(",") if s.strip()], args.heartbeat)
    print(json.dumps({"type": "hello", "fw": "simulator"}), flush=True)
    for slot in sim.slots.values():
        sim.emit(slot)

    running = threading.Event()
    running.set()

    def read_commands():
        try:
            src = open(args.commands) if args.commands else open("/dev/tty")
        except OSError:
            src = sys.stdin
        print("Simulator bereit. Befehle: p|b|s|e <Platz>, q", file=sys.stderr)
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
