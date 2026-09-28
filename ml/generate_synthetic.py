#!/usr/bin/env python3
"""Generate SIMULATED example runs - only to test the ML pipeline without hardware.

This data says NOTHING about real thefts or the real sensor. Every row carries
source="simulated"; a model trained on it is labelled as such in the dashboard and the report.
For the final demo record real runs with export_windows.py (plan 8.2).
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from common import ML_DIR, load_params, trigger_rows, write_rows

HB = 10.0  # heartbeat interval (s)


def timeline(kind: str, rng: random.Random):
    """Creates the (time, occupied, score) history of one run."""
    h: list[tuple[float, bool, int]] = []
    t = 0.0

    def hb(until: float, occ: bool):
        nonlocal t
        while t + HB < until:
            t += HB
            h.append((t, occ, 0))
        t = until

    def vib(n: int, lo: int, hi: int, step: tuple[float, float], occ=True):
        nonlocal t
        for _ in range(n):
            t += rng.uniform(*step)
            h.append((t, occ, rng.randint(lo, hi)))

    # Every run starts with an empty space and a bike being parked.
    h.append((t, False, 0))
    hb(rng.uniform(5, 15), False)
    h.append((t, True, rng.randint(150, 380)))  # parking wobbles
    vib(rng.randint(0, 3), 100, 420, (0.4, 0.9))
    park = rng.uniform(25, 90)
    hb(t + park, True)

    if kind == "parked":
        pass
    elif kind == "bump":
        vib(1, 250, 480, (0.1, 0.5))
    elif kind == "passer_by":
        vib(rng.randint(1, 3), 20, 200, (0.4, 1.5))
    elif kind == "neighbour":  # neighbouring space in use -> slight transmission
        vib(rng.randint(2, 4), 60, 280, (0.4, 0.9))
    elif kind == "wind":
        vib(rng.randint(3, 6), 10, 150, (0.8, 2.5))
    elif kind == "shaking":  # controlled simulation of strong, repeated movement
        vib(rng.randint(5, 10), 500, 1000, (0.3, 0.7))
    elif kind == "tugging":  # less strong, but long lasting
        vib(rng.randint(8, 14), 250, 600, (0.4, 1.0))
    hb(t + rng.uniform(5, 20), True)

    # leaving
    vib(rng.randint(1, 3), 150, 450, (0.3, 0.8))
    t += 1.0
    h.append((t, False, rng.randint(100, 300)))
    hb(t + 12, False)
    return h


NORMAL = ["parked", "bump", "passer_by", "neighbour", "wind"]
ANOMALOUS = ["shaking", "tugging"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--normal-runs", type=int, default=150)
    ap.add_argument("--anomalous-runs", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(ML_DIR / "data" / "synthetic.csv"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    params = load_params()
    out = Path(args.out)
    if out.exists():
        out.unlink()
    rows = []
    plan = [("normal", rng.choice(NORMAL)) for _ in range(args.normal_runs)] + [
        ("anomalous", rng.choice(ANOMALOUS)) for _ in range(args.anomalous_runs)
    ]
    for i, (label, kind) in enumerate(plan):
        run_id = f"sim-{i:04d}-{kind}"
        for t, feats in trigger_rows(timeline(kind, rng), params):
            rows.append({"run_id": run_id, "label": label, "source": "simulated", "slot_id": "A", "t": round(t, 3), **feats})
    write_rows(out, rows)
    print(f"{len(plan)} SIMULATED runs, {len(rows)} feature windows -> {out}")


if __name__ == "__main__":
    main()
