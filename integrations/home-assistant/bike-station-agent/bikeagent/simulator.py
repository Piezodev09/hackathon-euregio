"""Arduino simulator - ONLY for development, demos without hardware and tests.

Prints lines in exactly the same format as the Arduino sketch. The agent labels all data from
the simulator as ``simulated``; the portal and the kiosk show that label visibly
("never pass off a simulated run as a live sensor").

Built into the agent (``--source simulator``) or as a separate process::

    python3 -m bikeagent.simulator | python3 -m bikeagent run --source stdin

Interactive commands (type + Enter in the terminal):
    p A    park / remove a bike at slot A
    b A    light bump at slot A (one peak)
    s A    strong, repeated shaking at slot A (about 3 s)
    e A    toggle a sensor fault at slot A
    q      quit
With ``--auto N`` occupancy changes randomly about every N seconds (fills the heatmap).
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
        self.pending_vib: list[tuple[float, int]] = []  # (due time, strength)
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
        """Apply one interactive command. Returns False on quit."""
        parts = cmd.strip().split()
        if not parts:
            return True
        if parts[0] == "q":
            return False
        if len(parts) != 2 or parts[1].upper() not in self.slots or parts[0] not in ("p", "b", "s", "e"):
            print(f"? usage: p|b|s|e <{'/'.join(self.slots)}> or q", file=sys.stderr)
            return True
        op, slot = parts[0], self.slots[parts[1].upper()]
        now = time.monotonic()
        with self.lock:
            if op == "p":
                # Parking wobbles the stand a little - realistic for the grace period.
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
    ap.add_argument("--slots", default="A,B,C", help="Arduino slot ids, comma separated")
    ap.add_argument("--heartbeat", type=float, default=10.0)
    ap.add_argument("--auto", type=float, default=0.0, metavar="SEC", help="random occupancy change about every SEC seconds")
    ap.add_argument("--commands", default=None, help="read commands from a file instead of the terminal")
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
        print("Simulator ready. Commands: p|b|s|e <slot>, q", file=sys.stderr)
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
