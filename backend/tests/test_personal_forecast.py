"""Tests for the on-phone personal forecaster.

It replaces a model whose rules were learned mostly from generated demo days,
so the tests pin down the two promises that matter: it learns ONLY from real
days, and it never shows a forecast that loses to "same as yesterday".
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.database.models import DataSource, UsageRecord
from app.ml.regression import personal
from app.services import analysis_service

TODAY = dt.date.today()


def series(days: int, end_ago: int = 1, seed: int = 0) -> pd.DataFrame:
    """A realistic-looking history: weekday/weekend pattern plus noise."""
    rng = np.random.default_rng(seed)
    dates = [TODAY - dt.timedelta(days=end_ago + days - 1 - i) for i in range(days)]
    mins = [420 + (90 if d.weekday() >= 5 else 0) + rng.normal(0, 35) for d in dates]
    return pd.DataFrame({"record_date": dates, "total_screen_minutes": np.clip(mins, 0, 1440)})


def store(db, frame: pd.DataFrame, source: DataSource) -> None:
    for d, m in zip(frame["record_date"], frame["total_screen_minutes"]):
        db.add(UsageRecord(record_date=d, source=source, total_screen_minutes=int(m)))
    db.commit()


# --------------------------------------------------------------------------
# The model itself
# --------------------------------------------------------------------------


def test_chooses_the_lowest_error_candidate_and_never_loses_to_naive():
    m = personal.fit(series(26))
    assert m.rmse == min(c.rmse for c in m.candidates)
    assert m.rmse <= m.naive_rmse
    assert {c.name for c in m.candidates} >= {"Lasso", "Ridge", "LinearRegression", "Same as yesterday"}


def test_needs_21_consecutive_days():
    with pytest.raises(personal.NotEnoughPersonalHistory):
        personal.fit(series(20))
    personal.fit(series(21))


def test_a_gap_means_only_the_latest_unbroken_run_counts():
    old = series(15, end_ago=30)
    recent = series(18, end_ago=1)  # 18 < 21 after the gap
    with pytest.raises(personal.NotEnoughPersonalHistory):
        personal.fit(pd.concat([old, recent]))


def test_out_of_fold_predictions_only_use_earlier_days():
    m = personal.fit(series(30))
    assert m.out_of_fold
    # Test days come after the warm-up and the first training window.
    first_test = min(p["date"] for p in m.out_of_fold)
    assert first_test > m.first_date


# --------------------------------------------------------------------------
# Through the service: real data only, demo data never
# --------------------------------------------------------------------------


def test_forecast_is_trained_on_real_days_not_demo(db_session):
    store(db_session, series(60, end_ago=40, seed=1), DataSource.SYNTHETIC)  # older demo days
    store(db_session, series(26, end_ago=1, seed=2), DataSource.WELLBEING)
    out = analysis_service.forecast(db_session, horizon=7, scope="device")
    assert out["training_data"]["fitted_on"] == "real"
    assert out["training_data"]["contains_synthetic"] is False
    assert "synthetic" not in out["training_data"]["rows_by_source"]
    assert len(out["predictions"]) == 7
    assert out["personal"]["chosen"] in {c["name"] for c in out["personal"]["candidates"]}
    assert out["accuracy"]["rmse_minutes"] <= out["accuracy"]["naive_baseline_rmse"]


def test_falls_back_to_shipped_model_when_history_is_short(db_session):
    from app.ml.regression import inference as reg

    if not reg.is_trained():
        pytest.skip("shipped forecaster not present")
    store(db_session, series(60, end_ago=12, seed=1), DataSource.SYNTHETIC)
    store(db_session, series(10, end_ago=1, seed=2), DataSource.DEVICE)
    out = analysis_service.forecast(db_session, horizon=3, scope="all")
    assert "personal" not in out
    assert out["personal_model"]["can_train_on_your_data"] is False


def test_actual_vs_predicted_uses_the_personal_model(db_session):
    store(db_session, series(26, end_ago=1, seed=3), DataSource.DEVICE)
    out = analysis_service.actual_vs_predicted(db_session, scope="device")
    assert out["n_points"] > 0
    assert "walk-forward" in out["note"]
