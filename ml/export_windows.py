#!/usr/bin/env python3
"""Echte Messdurchläufe aus der Datenbank als Trainings-/Testdaten exportieren (Plan 8.2).

Ablauf beim Aufnehmen:
  1. Zeitpunkt notieren, einen vollständigen Durchlauf durchführen (z. B. "einstellen,
     30 s stehen lassen, leicht anstoßen, ausparken"), Endzeit notieren.
  2. Diesen Zeitraum mit eindeutiger run-id und Label exportieren:

     python ml/export_windows.py --db backend/bike_station.db --station <STATION_ID> \
         --since 2026-10-01T10:02:00 --until 2026-10-01T10:03:30 \
         --label normal --run-id r07-anstossen

  Label: "normal" (leer, einstellen, geparkt, leichtes Anstoßen, ausparken)
         "anomal" (kontrolliert simuliertes, wiederholtes starkes Bewegen des Demo-Objekts)
  Niemanden zu echtem Diebstahl oder Beschädigung anleiten!
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from common import ML_DIR, load_params, trigger_rows, write_rows


def parse_time(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return datetime.fromisoformat(s).timestamp()  # ohne Zeitzone = lokale Zeit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--station", required=True, help="Station-ID aus dem Portal (eine Station = ein Stellplatz)")
    ap.add_argument("--since", required=True, help="ISO-Zeit (lokal) oder Unix-Zeit")
    ap.add_argument("--until", required=True)
    ap.add_argument("--label", required=True, choices=["normal", "anomal"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default=str(ML_DIR / "data" / "recorded.csv"))
    args = ap.parse_args()

    since, until = parse_time(args.since), parse_time(args.until)
    params = load_params()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT server_time, occupied, vibration_score, source FROM measurement "
        "WHERE station_id = ? AND sensor_state = 'ok' AND server_time <= ? ORDER BY server_time",
        (args.station, until),
    ).fetchall()
    history = [(t, bool(o), s) for t, o, s, _ in rows]
    sources = {src for t, _, _, src in rows if t >= since}
    source = "simulated" if "simulated" in sources else "live"

    out = []
    for t, feats in trigger_rows(history, params):
        if t < since:
            continue
        out.append({"run_id": args.run_id, "label": args.label, "source": source,
                    "t": round(t - since, 3), **feats})
    if not out:
        print("Keine bewertbaren Messungen (belegt + Vibration > 0) im Zeitraum gefunden.")
        return
    write_rows(Path(args.out), out)
    print(f"{len(out)} Merkmalsfenster aus Lauf {args.run_id!r} ({args.label}, {source}) -> {args.out}")


if __name__ == "__main__":
    main()
