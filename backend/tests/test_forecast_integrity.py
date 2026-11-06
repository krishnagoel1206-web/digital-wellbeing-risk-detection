"""Tests for the forecaster's data-integrity rules.

Both rules here were written in response to measured defects, not to
hypotheticals, and each is noted with the number it actually produced.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from app.ml.regression.train_forecast import (
    LAG_WARMUP_DAYS,
    MIN_ROWS_TO_TRAIN,
    NotEnoughHistory,
    _describe_training_data,
    build_supervised_frame,
    drop_incomplete_today,
    train,
)


def _series(n: int, *, minutes: int = 400, source: str = "device", end: dt.date | None = None):
    """n consecutive days ending on `end` (default today)."""
    end = end or dt.date.today()
    return pd.DataFrame(
        {
            "record_date": [end - dt.timedelta(days=i) for i in range(n - 1, -1, -1)],
            "total_screen_minutes": [minutes] * n,
            "source": [source] * n,
        }
    )


# --------------------------------------------------------------------------
# The day in progress
# --------------------------------------------------------------------------


def test_the_current_day_is_dropped_from_model_input():
    frame = _series(5)
    out, removed = drop_incomplete_today(frame)

    assert len(out) == 4
    assert removed == 400
    assert dt.date.today() not in set(pd.to_datetime(out["record_date"]).dt.date)


def test_a_history_ending_yesterday_is_untouched():
    """Nothing is dropped when no row carries today's date."""
    frame = _series(5, end=dt.date.today() - dt.timedelta(days=1))
    out, removed = drop_incomplete_today(frame)

    assert len(out) == 5
    assert removed is None


def test_the_rule_is_unconditional_not_a_size_threshold():
    """A nearly-complete day is still not a complete day.

    Dropping only "suspiciously small" totals would need to know what a normal
    day looks like for this person - which is what the model is estimating.
    At 23:50 a large-looking day is still incomplete.
    """
    frame = _series(5)
    frame.loc[frame.index[-1], "total_screen_minutes"] = 900  # a big day so far

    out, removed = drop_incomplete_today(frame)
    assert len(out) == 4, "a large partial day was kept"
    assert removed == 900


def test_a_partial_day_would_otherwise_dominate_the_next_prediction(tmp_path, monkeypatch):
    """The defect this guards, measured rather than asserted.

    lag_1 is the most influential feature, and at forecast time the most
    recent row occupies it. On real data a 7-minute morning in that slot
    dragged the next-day forecast to 5.0 h while the preceding seven complete
    days averaged 8.7 h.

    The comparison below isolates the mechanism: the SAME 7-minute value is
    placed once on today's date (where the rule drops it) and once on
    yesterday's (where it is a genuine, if quiet, completed day and is kept).
    Only the second should pull the prediction down.

    Note this test would have passed against build_supervised_frame() while
    the bug was fully live - in a training frame the partial day is the last
    row and never becomes anyone's lag. The defect exists only on the
    inference path, which is what is exercised here.
    """
    from app.config import get_settings
    from app.ml.regression import inference

    settings = get_settings()
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path)

    # Train on a steady history that ends before the days under test.
    history = _series(40, minutes=520, end=dt.date.today() - dt.timedelta(days=2))
    history["total_screen_minutes"] = [500 + (i % 5) * 10 for i in range(len(history))]
    train(history, n_splits=3)
    inference.reset_cache()

    quiet = 7

    # (a) the 7 minutes belong to TODAY - a day still in progress.
    today_partial = _series(12, minutes=520, end=dt.date.today())
    today_partial.loc[today_partial.index[-1], "total_screen_minutes"] = quiet
    dropped = inference.forecast_next_days(today_partial, horizon=1)

    # (b) the same 7 minutes belong to YESTERDAY - a completed quiet day,
    #     which the model is entitled to learn from.
    past_quiet = _series(12, minutes=520, end=dt.date.today() - dt.timedelta(days=1))
    past_quiet.loc[past_quiet.index[-1], "total_screen_minutes"] = quiet
    kept = inference.forecast_next_days(past_quiet, horizon=1)

    assert dropped and kept

    # With today excluded, the first forecast day IS today.
    assert dropped[0].date == dt.date.today().isoformat()
    assert dropped[0].is_current_day is True
    assert kept[0].is_current_day is True  # also today, the day after the last row

    # The quiet day drags the prediction down only when it is a real past day.
    assert kept[0].predicted_minutes < dropped[0].predicted_minutes, (
        "excluding the day in progress made no difference, so it is still "
        f"reaching lag_1 (dropped={dropped[0].predicted_minutes}, "
        f"kept={kept[0].predicted_minutes})"
    )

    inference.reset_cache()


# --------------------------------------------------------------------------
# What the model was fitted on
# --------------------------------------------------------------------------


def test_training_records_a_synthetic_only_fit_as_such():
    desc = _describe_training_data(_series(30, source="synthetic"))

    assert desc["fitted_on"] == "synthetic"
    assert desc["contains_synthetic"] is True
    assert "NOT a forecast of this person" in desc["note"]


def test_training_records_a_measured_only_fit_as_real():
    desc = _describe_training_data(_series(30, source="device"))

    assert desc["fitted_on"] == "real"
    assert desc["contains_synthetic"] is False


def test_a_mixed_fit_is_not_reported_as_personal():
    frame = pd.concat([_series(25, source="synthetic"), _series(5, source="device")])
    desc = _describe_training_data(frame)

    assert desc["fitted_on"] == "mixed"
    assert "not this person" in desc["note"]


def test_missing_provenance_is_admitted_not_assumed():
    """Absent a source column, the model does not claim a clean pedigree."""
    frame = _series(30).drop(columns=["source"])
    desc = _describe_training_data(frame)

    assert desc["fitted_on"] == "unknown"
    assert desc["contains_synthetic"] is None


def test_the_report_carries_the_composition_through_to_disk(tmp_path, monkeypatch):
    """train() must record provenance, since the API builds its caveat from it."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path)

    report = train(_series(40, source="synthetic"), n_splits=3)

    assert report["training_data"]["fitted_on"] == "synthetic"
    assert report["n_samples"] > 0


# --------------------------------------------------------------------------
# Refusing to train rather than guessing
# --------------------------------------------------------------------------


def test_a_short_history_refuses_to_train():
    """Measured on the real database: 8 device days leave 1 usable row.

    The 7-day lag consumes the first week, so the calendar-day requirement is
    MIN_ROWS_TO_TRAIN + LAG_WARMUP_DAYS. Refusing is the correct behaviour -
    fitting 9 features to a handful of rows would produce coefficients that
    mean nothing.
    """
    with pytest.raises(NotEnoughHistory):
        train(_series(8))


def test_exactly_enough_history_does_train(tmp_path, monkeypatch):
    """The stated requirement is the real one, not an over-estimate."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "artifacts_dir", tmp_path)

    needed = MIN_ROWS_TO_TRAIN + LAG_WARMUP_DAYS
    # +1 because the current day is excluded from training.
    frame = _series(needed + 1)
    frame["total_screen_minutes"] = [300 + 11 * i for i in range(len(frame))]

    report = train(frame, n_splits=3)
    assert report["n_samples"] >= MIN_ROWS_TO_TRAIN


# --------------------------------------------------------------------------
# The same rule, applied once for every model (API level)
# --------------------------------------------------------------------------


def _sync_days(client, specs: list[tuple[int, int]]):
    """Sync (days_ago, screen_minutes) pairs as device data."""
    today = dt.date.today()
    days = []
    for offset, total in specs:
        days.append(
            {
                "record_date": (today - dt.timedelta(days=offset)).isoformat(),
                "total_screen_minutes": total,
                "social_minutes": int(total * 0.3),
                "gaming_minutes": int(total * 0.05),
                "productivity_minutes": int(total * 0.15),
                "app_launches": 180,
                "unlock_count": 95,
                "notification_count": 120,
                "calls_count": 4,
                "call_duration_minutes": 9,
                "battery_drain_percent": None,
                "late_night_minutes": int(total * 0.1),
                "sleep_hours": 7.0,
                "sleep_is_estimated": True,
                "had_any_usage": True,
            }
        )
    return client.post("/api/data/sync", json={"days": days})


def test_the_api_excludes_the_day_in_progress_from_every_model(client):
    """One rule in _daily_frame, rather than four partial fixes.

    Measured on real data: with today's 7-minute morning counted as a day,
    the classifier averaged 7.62 h/day; the seven complete days averaged
    8.69 h. The whole point of the headline is to describe how much the person
    actually uses their phone, and a morning in the mean defeats that.
    """
    # Eight heavy complete days, plus a 7-minute morning for today.
    specs = [(offset, 520) for offset in range(1, 9)] + [(0, 7)]
    assert _sync_days(client, specs).status_code == 200, "fixture failed to sync"

    response = client.get("/api/ml/risk-classification?scope=device")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days_averaged"] == 8, "today was counted as a day"

    # The averaged input reflects the complete days only.
    screen_hours = body["model_inputs"]["daily_screen_time_hours"]
    assert screen_hours == pytest.approx(520 / 60, abs=0.05), (
        f"the day in progress reached the model: {screen_hours:.2f} h/day"
    )

    # And the exclusion is stated rather than silently applied.
    assert any(
        c and "Today is not included" in c for c in body["caveats"]
    ), "a day was withheld without saying so"


def test_a_history_ending_yesterday_loses_nothing(client):
    """The rule must not cost a day when there is no day in progress."""
    _sync_days(client, [(offset, 480) for offset in range(1, 9)])

    body = client.get("/api/ml/risk-classification?scope=device").json()
    assert body["days_averaged"] == 8
    assert not any(c and "Today is not included" in c for c in body["caveats"])
