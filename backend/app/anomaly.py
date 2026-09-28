"""Erkennung auffälliger Bewegungen (Plan Kapitel 8).

Zwei Verfahren arbeiten auf DENSELBEN Merkmalen:
  * Baseline-Regel: mehrere deutliche Ausschläge an einem länger belegten Platz.
  * KI: Isolation Forest (scikit-learn), trainiert mit ml/train.py auf überwiegend
    normalen, selbst gemessenen Abläufen.

Eine Anomalie bedeutet nur: "Dieses Muster war in den bisherigen Messungen
ungewöhnlich." Sie ist KEIN Diebstahlnachweis.

Die Merkmalsberechnung wird auch von ml/train.py und ml/evaluate.py verwendet,
damit Training und Betrieb garantiert gleich rechnen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

FEATURE_NAMES: tuple[str, ...] = (
    "n_peaks",            # Meldungen mit vibration_score >= peak_threshold im Fenster
    "n_active",           # Meldungen mit vibration_score > 0 im Fenster
    "max_score",          # stärkster Ausschlag im Fenster
    "mean_active_score",  # mittlere Stärke der aktiven Meldungen
    "since_change_s",     # Sekunden seit letztem Belegungswechsel (gekappt)
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
    """Merkmale aus den vibration_scores eines Zeitfensters (bereits gefiltert)."""
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
    """Baseline: deutliche Erschütterungen an einem schon länger belegten Platz."""
    if features["since_change_s"] < params.grace_period_s:
        return False  # Schonzeit nach Einstellen/Ausparken
    return features["n_peaks"] >= params.min_peaks


class MlDetector:
    """Lädt ein Isolation-Forest-Modell, falls vorhanden. Fehlt es, ist die KI 'nicht verfügbar'."""

    def __init__(self, model_path: Path | None):
        self.model_path = model_path
        self.model = None
        self.meta: dict = {}
        self.error: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.model_path:
            self.error = "kein Modellpfad konfiguriert"
            return
        if not Path(self.model_path).exists():
            self.error = "Modell nicht trainiert (ml/train.py ausführen)"
            return
        try:
            import joblib  # nur nötig, wenn ein Modell existiert

            bundle = joblib.load(self.model_path)
            if tuple(bundle.get("features", ())) != FEATURE_NAMES:
                raise ValueError("Merkmale des Modells passen nicht zur aktuellen Version")
            self.model = bundle["model"]
            self.meta = {k: v for k, v in bundle.items() if k != "model"}
            self.error = None
            log.info("KI-Modell geladen: %s", self.model_path)
        except Exception as exc:  # KI-Ausfall darf die Belegung nie stören
            self.model = None
            self.error = f"Modell konnte nicht geladen werden: {type(exc).__name__}"
            log.warning("KI-Modell nicht verfügbar: %s", exc)

    @property
    def available(self) -> bool:
        return self.model is not None

    def decision(self, features: dict[str, float], params: DetectorParams) -> bool | None:
        """True = ungewöhnlich, False = normal, None = KI nicht verfügbar."""
        if self.model is None:
            return None
        if features["since_change_s"] < params.grace_period_s:
            return False
        try:
            return bool(self.model.predict([feature_vector(features)])[0] == -1)
        except Exception as exc:
            log.warning("KI-Auswertung fehlgeschlagen: %s", exc)
            return None


def features_at(history: Sequence[tuple[float, bool, int]], i: int, params: DetectorParams) -> dict[str, float]:
    """Merkmale für Zeile i einer zeitlich sortierten Historie eines Platzes.

    history: (zeit, belegt, vibration_score) – nur gültige Messungen (sensor_state ok).
    Wird im Live-Betrieb (service.py) und beim Export von Trainingsdaten (ml/) genutzt.
    """
    t, occ, _ = history[i]
    scores = [s for (tt, _, s) in history[: i + 1] if t - params.window_s < tt <= t]
    since = t
    for tt, o, _ in reversed(history[: i + 1]):
        if o != occ:
            break
        since = tt
    return compute_features(scores, t - since, params)
