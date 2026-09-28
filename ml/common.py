"""Shared helpers for the ML scripts. Uses the SAME feature computation as the server."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ML_DIR = Path(__file__).resolve().parent
REPO = ML_DIR.parent
sys.path.insert(0, str(REPO / "server"))

from app.anomaly import FEATURE_NAMES, DetectorParams, features_at, rule_decision  # noqa: E402

__all__ = [
    "FEATURE_NAMES",
    "DetectorParams",
    "features_at",
    "rule_decision",
    "load_params",
    "write_rows",
    "read_rows",
    "CSV_FIELDS",
    "DEFAULT_MODEL",
]

CSV_FIELDS = ["run_id", "label", "source", "slot_id", "t", *FEATURE_NAMES]
DEFAULT_MODEL = ML_DIR / "models" / "vibration_iforest.joblib"


def load_params(config: str | Path | None = None) -> DetectorParams:
    import tomllib

    path = Path(config) if config else REPO / "server" / "config.toml"
    with open(path, "rb") as f:
        an = tomllib.load(f).get("anomaly", {})
    return DetectorParams(
        window_s=float(an.get("window_s", 10)),
        peak_threshold=int(an.get("peak_threshold", 300)),
        min_peaks=int(an.get("min_peaks", 3)),
        grace_period_s=float(an.get("grace_period_s", 15)),
    )


def trigger_rows(history, params: DetectorParams):
    """As in live operation: every measurement at an occupied space with vibration > 0 is evaluated."""
    for i, (t, occ, score) in enumerate(history):
        if occ and score > 0:
            yield t, features_at(history, i, params)


def write_rows(path: Path, rows: list[dict]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def read_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, newline="") as f:
            for r in csv.DictReader(f):
                if r["label"] == "anomal":  # label used by older exports
                    r["label"] = "anomalous"
                if r["label"] not in ("normal", "anomalous"):
                    raise SystemExit(f"{p}: unknown label {r['label']!r} (expected normal or anomalous)")
                for k in ("t", *FEATURE_NAMES):
                    r[k] = float(r[k])
                rows.append(r)
    return rows
