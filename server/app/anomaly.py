"""Detection of unusual movement at an occupied bike space (project plan chapter 8).

Two methods work on the SAME features:
  * baseline rule: several clear peaks at a space that has been occupied for a while
  * AI: Isolation Forest (scikit-learn), trained with ml/train.py on mostly normal,
    self-recorded runs

An anomaly only means: "this pattern was unusual compared with the measurements so far".
It is NOT proof of theft and says nothing about people.

The feature computation is shared with ml/train.py and ml/export_windows.py so that training
and live operation are guaranteed to compute identical features.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = (
    "n_peaks",            # messages with vibration_score >= peak_threshold in the window
    "n_active",           # messages with vibration_score > 0 in the window
    "max_score",          # strongest peak in the window
    "mean_active_score",  # mean strength of the active messages
    "since_change_s",     # seconds since the last occupancy change (capped)
)
SINCE_CHANGE_CAP_S = 600.0


@dataclass(frozen=True)
class DetectorParams:
    window_s: float = 10
    peak_threshold: int = 300
    min_peaks: int = 3
    grace_period_s: float = 15


def compute_features(
    scores: Sequence[int],
    since_change_s: float,
    params: DetectorParams,
) -> dict[str, float]:
    """Features from the vibration scores of one (already filtered) time window."""
    active = [s for s in scores if s > 0]
    return {
        "n_peaks": float(sum(1 for s in scores if s >= params.peak_threshold)),
        "n_active": float(len(active)),
        "max_score": float(max(scores, default=0)),
        "mean_active_score": float(sum(active) / len(active)) if active else 0.0,
        "since_change_s": float(min(max(since_change_s, 0.0), SINCE_CHANGE_CAP_S)),
    }


def feature_vector(features: dict[str, float]) -> list[float]:
    return [features[n] for n in FEATURE_NAMES]


def rule_decision(features: dict[str, float], params: DetectorParams) -> bool:
    """Baseline: clear shocks at a space that has been occupied for a while."""
    if features["since_change_s"] < params.grace_period_s:
        return False  # grace period after parking / leaving
    return features["n_peaks"] >= params.min_peaks


class MlDetector:
    """Loads an Isolation Forest model if present. Without a model the AI is "not available"."""

    def __init__(self, model_path: Path | None):
        self.model_path = model_path
        self.model = None
        self.meta: dict = {}
        self.error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.model_path:
            self.error = "no model path configured"
            return
        if not Path(self.model_path).exists():
            self.error = "model not trained (run ml/train.py)"
            return
        try:
            import joblib  # only needed when a model exists

            bundle = joblib.load(self.model_path)
            if tuple(bundle.get("features", ())) != FEATURE_NAMES:
                raise ValueError("model features do not match this version")
            self.model = bundle["model"]
            self.meta = {k: v for k, v in bundle.items() if k != "model"}
            self.error = None
            log.info("AI model loaded: %s", self.model_path)
        except Exception as exc:  # an AI failure must never disturb occupancy detection
            self.model = None
            self.error = f"model could not be loaded: {type(exc).__name__}"
            log.warning("AI model not available: %s", exc)

    @property
    def available(self) -> bool:
        return self.model is not None

    def decision(self, features: dict[str, float], params: DetectorParams) -> bool | None:
        """True = unusual, False = normal, None = AI not available."""
        if self.model is None:
            return None
        if features["since_change_s"] < params.grace_period_s:
            return False
        try:
            return bool(self.model.predict([feature_vector(features)])[0] == -1)
        except Exception as exc:
            log.warning("AI evaluation failed: %s", exc)
            return None


def features_at(history: Sequence[tuple[float, bool, int]], i: int, params: DetectorParams) -> dict[str, float]:
    """Features for row i of the time-ordered history of one slot.

    history: (time, occupied, vibration_score) - valid measurements only (sensor_state ok).
    Used in live operation (service.py) and when exporting training data (ml/).
    """
    t, occ, _ = history[i]
    scores = [s for (tt, _, s) in history[: i + 1] if t - params.window_s < tt <= t]
    since = t
    for tt, o, _ in reversed(history[: i + 1]):
        if o != occ:
            break
        since = tt
    return compute_features(scores, t - since, params)
