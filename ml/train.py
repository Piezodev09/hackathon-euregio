#!/usr/bin/env python3
"""Isolation Forest trainieren und ehrlich mit der Baseline-Regel vergleichen (Plan 8.1, 8.2).

  * Aufteilung in Training/Test nach KOMPLETTEN Durchläufen (run_id), nie nach Einzelfenstern,
    damit fast identische, aufeinanderfolgende Fenster nicht in beiden Gruppen landen.
  * Training nur auf normalen Läufen (das Modell lernt "normal").
  * Regel und Modell werden auf denselben Testläufen bewertet: Wie viele auffällige Läufe
    werden erkannt? Wie viele normale Läufe lösen eine unnötige Warnung aus?
  * Ergebnis als Markdown-Report (ml/report.md) mit Anzahl der Fälle und Datenquelle.

    python ml/train.py ml/data/recorded.csv            # echte Aufnahmen
    python ml/train.py ml/data/synthetic.csv           # nur Pipeline-Test (SIMULIERT)
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from common import DEFAULT_MODEL, FEATURE_NAMES, ML_DIR, load_params, read_rows, rule_decision


def split_runs(runs: dict[str, list[dict]], test_frac: float, seed: int):
    rng = random.Random(seed)
    by_label = defaultdict(list)
    for rid, rows in runs.items():
        by_label[rows[0]["label"]].append(rid)
    train, test = set(), set()
    for label, ids in by_label.items():
        ids = sorted(ids)
        rng.shuffle(ids)
        n_test = max(1, round(len(ids) * test_frac)) if len(ids) > 1 else 0
        test.update(ids[:n_test])
        train.update(ids[n_test:])
    return train, test


def evaluate(runs, ids, decide) -> dict:
    res = {"normal": [0, 0], "anomal": [0, 0]}  # [Läufe mit Warnung, Läufe gesamt]
    windows = {"normal": [0, 0], "anomal": [0, 0]}
    for rid in ids:
        rows = runs[rid]
        label = rows[0]["label"]
        flags = [decide(r) for r in rows]
        res[label][0] += any(flags)
        res[label][1] += 1
        windows[label][0] += sum(flags)
        windows[label][1] += len(flags)
    return {"runs": res, "windows": windows}


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f} %)" if b else "0/0 (–)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+", type=Path)
    ap.add_argument("--test-frac", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--contamination", type=float, default=0.02,
                    help="erwarteter Anteil ungewöhnlicher Fenster in normalen Trainingsdaten")
    ap.add_argument("--model-out", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--report", type=Path, default=ML_DIR / "report.md")
    args = ap.parse_args()

    from sklearn.ensemble import IsolationForest  # erst hier importieren: nur fürs Training nötig
    import joblib

    params = load_params()
    rows = read_rows(args.csv)
    if not rows:
        raise SystemExit("Keine Daten.")
    runs: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        runs[r["run_id"]].append(r)
    sources = sorted({r["source"] for r in rows})
    simulated = "simulated" in sources

    train_ids, test_ids = split_runs(runs, args.test_frac, args.seed)
    # Wie im Betrieb: in der Schonzeit wird nicht bewertet, also auch nicht darauf trainiert.
    X = [
        [r[f] for f in FEATURE_NAMES]
        for rid in train_ids
        for r in runs[rid]
        if r["label"] == "normal" and r["since_change_s"] >= params.grace_period_s
    ]
    if len(X) < 10:
        raise SystemExit(f"Zu wenige normale Trainingsfenster ({len(X)}). Mehr Durchläufe aufnehmen.")

    model = IsolationForest(n_estimators=200, contamination=args.contamination, random_state=args.seed)
    model.fit(X)

    def rule(r):
        return rule_decision(r, params)

    def ml(r):
        if r["since_change_s"] < params.grace_period_s:
            return False
        return model.predict([[r[f] for f in FEATURE_NAMES]])[0] == -1

    ev_rule = evaluate(runs, test_ids, rule)
    ev_ml = evaluate(runs, test_ids, ml)

    def score(ev):  # erkannte auffällige Läufe minus unnötige Warnungen
        return ev["runs"]["anomal"][0] - ev["runs"]["normal"][0]

    go = score(ev_ml) > score(ev_rule)
    recommendation = (
        "KI ist auf diesen Testdaten besser als die Regel -> `alert_source = \"ml\"` möglich."
        if go
        else "KI ist NICHT besser als die Regel -> sichtbare Warnung bleibt bei der Regel "
        "(`alert_source = \"rule\"`); KI als Forschungsfunktion vorführen."
    )

    n_train_runs = len(train_ids)
    n_test_runs = len(test_ids)
    trained_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = {
        "features": list(FEATURE_NAMES),
        "trained_at": trained_at,
        "data_sources": sources,
        "trained_on_simulated_data": simulated,
        "n_train_windows": len(X),
        "n_train_runs": n_train_runs,
        "n_test_runs": n_test_runs,
        "contamination": args.contamination,
        "params": params.__dict__,
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, **meta, "features": tuple(FEATURE_NAMES)}, args.model_out)

    lines = [
        "# KI-Vergleich: Isolation Forest vs. Baseline-Regel",
        "",
        f"- Erstellt: {trained_at}",
        f"- Datenquelle: **{', '.join(sources)}**"
        + ("  \n  **ACHTUNG: Simulierte Daten – nur Machbarkeitsdemo, keine Aussage über echte Situationen.**" if simulated else ""),
        f"- Läufe gesamt: {len(runs)} (Training {n_train_runs}, Test {n_test_runs}); Aufteilung nach ganzen Läufen",
        f"- Trainingsfenster (nur normal, nach Schonzeit): {len(X)}",
        f"- Regel: mindestens {params.min_peaks} Ausschläge ≥ {params.peak_threshold} in {params.window_s:g} s, "
        f"Schonzeit {params.grace_period_s:g} s",
        f"- Modell: IsolationForest(n_estimators=200, contamination={args.contamination})",
        "",
        "## Ergebnis auf den Testläufen",
        "",
        "| Verfahren | Auffällige Läufe erkannt | Normale Läufe mit unnötiger Warnung | Auffällige Fenster | Normale Fenster markiert |",
        "|---|---|---|---|---|",
    ]
    for name, ev in (("Regel (Baseline)", ev_rule), ("KI (Isolation Forest)", ev_ml)):
        lines.append(
            f"| {name} | {pct(*ev['runs']['anomal'])} | {pct(*ev['runs']['normal'])} | "
            f"{pct(*ev['windows']['anomal'])} | {pct(*ev['windows']['normal'])} |"
        )
    lines += [
        "",
        f"**Go/No-Go:** {recommendation}",
        "",
        "## Grenzen",
        "",
        "- Eine Anomalie heißt nur: *Dieses Muster war in den bisherigen Messungen ungewöhnlich.*",
        "- Wenige Hackathon-Messungen reichen nicht für belastbare Aussagen über echte Diebstähle.",
        "- Neues Fahrrad, vorbeigehende Personen, Wind oder Ausparken können Fehlalarme auslösen.",
        "- Die Zeit bis zur Anzeige wird im Live-Test (T05) gemessen, nicht hier.",
        "",
    ]
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Modell gespeichert: {args.model_out}")


if __name__ == "__main__":
    main()
