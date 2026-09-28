#!/usr/bin/env python3
"""Train an Isolation Forest and compare it honestly with the baseline rule (plan 8.1, 8.2).

  * Train/test split by COMPLETE runs (run_id), never by single windows, so that nearly identical
    consecutive windows never end up in both groups.
  * Training on normal runs only (the model learns "normal").
  * Rule and model are evaluated on the same test runs: how many anomalous runs are detected?
    How many normal runs cause an unnecessary warning?
  * Result as a Markdown report (ml/report.md) with case counts and data source.

    python3 ml/train.py ml/data/recorded.csv            # real recordings
    python3 ml/train.py ml/data/synthetic.csv           # pipeline test only (SIMULATED)
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
    res = {"normal": [0, 0], "anomalous": [0, 0]}  # [runs with a warning, runs in total]
    windows = {"normal": [0, 0], "anomalous": [0, 0]}
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
                    help="expected share of unusual windows in normal training data")
    ap.add_argument("--model-out", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--report", type=Path, default=ML_DIR / "report.md")
    args = ap.parse_args()

    from sklearn.ensemble import IsolationForest  # imported here: only needed for training
    import joblib

    params = load_params()
    rows = read_rows(args.csv)
    if not rows:
        raise SystemExit("No data.")
    runs: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        runs[r["run_id"]].append(r)
    sources = sorted({r["source"] for r in rows})
    simulated = "simulated" in sources

    train_ids, test_ids = split_runs(runs, args.test_frac, args.seed)
    # As in operation: nothing is evaluated during the grace period, so nothing is trained on it either.
    X = [
        [r[f] for f in FEATURE_NAMES]
        for rid in train_ids
        for r in runs[rid]
        if r["label"] == "normal" and r["since_change_s"] >= params.grace_period_s
    ]
    if len(X) < 10:
        raise SystemExit(f"Too few normal training windows ({len(X)}). Record more runs.")

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

    def score(ev):  # detected anomalous runs minus unnecessary warnings
        return ev["runs"]["anomalous"][0] - ev["runs"]["normal"][0]

    go = score(ev_ml) > score(ev_rule)
    recommendation = (
        "The AI beats the rule on this test data -> `alert_source = \"ml\"` is possible."
        if go
        else "The AI is NOT better than the rule -> visible warnings stay with the rule "
        "(`alert_source = \"rule\"`); present the AI as a research feature."
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
        "# AI comparison: Isolation Forest vs. baseline rule",
        "",
        f"- Created: {trained_at}",
        f"- Data source: **{', '.join(sources)}**"
        + ("  \n  **WARNING: simulated data - feasibility demo only, says nothing about real situations.**" if simulated else ""),
        f"- Runs in total: {len(runs)} (training {n_train_runs}, test {n_test_runs}); split by complete runs",
        f"- Training windows (normal only, after the grace period): {len(X)}",
        f"- Rule: at least {params.min_peaks} peaks ≥ {params.peak_threshold} within {params.window_s:g} s, "
        f"grace period {params.grace_period_s:g} s",
        f"- Model: IsolationForest(n_estimators=200, contamination={args.contamination})",
        "",
        "## Result on the test runs",
        "",
        "| Method | Anomalous runs detected | Normal runs with an unnecessary warning | Anomalous windows flagged | Normal windows flagged |",
        "|---|---|---|---|---|",
    ]
    for name, ev in (("Rule (baseline)", ev_rule), ("AI (Isolation Forest)", ev_ml)):
        lines.append(
            f"| {name} | {pct(*ev['runs']['anomalous'])} | {pct(*ev['runs']['normal'])} | "
            f"{pct(*ev['windows']['anomalous'])} | {pct(*ev['windows']['normal'])} |"
        )
    lines += [
        "",
        f"**Go/No-Go:** {recommendation}",
        "",
        "## Limitations",
        "",
        "- An anomaly only means: *this pattern was unusual compared with the measurements so far.*",
        "- A few hackathon recordings are not enough for reliable statements about real thefts.",
        "- A new bike, people passing by, wind or taking a bike out can cause false alarms.",
        "- The time until a warning is shown is measured in the live test (T05), not here.",
        "",
    ]
    args.report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"Model saved: {args.model_out}")


if __name__ == "__main__":
    main()
