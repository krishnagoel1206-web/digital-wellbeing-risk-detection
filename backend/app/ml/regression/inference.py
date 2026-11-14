"""INFERENCE for the screen-time forecaster.

This module only LOADS artifacts produced by train_forecast.py and calls
predict(). It never fits anything. If the artifact is missing it raises
ModelNotTrained, which the API turns into an explicit "not trained" response
so the app can hide the forecast rather than display a fabricated number.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Any

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings
from app.ml.regression.train_forecast import (
    ARTIFACT_NAME,
    FEATURE_COLUMNS,
    build_supervised_frame,
    drop_incomplete_today,
)


class ModelNotTrained(Exception):
    """Raised when the forecaster has not been trained yet."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"model '{name}' has not been trained. Run: "
            "python scripts/train_models.py --only regression"
        )


@dataclass
class Forecast:
    """One predicted day."""

    date: str
    predicted_minutes: float
    #: True once the prediction depends on earlier predictions rather than
    #: only on observed data - uncertainty compounds from that point on.
    is_recursive: bool
    #: True when this prediction covers the CURRENT date, for which partial
    #: actuals already exist. The forecast is for the whole day; the figure the
    #: Usage screen shows is the day so far. Both are correct and they are not
    #: the same number, so the app must label this one.
    is_current_day: bool = False


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any]:
    """Load and cache the fitted pipeline.

    Cached because unpickling on every request would be wasteful. Call
    `reset_cache()` after retraining.
    """
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    if not path.exists():
        raise ModelNotTrained(ARTIFACT_NAME)
    return joblib.load(path)


def reset_cache() -> None:
    """Drop the cached artifact so a freshly trained model is picked up."""
    _load_artifact.cache_clear()


def is_trained() -> bool:
    """Whether a usable artifact exists on disk."""
    settings = get_settings()
    return (settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib").exists()


def load_metrics() -> dict | None:
    """Return the metrics recorded at training time, or None if absent.

    These are READ from disk, never recomputed here. That guarantees the app
    shows the scores the shipped model actually achieved.
    """
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def forecast_next_days(
    daily: pd.DataFrame,
    *,
    horizon: int = 7,
    predictor: Callable[[np.ndarray], float] | None = None,
) -> list[Forecast]:
    """Forecast the next `horizon` days of screen time.

    Only the FIRST predicted day rests purely on observed history. Each
    subsequent day must use the previous prediction as its lag_1, so errors
    compound - every such day is flagged `is_recursive=True` so the app can
    present later days with appropriate caution rather than equal confidence.
    """
    # `predictor` maps one feature row to minutes. Without one, the shipped
    # population-fitted artifact is used; the personal forecaster (fitted on
    # the phone from the user's real days) passes its own.
    if predictor is None:
        pipeline = _load_artifact()["pipeline"]

        def predictor(row: np.ndarray) -> float:
            return float(pipeline.predict(row.reshape(1, -1))[0])

    if daily.empty:
        return []

    # A day still in progress must not drive a forecast. Its total sits at
    # lag_1, where it has the largest single influence on the prediction - on
    # real data a 7-minute morning dragged the next day from 8.7 h down to
    # 5.0 h. See drop_incomplete_today() for why the rule is unconditional.
    #
    # One consequence is deliberate: with today removed, the first forecast
    # day IS today, predicting the complete day it is going to become. That is
    # the most useful thing the model can say, and `is_current_day` marks it so
    # the app never shows it as if it were a measurement.
    work, _partial_today = drop_incomplete_today(daily)
    if work.empty:
        return []

    work = work.copy()
    work["record_date"] = pd.to_datetime(work["record_date"])
    work = work.sort_values("record_date").reset_index(drop=True)

    today = dt.date.today()

    results: list[Forecast] = []
    last_date = work["record_date"].iloc[-1]

    for step in range(horizon):
        target_date = last_date + pd.Timedelta(days=1)

        # Append a placeholder row for the day being predicted, so the
        # feature builder computes its lags from the history behind it.
        extended = pd.concat(
            [
                work[["record_date", "total_screen_minutes"]],
                pd.DataFrame(
                    {"record_date": [target_date], "total_screen_minutes": [np.nan]}
                ),
            ],
            ignore_index=True,
        )

        target = extended["total_screen_minutes"].astype("float64")
        row: dict[str, float] = {}
        for lag in (1, 2, 3, 7):
            row[f"lag_{lag}"] = float(target.shift(lag).iloc[-1])

        prior = target.shift(1)
        row["roll_mean_3"] = float(prior.rolling(3, min_periods=3).mean().iloc[-1])
        row["roll_mean_7"] = float(prior.rolling(7, min_periods=7).mean().iloc[-1])
        row["roll_std_7"] = float(prior.rolling(7, min_periods=7).std().iloc[-1])
        row["day_of_week"] = float(target_date.weekday())
        row["is_weekend"] = 1.0 if target_date.weekday() >= 5 else 0.0

        features = np.array([[row[c] for c in FEATURE_COLUMNS]], dtype=float)
        if not np.isfinite(features).all():
            # Not enough history to populate every lag - stop rather than
            # impute, because an imputed lag would be an invented observation.
            break

        predicted = float(predictor(features[0]))
        # Screen time cannot be negative or exceed a day.
        predicted = max(0.0, min(1440.0, predicted))

        results.append(
            Forecast(
                date=target_date.date().isoformat(),
                predicted_minutes=round(predicted, 1),
                is_recursive=step > 0,
                is_current_day=target_date.date() == today,
            )
        )

        # Feed the prediction back in so the next step has a lag_1.
        work = pd.concat(
            [
                work,
                pd.DataFrame(
                    {
                        "record_date": [target_date],
                        "total_screen_minutes": [predicted],
                    }
                ),
            ],
            ignore_index=True,
        )
        last_date = target_date

    return results


def actual_vs_predicted(daily: pd.DataFrame) -> list[dict]:
    """One-step-ahead predictions over the observed history.

    Used for the "actual versus predicted" chart. Note these are IN-SAMPLE
    for the final model (it was refitted on all data), so they look better
    than the cross-validated error. The API labels them accordingly, and the
    honest out-of-sample figures remain those in the metrics file.
    """
    artifact = _load_artifact()
    pipeline = artifact["pipeline"]

    # Today has no complete actual to compare a prediction against, so
    # plotting it would show the model missing a target that does not exist
    # yet. Dropped here for the same reason it is dropped from training.
    daily, _partial_today = drop_incomplete_today(daily)

    supervised = build_supervised_frame(daily)
    if supervised.empty:
        return []

    X = supervised[FEATURE_COLUMNS].to_numpy(dtype=float)
    predicted = pipeline.predict(X)

    return [
        {
            "date": row.record_date.date().isoformat()
            if isinstance(row.record_date, (pd.Timestamp, dt.datetime))
            else str(row.record_date),
            "actual_minutes": float(row.target),
            "predicted_minutes": round(float(pred), 1),
        }
        for row, pred in zip(supervised.itertuples(index=False), predicted)
    ]
