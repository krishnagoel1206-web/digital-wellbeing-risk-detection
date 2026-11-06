"""INFERENCE for the risk classifier.

Loads the artifact produced by train_risk.py and predicts. Never fits.

A NOTE ON APPLYING A POPULATION MODEL TO ONE PERSON
---------------------------------------------------
The classifier was trained on cross-sectional population data where each row
is a different person described in HOURS PER DAY. To classify this user, their
own daily records must be summarised into the same shape - an average day -
and converted from minutes to hours.

That conversion is done explicitly in `features_from_personal_history` so the
mapping is auditable, and the API reports how many days it averaged over.
Applying the model to a single day would be a category error: the model has
never seen a single day, only people's typical behaviour.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings
from app.ml.classification.train_risk import ARTIFACT_NAME
from app.ml.preprocessing.feature_dictionary import classifier_features


class ModelNotTrained(Exception):
    """Raised when the classifier has not been trained yet."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"model '{name}' has not been trained. Run: "
            "python scripts/train_models.py --only classification"
        )


class InsufficientHistory(Exception):
    """Raised when there are too few days to summarise into an average day."""

    def __init__(self, have: int, need: int) -> None:
        self.have = have
        self.need = need
        super().__init__(
            f"need at least {need} days of history to classify, have {have}"
        )


class MissingFeatures(Exception):
    """Raised when the days exist but a required measurement is absent.

    WHY THIS IS SEPARATE FROM InsufficientHistory
    ---------------------------------------------
    They are different problems with different remedies, and conflating them
    produces a genuinely confusing message. Observed in practice:

        "Not enough history to assign a persona - 7 day(s) recorded,
         7 needed."

    Seven recorded, seven needed, and still refused - because the real
    blocker was `notifications_per_day`, null on every day since Android
    keeps no notification history to backfill. "Record more days" would not
    have fixed that. Naming the missing measurement does.

    PARTIALLY missing counts too, which was the second version of the same
    mistake. Once notification access was granted, the field was present on
    exactly one day out of eight - so it was no longer missing EVERYWHERE, the
    check for that stopped firing, and the user was told "0 usable days, 7
    needed". That reads as "your days are no good", when the truth was "one
    measurement started on Thursday". `blocked_rows` carries the count so the
    message can say which, and on how many days.
    """

    def __init__(
        self,
        features: list[str],
        days: int,
        blocked_rows: dict[str, int] | None = None,
    ) -> None:
        self.features = features
        self.days = days
        self.blocked_rows = blocked_rows or {}

        if self.blocked_rows:
            parts = [
                f"{name.replace('_', ' ')} (missing on {count} of {days} days)"
                for name, count in sorted(
                    self.blocked_rows.items(), key=lambda kv: -kv[1]
                )
            ]
            super().__init__(
                f"{days} day(s) of history are available, but no single day "
                "carries every measurement this model needs. Missing: "
                + "; ".join(parts)
                + ". The model becomes available once enough days carry "
                "all of them - which depends on the measurement: notification "
                "counts accrue from the day access was granted, while a sleep "
                "estimate appears only on days with a clear period of "
                "inactivity to infer it from."
            )
        else:
            readable = ", ".join(f.replace("_", " ") for f in features)
            super().__init__(
                f"{days} day(s) of history are available, but this model also "
                f"needs: {readable}. Those measurements are missing from every "
                "recorded day, so the model cannot run."
            )


@dataclass
class RiskPrediction:
    """A predicted risk class with its class probabilities."""

    risk_class: str
    #: class -> probability. From the model, never invented.
    probabilities: dict[str, float]
    #: Highest probability, i.e. the model's confidence in its own answer.
    confidence: float
    model_name: str
    #: How many days of history were averaged to build the input.
    days_averaged: int
    #: The feature values actually fed to the model, for transparency.
    inputs: dict[str, float] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any]:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    if not path.exists():
        raise ModelNotTrained(ARTIFACT_NAME)
    return joblib.load(path)


def reset_cache() -> None:
    """Drop the cached artifact so a freshly trained model is picked up."""
    _load_artifact.cache_clear()


def is_trained() -> bool:
    settings = get_settings()
    return (settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib").exists()


def load_metrics() -> dict | None:
    """Return the metrics recorded at training time, or None if absent."""
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def features_from_personal_history(
    daily: pd.DataFrame, *, min_days: int = 7
) -> tuple[dict[str, float], int]:
    """Summarise this user's daily records into the model's input shape.

    The population model expects HOURS PER DAY for an average day, so stored
    MINUTES are divided by 60 and averaged across the available history.

    Mapping from personal columns to population columns:
        total_screen_minutes  / 60 -> daily_screen_time_hours
        social_minutes        / 60 -> social_media_hours
        productivity_minutes  / 60 -> work_study_hours
        sleep_hours                -> sleep_hours (already hours)
        weekend-only screen   / 60 -> weekend_screen_time

    `weekend_screen_time` uses only Saturday/Sunday rows where any exist,
    falling back to the overall mean otherwise - with a 7-day minimum there is
    almost always at least one weekend day.
    """
    if len(daily) < min_days:
        raise InsufficientHistory(have=len(daily), need=min_days)

    df = daily.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])

    def mean_hours(column: str) -> float:
        if column not in df.columns:
            return float("nan")
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        return float(series.mean() / 60.0) if not series.empty else float("nan")

    weekend_rows = df[df["record_date"].dt.day_of_week >= 5]
    if not weekend_rows.empty:
        weekend_series = pd.to_numeric(
            weekend_rows["total_screen_minutes"], errors="coerce"
        ).dropna()
        weekend_hours = (
            float(weekend_series.mean() / 60.0) if not weekend_series.empty else float("nan")
        )
    else:
        weekend_hours = mean_hours("total_screen_minutes")

    sleep = float("nan")
    if "sleep_hours" in df.columns:
        sleep_series = pd.to_numeric(df["sleep_hours"], errors="coerce").dropna()
        if not sleep_series.empty:
            sleep = float(sleep_series.mean())

    mapped = {
        "daily_screen_time_hours": mean_hours("total_screen_minutes"),
        "social_media_hours": mean_hours("social_minutes"),
        "weekend_screen_time": weekend_hours,
        "work_study_hours": mean_hours("productivity_minutes"),
        "sleep_hours": sleep,
    }
    return mapped, len(df)


def predict_from_personal_history(
    daily: pd.DataFrame, *, min_days: int = 7
) -> RiskPrediction:
    """Classify this user's typical behaviour.

    Raises InsufficientHistory rather than guessing from one or two days.
    """
    artifact = _load_artifact()
    pipeline = artifact["pipeline"]
    class_order: list[str] = artifact["class_order"]
    features = classifier_features()

    mapped, days = features_from_personal_history(daily, min_days=min_days)

    # A missing MEASUREMENT is not a missing DAY, and reporting one as the
    # other produces the exact message this codebase has already had to fix
    # twice: "7 day(s) recorded, 7 needed" - refused, with the stated
    # requirement met. Recording more days would not have helped; the blocker
    # was a feature that was never there.
    missing = [k for k, v in mapped.items() if not np.isfinite(v)]
    if missing:
        raise MissingFeatures(features=missing, days=days)

    X = np.array([[mapped[f] for f in features]], dtype=float)

    predicted = str(pipeline.predict(X)[0])

    probabilities: dict[str, float] = {}
    confidence = float("nan")
    if hasattr(pipeline, "predict_proba"):
        proba = pipeline.predict_proba(X)[0]
        model_classes = list(pipeline.named_steps["model"].classes_)
        probabilities = {
            str(cls): float(proba[model_classes.index(cls)])
            for cls in class_order
            if cls in model_classes
        }
        confidence = float(max(probabilities.values())) if probabilities else float("nan")

    return RiskPrediction(
        risk_class=predicted,
        probabilities=probabilities,
        confidence=confidence,
        model_name=artifact["model_name"],
        days_averaged=days,
        inputs={k: round(v, 3) for k, v in mapped.items()},
    )
