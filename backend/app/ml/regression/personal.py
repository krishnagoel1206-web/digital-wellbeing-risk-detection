"""A screen-time forecaster fitted on THIS user's real days, on the phone.

WHY THIS EXISTS
---------------
The shipped forecaster (regression_forecast.joblib) was fitted on the laptop
from a history that was mostly generated demo data (359 generated days, 8
measured). Its predictions used the user's real recent days as INPUT, but the
rules it applied - how much yesterday matters, how weekends differ - were
learned from invented days. The data that could fix that lives only in the
app's private database on the phone, so the fix has to run there too.

WHAT IT DOES
------------
Every forecast request refits from the user's real history (device +
Digital Wellbeing imports + anything they entered; never demo data):

1. Take the latest run of CONSECUTIVE calendar days. The lag features are
   row shifts, so a gap would make "yesterday" silently mean "the last
   recorded day".
2. Build the same 9 features as the shipped model (lags 1/2/3/7, 3- and
   7-day means, 7-day spread, weekday, weekend).
3. Compare, by walk-forward validation over 7-DAY forecasts made exactly as
   the app makes them (always from the past, never shuffled):
     learned:   LinearRegression, Ridge, Lasso
     baselines: "same as yesterday", "7-day average", "same day last week"
4. Use whichever has the lowest error ON YOUR DAYS. If no learned model beats
   the simple baselines, a baseline is used and the app says so: on a few
   weeks of data that is a real possibility, and a model that loses to "same
   as yesterday" has not earned the right to be shown as a forecast.

Fitting three tiny linear models on a few dozen rows takes milliseconds, so it
is redone per request rather than cached - the forecast always reflects every
day recorded so far, with no laptop and no retraining step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from app.ml.regression.inference import forecast_next_days
from app.ml.regression.train_forecast import (
    FEATURE_COLUMNS,
    LAG_WARMUP_DAYS,
    MIN_ROWS_FOR_CONFIDENCE,
    MIN_ROWS_TO_TRAIN,
    _candidate_models,
    build_supervised_frame,
)

#: Days ahead the app forecasts, and therefore the horizon candidates are
#: judged on.
HORIZON = 7

#: Last fit, keyed by the exact data it was fitted on.
_CACHE: dict = {}

#: Baselines expressed as "which feature column is the prediction".
BASELINES: dict[str, str] = {
    "Same as yesterday": "lag_1",
    "7-day average": "roll_mean_7",
    "Same day last week": "lag_7",
}


class NotEnoughPersonalHistory(Exception):
    def __init__(self, have_days: int, need_days: int) -> None:
        self.have_days = have_days
        self.need_days = need_days
        super().__init__(f"{have_days} consecutive real day(s), {need_days} needed")


@dataclass
class CandidateScore:
    name: str
    kind: str  # "model" | "baseline"
    rmse: float
    mae: float


@dataclass
class PersonalForecaster:
    name: str
    kind: str
    rmse: float
    mae: float
    naive_rmse: float
    rows: int
    days_used: int
    first_date: str
    last_date: str
    n_splits: int
    candidates: list[CandidateScore]
    #: Out-of-fold (actual, predicted) per tested date, for the chosen candidate.
    out_of_fold: list[dict] = field(default_factory=list)
    _predict: Callable[[np.ndarray], float] | None = None

    def predict(self, features: np.ndarray) -> float:
        assert self._predict is not None
        return float(self._predict(features))


def latest_consecutive_run(daily: pd.DataFrame) -> pd.DataFrame:
    """The newest stretch of days with no calendar gap."""
    if daily.empty:
        return daily
    df = daily.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    df = df.sort_values("record_date").reset_index(drop=True)
    gaps = df["record_date"].diff().dt.days.fillna(1) != 1
    run_id = gaps.cumsum()
    return df[run_id == run_id.iloc[-1]].reset_index(drop=True)


def _rmse(errors: np.ndarray) -> float:
    return float(np.sqrt(np.mean(errors ** 2)))


def fit(daily: pd.DataFrame) -> PersonalForecaster:
    """Choose and fit the best forecaster for this user's real days."""
    run = latest_consecutive_run(daily[["record_date", "total_screen_minutes"]])
    need_days = MIN_ROWS_TO_TRAIN + LAG_WARMUP_DAYS
    if len(run) < need_days:
        raise NotEnoughPersonalHistory(have_days=len(run), need_days=need_days)

    key = (
        tuple(run["record_date"].dt.date.astype(str)),
        tuple(np.round(run["total_screen_minutes"].astype(float), 1)),
    )
    if key in _CACHE:
        return _CACHE[key]

    sup = build_supervised_frame(run)
    X = sup[FEATURE_COLUMNS].to_numpy(dtype=float)
    y = sup["target"].to_numpy(dtype=float)
    n = len(sup)
    history = run[["record_date", "total_screen_minutes"]].copy()
    actual = dict(zip(history["record_date"].dt.date.astype(str), history["total_screen_minutes"].astype(float)))

    # Walk-forward over every possible start date. At each one, a candidate
    # sees only the days BEFORE it, then forecasts up to HORIZON days with the
    # very loop the app uses (later days built on earlier predictions). The
    # error is measured against what actually happened over that window, so
    # a rule that is good for tomorrow but poor for next week cannot win.
    min_train = max(MIN_ROWS_TO_TRAIN // 2, 7)
    origins = list(range(min_train, n))
    if not origins:
        raise NotEnoughPersonalHistory(have_days=len(run), need_days=need_days + 1)

    def baseline_predictor(column: str):
        col = FEATURE_COLUMNS.index(column)
        return lambda f: float(np.clip(f.reshape(-1)[col], 0, 1440))

    def learned_predictor(name: str, upto: int):
        pipe = _candidate_models()[name]
        pipe.fit(X[:upto], y[:upto])
        return lambda f: float(np.clip(pipe.predict(f.reshape(1, -1))[0], 0, 1440))

    specs = [(name, "model") for name in _candidate_models()] + [(name, "baseline") for name in BASELINES]
    candidates: list[CandidateScore] = []
    first_step: dict[str, list[tuple[int, float]]] = {}

    for name, kind in specs:
        errors: list[float] = []
        steps1: list[tuple[int, float]] = []
        for i in origins:
            origin = sup["record_date"].iloc[i]
            past = history[history["record_date"] < origin]
            predictor = (
                learned_predictor(name, i) if kind == "model" else baseline_predictor(BASELINES[name])
            )
            horizon = min(HORIZON, n - i)
            preds = forecast_next_days(past, horizon=horizon, predictor=predictor)
            for step, pr in enumerate(preds):
                if pr.date in actual:
                    errors.append(pr.predicted_minutes - actual[pr.date])
                    if step == 0:
                        steps1.append((i, pr.predicted_minutes))
        e = np.array(errors)
        candidates.append(CandidateScore(name, kind, _rmse(e), float(np.mean(np.abs(e)))))
        first_step[name] = steps1

    best = min(candidates, key=lambda c: c.rmse)
    naive = next(c for c in candidates if c.name == "Same as yesterday")
    predict = learned_predictor(best.name, n) if best.kind == "model" else baseline_predictor(BASELINES[best.name])

    dates = sup["record_date"].dt.date.astype(str).tolist()
    result = PersonalForecaster(
        name=best.name,
        kind=best.kind,
        rmse=best.rmse,
        mae=best.mae,
        naive_rmse=naive.rmse,
        rows=n,
        days_used=len(run),
        first_date=str(run["record_date"].iloc[0].date()),
        last_date=str(run["record_date"].iloc[-1].date()),
        n_splits=len(origins),
        candidates=sorted(candidates, key=lambda c: c.rmse),
        out_of_fold=[
            {"date": dates[i], "actual_minutes": float(y[i]), "predicted_minutes": round(p, 1)}
            for i, p in sorted(first_step[best.name])
        ],
        _predict=predict,
    )
    _CACHE.clear()  # one entry: the data only ever grows
    _CACHE[key] = result
    return result


def is_stable(f: PersonalForecaster) -> bool:
    """Whether the error estimate rests on enough rows to be taken at face value."""
    return f.rows >= MIN_ROWS_FOR_CONFIDENCE
