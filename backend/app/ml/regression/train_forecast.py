"""TRAINING code for next-day screen-time forecasting.

Compares Linear, Ridge and Lasso regression. This module FITS models and
writes artifacts; it is never imported by the API at request time. Inference
lives in app/ml/regression/inference.py, which only loads what this produced.

WHY TimeSeriesSplit AND NOT KFold
---------------------------------
This is a time series: each row is one day, and the rows are ordered. A
shuffled KFold would put future days in the training set and past days in the
test set, so the model would effectively see tomorrow while predicting
yesterday. Reported error would be far too optimistic.

TimeSeriesSplit only ever trains on days BEFORE the test fold, which is the
situation the model actually faces in production.

WHY THE FEATURES ARE ALL LAGGED
-------------------------------
To predict day N's screen time we may use only information available before
day N. Every predictor is therefore a lag or a rolling statistic of PRIOR
days. Using day N's own notification count would be leakage - we would not
know it in advance.

WHICH DATA THE MODEL WAS FITTED ON IS PART OF THE MODEL
-------------------------------------------------------
This model forecasts ONE PERSON's screen time, so where its training rows came
from changes what a prediction means. A forecast fitted on generated demo days
is a demonstration of the method; a forecast fitted on measured days is a
statement about that person. The two are not interchangeable and must not look
alike in the app.

So the saved report records `training_data`: how many rows came from each
source, and whether any generated data was involved. Inference reads it back
and the API turns it into a caveat, which means a synthetic-fitted model can no
longer be served as a personal forecast without saying so.

SAMPLE SIZE HONESTY
-------------------
A personal history is small by nature. With 60 days and a 7-day lag, about 53
rows remain. The saved metrics include `n_samples` and a `sample_size_warning`
so the app can state plainly that these figures rest on few observations
rather than presenting them with false confidence.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Lasso, LinearRegression, Ridge
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import get_settings
from app.ml.evaluation.metrics import regression_metrics

#: Below this many usable rows, metrics are too unstable to report without a
#: prominent caveat.
MIN_ROWS_FOR_CONFIDENCE = 30
#: Absolute minimum to fit anything at all.
MIN_ROWS_TO_TRAIN = 14

#: Days consumed before the first usable row exists, because lag_7 and the
#: 7-day rolling statistics need seven prior days. Named so the API can state
#: the calendar-day requirement (MIN_ROWS_TO_TRAIN + this) without repeating
#: the arithmetic and letting the two drift apart.
LAG_WARMUP_DAYS = 7

ARTIFACT_NAME = "regression_forecast"

#: Features, all derived from days STRICTLY BEFORE the target day.
FEATURE_COLUMNS = [
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_7",
    "roll_mean_3",
    "roll_mean_7",
    "roll_std_7",
    "day_of_week",
    "is_weekend",
]


class NotEnoughHistory(Exception):
    """Raised when there are too few days to fit a forecaster."""

    def __init__(self, have: int, need: int) -> None:
        self.have = have
        self.need = need
        super().__init__(f"need at least {need} usable days to train, have {have}")


@dataclass
class ModelResult:
    """Cross-validated result for one regression model."""

    name: str
    rmse: float
    mae: float
    r2: float
    rmse_pct_of_mean: float
    #: Per-fold RMSE, so fold-to-fold stability is visible.
    fold_rmse: list[float] = field(default_factory=list)
    #: Fitted coefficient per feature (from the final full-data refit).
    coefficients: dict[str, float] = field(default_factory=dict)
    intercept: float = 0.0
    #: Hyperparameter actually chosen, where applicable.
    params: dict[str, Any] = field(default_factory=dict)


def build_supervised_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """Turn a daily series into a supervised learning table.

    Expects columns `record_date` and `total_screen_minutes`.

    Every feature is shifted so it describes only prior days. The target is
    the current day's screen time.
    """
    if "record_date" not in daily.columns or "total_screen_minutes" not in daily.columns:
        raise ValueError("daily frame needs 'record_date' and 'total_screen_minutes'")

    df = daily.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    df = df.sort_values("record_date").reset_index(drop=True)

    target = df["total_screen_minutes"].astype("float64")

    out = pd.DataFrame({"record_date": df["record_date"], "target": target})

    # Plain lags.
    for lag in (1, 2, 3, 7):
        out[f"lag_{lag}"] = target.shift(lag)

    # Rolling statistics over PRIOR days only: shift(1) first, then roll.
    prior = target.shift(1)
    out["roll_mean_3"] = prior.rolling(window=3, min_periods=3).mean()
    out["roll_mean_7"] = prior.rolling(window=7, min_periods=7).mean()
    out["roll_std_7"] = prior.rolling(window=7, min_periods=7).std()

    # Calendar features are known in advance, so they need no lag.
    out["day_of_week"] = out["record_date"].dt.day_of_week
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)

    # Drop the warm-up rows where lags are undefined.
    before = len(out)
    out = out.dropna(subset=FEATURE_COLUMNS + ["target"]).reset_index(drop=True)
    out.attrs["rows_dropped_warmup"] = before - len(out)

    return out


def _candidate_models() -> dict[str, Pipeline]:
    """The three required regressors, each inside a scaling Pipeline.

    Scaling matters here: Ridge and Lasso penalise coefficient magnitude, so
    unscaled features would be regularised unevenly purely because of their
    units. Putting StandardScaler inside the Pipeline means cross-validation
    refits it on each training fold, so no test-fold statistics leak in.
    """
    return {
        "LinearRegression": Pipeline(
            [("scaler", StandardScaler()), ("model", LinearRegression())]
        ),
        "Ridge": Pipeline(
            [("scaler", StandardScaler()), ("model", Ridge(alpha=1.0, random_state=None))]
        ),
        "Lasso": Pipeline(
            [
                ("scaler", StandardScaler()),
                # max_iter raised because Lasso on small, collinear data can
                # otherwise stop before converging and emit a warning.
                ("model", Lasso(alpha=1.0, max_iter=10_000, random_state=None)),
            ]
        ),
    }


def drop_incomplete_today(
    daily: pd.DataFrame, *, today: dt.date | None = None
) -> tuple[pd.DataFrame, float | None]:
    """Remove the current date from a frame used for MODELLING.

    WHY THIS EXISTS
    ---------------
    Today's screen time is still accumulating. A sync at 08:00 reports a few
    minutes for a day that will end at eight hours, so the row is a
    day-SO-FAR, not a day. Treating it as an observation is wrong in two
    separate ways:

      As a TRAINING row, it teaches the model that days like this one end at a
      few minutes.

      As lag_1 for a forecast, it is worse, because it directly drives the
      prediction. Measured on real data: a 7-minute partial day sitting at
      lag_1 pulled the next-day forecast down to 5.0 h while the preceding
      seven complete days averaged 8.7 h - an error of nearly four hours,
      caused entirely by treating a morning as a day.

    The rule is deliberately unconditional: the current date is excluded
    regardless of how large its total already is. Any threshold ("drop it only
    if it looks too small") would need to know what a normal day looks like for
    this person, which is the very thing being estimated - and at 23:50 a
    complete-looking day is still not complete.

    Days are NOT dropped from anything the user reads as a measurement. Home
    and Usage still show today's figure, because there it is correctly labelled
    as the day so far. This function guards the models only.

    Returns the frame without today, and today's total if one was removed
    (so callers can say what they set aside, rather than dropping it silently).
    """
    if daily.empty or "record_date" not in daily.columns:
        return daily, None

    today = today or dt.date.today()

    dates = pd.to_datetime(daily["record_date"]).dt.date
    is_today = dates == today
    if not is_today.any():
        return daily, None

    removed = float(daily.loc[is_today, "total_screen_minutes"].iloc[0])
    return daily.loc[~is_today].reset_index(drop=True), removed


def _describe_training_data(daily: pd.DataFrame) -> dict:
    """Count the training rows by provenance.

    Derived from the data actually handed to train(), so the record cannot
    drift from reality if a caller forgets to pass a scope.
    """
    if "source" not in daily.columns:
        return {
            "rows_by_source": {},
            "contains_synthetic": None,
            "fitted_on": "unknown",
            "note": (
                "Provenance was not supplied with the training rows, so what "
                "this model learned from cannot be stated. Treat its output as "
                "unattributed."
            ),
        }

    counts = {str(k): int(v) for k, v in daily["source"].value_counts().items()}
    synthetic = counts.get("synthetic", 0)
    real = sum(v for k, v in counts.items() if k != "synthetic")

    if synthetic and real:
        fitted_on, note = "mixed", (
            f"Fitted on {real} measured day(s) and {synthetic} generated "
            "demonstration day(s). The generated days outnumber or dilute the "
            "real ones, so predictions describe the mixture, not this person."
        )
    elif synthetic:
        fitted_on, note = "synthetic", (
            f"Fitted entirely on {synthetic} generated demonstration day(s). "
            "This demonstrates that the forecasting pipeline works; it is NOT "
            "a forecast of this person's behaviour, because the model has "
            "never seen their data."
        )
    elif real:
        fitted_on, note = "real", (
            f"Fitted on {real} measured day(s) with no generated data."
        )
    else:
        fitted_on, note = "unknown", "No rows carried a recognised source."

    return {
        "rows_by_source": counts,
        "contains_synthetic": synthetic > 0,
        "fitted_on": fitted_on,
        "note": note,
    }


def train(daily: pd.DataFrame, *, n_splits: int = 5) -> dict:
    """Train and compare the three regressors; save the best as an artifact.

    Returns the metrics report that is also written to disk as JSON.
    """
    settings = get_settings()

    # A day still in progress is not an observation - see
    # drop_incomplete_today(). Dropped before the supervised frame is built so
    # it cannot become a training row OR a lag for one.
    daily, partial_today = drop_incomplete_today(daily)

    supervised = build_supervised_frame(daily)

    n_rows = len(supervised)
    if n_rows < MIN_ROWS_TO_TRAIN:
        raise NotEnoughHistory(have=n_rows, need=MIN_ROWS_TO_TRAIN)

    # What this model was fitted on, counted from the rows themselves rather
    # than from whatever the caller intended to pass. See the module docstring:
    # a forecast fitted on generated days is a demonstration of the method, not
    # a statement about this person, and the app must be able to tell them
    # apart without guessing.
    training_data = _describe_training_data(daily)

    X = supervised[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = supervised["target"].to_numpy(dtype=float)

    # TimeSeriesSplit needs at least n_splits+1 samples; shrink the fold count
    # on short histories rather than crashing.
    usable_splits = max(2, min(n_splits, n_rows - 1))

    splitter = TimeSeriesSplit(n_splits=usable_splits)
    results: list[ModelResult] = []

    for name, pipeline in _candidate_models().items():
        fold_rmse: list[float] = []
        all_true: list[float] = []
        all_pred: list[float] = []

        for train_idx, test_idx in splitter.split(X):
            pipeline.fit(X[train_idx], y[train_idx])
            pred = pipeline.predict(X[test_idx])

            fold_rmse.append(float(np.sqrt(np.mean((y[test_idx] - pred) ** 2))))
            all_true.extend(y[test_idx].tolist())
            all_pred.extend(pred.tolist())

        # Pooled out-of-fold predictions give one honest overall score.
        pooled = regression_metrics(np.array(all_true), np.array(all_pred))

        # Refit on everything for the deployable model and its coefficients.
        pipeline.fit(X, y)
        model = pipeline.named_steps["model"]
        coefs = {
            feat: float(c) for feat, c in zip(FEATURE_COLUMNS, np.ravel(model.coef_))
        }

        results.append(
            ModelResult(
                name=name,
                rmse=pooled.rmse,
                mae=pooled.mae,
                r2=pooled.r2,
                rmse_pct_of_mean=pooled.rmse_pct_of_mean,
                fold_rmse=fold_rmse,
                coefficients=coefs,
                intercept=float(model.intercept_),
                params=model.get_params(),
            )
        )

    # Selection is by cross-validated RMSE, decided before looking at any
    # single model's numbers in isolation.
    best = min(results, key=lambda r: r.rmse)

    # Persist the winner, refitted on all available data.
    final_pipeline = _candidate_models()[best.name]
    final_pipeline.fit(X, y)

    settings.ensure_directories()
    artifact_path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    joblib.dump(
        {
            "pipeline": final_pipeline,
            "feature_columns": FEATURE_COLUMNS,
            "model_name": best.name,
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "n_training_rows": n_rows,
        },
        artifact_path,
    )

    # A baseline every model must beat: "tomorrow looks like today".
    naive_rmse = float(np.sqrt(np.mean((y[1:] - y[:-1]) ** 2))) if n_rows > 1 else float("nan")

    report = {
        "task": "regression",
        "target": "total_screen_minutes (next day)",
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_samples": n_rows,
        "training_data": training_data,
        "excluded_incomplete_day": (
            None
            if partial_today is None
            else (
                f"The current date was excluded from training: it held "
                f"{partial_today:.0f} minutes at the time of training and was "
                "still accumulating, so it is a day so far rather than a day."
            )
        ),
        "rows_dropped_warmup": int(supervised.attrs.get("rows_dropped_warmup", 0)),
        "cv_strategy": f"TimeSeriesSplit(n_splits={usable_splits})",
        "cv_note": (
            "TimeSeriesSplit is used instead of KFold because the data is "
            "ordered in time. Shuffling would train on future days to predict "
            "earlier ones and make the error look far smaller than it is."
        ),
        "feature_columns": FEATURE_COLUMNS,
        "feature_note": (
            "Every predictor is a lag or a rolling statistic of PRIOR days, so "
            "nothing from the target day itself is used."
        ),
        "naive_persistence_rmse": naive_rmse,
        "naive_note": (
            "Naive baseline predicts today's value for tomorrow. A model that "
            "cannot beat this has learned nothing useful."
        ),
        "best_model": best.name,
        "sample_size_warning": (
            None
            if n_rows >= MIN_ROWS_FOR_CONFIDENCE
            else (
                f"Only {n_rows} usable days. These figures are indicative, not "
                f"reliable; at least {MIN_ROWS_FOR_CONFIDENCE} days are needed "
                "for stable estimates."
            )
        ),
        "models": [
            {
                "name": r.name,
                "rmse": r.rmse,
                "mae": r.mae,
                "r2": r.r2,
                "rmse_pct_of_mean": r.rmse_pct_of_mean,
                "fold_rmse": r.fold_rmse,
                "coefficients": r.coefficients,
                "intercept": r.intercept,
                "beats_naive": bool(r.rmse < naive_rmse) if np.isfinite(naive_rmse) else None,
            }
            for r in results
        ],
        "artifact": artifact_path.name,
    }

    metrics_path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def load_daily_from_db(sources: list[Any] | None = None) -> pd.DataFrame:
    """Read this user's daily records out of SQLite into a DataFrame.

    `sources` restricts training to given provenances (a list of DataSource).
    None means every row, generated demonstration days included - useful for
    demonstrating the method, but see the module docstring: the resulting model
    is not a personal forecast and the report records that.

    The `source` column is carried through so train() can report exactly what
    it was fitted on.
    """
    from app.database.session import SessionLocal
    from app.services import usage_service

    db = SessionLocal()
    try:
        records = usage_service.list_records(db, limit=2000, sources=sources)
    finally:
        db.close()

    return pd.DataFrame(
        [
            {
                "record_date": r.record_date,
                "total_screen_minutes": r.total_screen_minutes,
                "source": r.source.value,
            }
            for r in records
        ]
    )
