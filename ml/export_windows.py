#!/usr/bin/env python3
"""Export real recorded runs from the platform database as training/test data (plan 8.2).

Recording procedure:
  1. Note the start time, perform one complete run (e.g. "park, leave for 30 s, bump lightly,
     take the bike out"), note the end time.
  2. Export that period with a unique run id and a label::

     python3 ml/export_windows.py --db server/bike_station.db --station st_... --slot A \\
         --since 2026-10-01T10:02:00 --until 2026-10-01T10:03:30 \\
         --label normal --run-id r07-bump

  Label: "normal"    (empty, parking, parked, light bump, taking the bike out)
         "anomalous" (controlled simulation of repeated strong movement of the demo object)
  Never instruct anybody to actually steal or damage a bike!

``--station`` is the station ID shown in the portal URL (``#/stations/st_...``); ``--slot`` is the
slot key used by the Arduino (e.g. ``A``). Without ``--station`` the slot key must be unique.
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
        return datetime.fromisoformat(s).timestamp()  # without a time zone = local time


def resolve_slot(con: sqlite3.Connection, station: str | None, key: str) -> tuple[str, str]:
    """Slot key (+ optional station ID) -> (internal slot ID, station ID)."""
    sql = "SELECT s.id, s.station_id FROM slot s WHERE s.key = ?"
    params: list = [key]
    if station:
        sql += " AND s.station_id = ?"
        params.append(station)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        raise SystemExit(f"Slot {key!r} not found" + (f" at station {station!r}" if station else ""))
    if len(rows) > 1:
        raise SystemExit(f"Slot key {key!r} exists at several stations - pass --station")
    return rows[0][0], rows[0][1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--station", help="station ID (st_...)")
    ap.add_argument("--slot", required=True, help="slot key as used by the Arduino, e.g. A")
    ap.add_argument("--since", required=True, help="ISO time (local) or Unix time")
    ap.add_argument("--until", required=True)
    ap.add_argument("--label", required=True, choices=["normal", "anomalous"])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", default=str(ML_DIR / "data" / "recorded.csv"))
    args = ap.parse_args()

    since, until = parse_time(args.since), parse_time(args.until)
    params = load_params()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    slot_id, station_id = resolve_slot(con, args.station, args.slot)
    rows = con.execute(
        "SELECT server_time, occupied, vibration_score, source FROM measurement "
        "WHERE slot_id = ? AND sensor_state = 'ok' AND server_time <= ? ORDER BY server_time",
        (slot_id, until),
    ).fetchall()
    history = [(t, bool(o), s) for t, o, s, _ in rows]
    sources = {src for t, _, _, src in rows if t >= since}
    source = "simulated" if "simulated" in sources else "live"

    out = []
    for t, feats in trigger_rows(history, params):
        if t < since:
            continue
        out.append({"run_id": args.run_id, "label": args.label, "source": source, "slot_id": args.slot,
                    "t": round(t - since, 3), **feats})
    if not out:
        print("No evaluable measurements (occupied + vibration > 0) found in this period.")
        return
    write_rows(Path(args.out), out)
    print(f"{len(out)} feature windows from run {args.run_id!r} ({args.label}, {source}, station {station_id}) -> {args.out}")


if __name__ == "__main__":
    main()
