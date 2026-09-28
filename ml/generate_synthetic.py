#!/usr/bin/env python3
"""SIMULIERTE Beispieldurchläufe erzeugen – nur, um die ML-Pipeline ohne Hardware zu testen.

Diese Daten sagen NICHTS über echte Diebstähle oder den echten Sensor aus. Alle Zeilen
tragen source="simulated"; ein damit trainiertes Modell wird im Dashboard/Report als
auf simulierten Daten trainiert ausgewiesen. Für die Abschlussdemo echte Durchläufe mit
export_windows.py aufnehmen (Plan 8.2).
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from common import ML_DIR, load_params, trigger_rows, write_rows

HB = 10.0  # Heartbeat


def timeline(kind: str, rng: random.Random):
    """Erzeugt (zeit, belegt, score)-Historie eines Durchlaufs."""
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

    # Jeder Lauf beginnt mit einem leeren Platz und dem Einstellen.
    h.append((t, False, 0))
    hb(rng.uniform(5, 15), False)
    h.append((t, True, rng.randint(150, 380)))  # Einstellen wackelt
    vib(rng.randint(0, 3), 100, 420, (0.4, 0.9))
    park = rng.uniform(25, 90)
    hb(t + park, True)

    if kind == "geparkt":
        pass
    elif kind == "anstossen":
        vib(1, 250, 480, (0.1, 0.5))
    elif kind == "vorbeigehen":
        vib(rng.randint(1, 3), 20, 200, (0.4, 1.5))
    elif kind == "nachbar":  # Nachbarplatz wird benutzt -> leichte Übertragung
        vib(rng.randint(2, 4), 60, 280, (0.4, 0.9))
    elif kind == "wind":
        vib(rng.randint(3, 6), 10, 150, (0.8, 2.5))
    elif kind == "ruetteln":  # kontrolliert simuliertes starkes, wiederholtes Bewegen
        vib(rng.randint(5, 10), 500, 1000, (0.3, 0.7))
    elif kind == "zerren":  # weniger stark, aber lange anhaltend
        vib(rng.randint(8, 14), 250, 600, (0.4, 1.0))
    hb(t + rng.uniform(5, 20), True)

    # Ausparken
    vib(rng.randint(1, 3), 150, 450, (0.3, 0.8))
    t += 1.0
    h.append((t, False, rng.randint(100, 300)))
    hb(t + 12, False)
    return h


NORMAL = ["geparkt", "anstossen", "vorbeigehen", "nachbar", "wind"]
ANOMAL = ["ruetteln", "zerren"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--normal-runs", type=int, default=150)
    ap.add_argument("--anomal-runs", type=int, default=30)
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
        ("anomal", rng.choice(ANOMAL)) for _ in range(args.anomal_runs)
    ]
    for i, (label, kind) in enumerate(plan):
        run_id = f"sim-{i:04d}-{kind}"
        for t, feats in trigger_rows(timeline(kind, rng), params):
            rows.append({"run_id": run_id, "label": label, "source": "simulated", "t": round(t, 3), **feats})
    write_rows(out, rows)
    print(f"{len(plan)} SIMULIERTE Läufe, {len(rows)} Merkmalsfenster -> {out}")


if __name__ == "__main__":
    main()
