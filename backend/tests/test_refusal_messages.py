"""A missing measurement is not a missing day.

This distinction was got wrong FOUR separate times, in four modules, always
the same way: code that had detected an absent feature raised
`InsufficientHistory`, whose message talks about day counts. The result was a
refusal that contradicted itself -

    "Not enough history for recommendations - 7 day(s) recorded, 7 needed."

Seven recorded, seven needed, and still refused. A user reading that collects
more days, which cannot help, because the blocker was a measurement that was
never there.

The two conditions have different remedies and so must have different
messages:

    InsufficientHistory  ->  "record more days"     (time fixes it)
    MissingFeatures      ->  "this measurement is absent, and here is why"

These tests pin each raise site that was wrong, so a future change cannot
quietly reintroduce the conflation.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.ml.classification.inference import InsufficientHistory, MissingFeatures


def _complete_days(n: int, *, end_offset: int = 1) -> pd.DataFrame:
    """n consecutive complete days, ending `end_offset` days before today."""
    end = dt.date.today() - dt.timedelta(days=end_offset)
    return pd.DataFrame(
        {
            "record_date": [end - dt.timedelta(days=i) for i in range(n - 1, -1, -1)],
            "total_screen_minutes": [480 + (i % 4) * 25 for i in range(n)],
            "social_minutes": [160] * n,
            "gaming_minutes": [20] * n,
            "productivity_minutes": [70] * n,
            "app_launches": [190] * n,
            "notification_count": [130] * n,
            "late_night_minutes": [45] * n,
            "sleep_hours": [7.0] * n,
        }
    )


# --------------------------------------------------------------------------
# The exception itself
# --------------------------------------------------------------------------


def test_the_message_names_the_measurement_and_the_count():
    exc = MissingFeatures(
        features=["notifications_per_day", "sleep_hours"],
        days=8,
        blocked_rows={"notifications_per_day": 7, "sleep_hours": 2},
    )
    text = str(exc)

    assert "notifications per day (missing on 7 of 8 days)" in text
    assert "sleep hours (missing on 2 of 8 days)" in text
    # Most-blocking first, so the user reads the real cause before a footnote.
    assert text.index("notifications per day") < text.index("sleep hours")


def test_the_message_does_not_tell_the_user_to_collect_more_days():
    """The remedy for a missing measurement is not more days.

    This is the specific falsehood the old message told.
    """
    text = str(MissingFeatures(features=["notifications_per_day"], days=8))

    assert "day(s) of history are available" in text
    assert "needed" not in text, "the message still frames this as a day shortfall"


def test_a_partially_missing_field_is_distinguished_from_a_wholly_missing_one():
    """Two different sentences, because they are two different situations.

    The "missing on every day" wording was correct until notification access
    was granted and exactly one day carried a count - after which the check
    stopped firing and the user was told "0 usable days, 7 needed".
    """
    whole = str(MissingFeatures(features=["notifications_per_day"], days=8))
    partial = str(
        MissingFeatures(
            features=["notifications_per_day"],
            days=8,
            blocked_rows={"notifications_per_day": 7},
        )
    )

    assert "missing from every recorded day" in whole
    assert "missing from every recorded day" not in partial
    assert "7 of 8 days" in partial


# --------------------------------------------------------------------------
# Each raise site that was wrong
# --------------------------------------------------------------------------


def test_the_classifier_reports_a_missing_measurement_as_such():
    """Blanked field is `sleep_hours`, deliberately.

    Not `notification_count`: the classifier does not use it. Its measured
    mutual information with the target was 0.0000, so it was excluded at
    training time - which is why the classifier keeps working on this user's
    real data while the persona, the behaviour index and the recommender all
    refuse for want of notifications. An earlier version of this test blanked
    notifications and passed vacuously.
    """
    from app.ml.classification import inference as clf

    assert "sleep_hours" in clf.classifier_features()
    assert "notifications_per_day" not in clf.classifier_features()

    daily = _complete_days(10)
    daily["sleep_hours"] = np.nan

    with pytest.raises(MissingFeatures):
        clf.predict_from_personal_history(daily)


def test_the_classifier_still_reports_a_short_history_as_a_short_history():
    """The fix must not swallow the genuine day-shortfall case."""
    from app.ml.classification import inference as clf

    with pytest.raises(InsufficientHistory):
        clf.predict_from_personal_history(_complete_days(3))


def test_the_recommender_reports_a_missing_measurement_as_such():
    from app.ml.recommendations import inference as rec

    daily = _complete_days(10)
    daily["notification_count"] = np.nan

    with pytest.raises(MissingFeatures):
        rec.population_recommendations(daily)


def test_the_personal_baseline_reports_a_missing_measurement_as_such():
    """The day count is met; no feature has enough non-null days.

    Reporting this as "have 20, need 14" would be self-contradictory.
    """
    from app.ml.anomaly_detection import personal_baseline as pb
    from app.ml.preprocessing.feature_engineering import build_personal_features

    daily = _complete_days(20)
    features, _ = build_personal_features(daily)

    # Blank every baseline feature, leaving the days themselves intact.
    for column in pb.BASELINE_FEATURES:
        if column in features.columns:
            features[column] = np.nan

    with pytest.raises(MissingFeatures):
        pb.assess_history(features)


def test_the_personal_baseline_still_reports_a_short_history_as_a_short_history():
    from app.ml.anomaly_detection import personal_baseline as pb
    from app.ml.preprocessing.feature_engineering import build_personal_features

    features, _ = build_personal_features(_complete_days(4))

    with pytest.raises(pb.InsufficientHistory):
        pb.assess_history(features)


# --------------------------------------------------------------------------
# The API must map it to 409, never 500
# --------------------------------------------------------------------------


def _sync(client, days: int, *, notifications: int | None):
    today = dt.date.today()
    payload = [
        {
            "record_date": (today - dt.timedelta(days=offset)).isoformat(),
            "total_screen_minutes": 500,
            "social_minutes": 150,
            "gaming_minutes": 25,
            "productivity_minutes": 75,
            "app_launches": 200,
            "unlock_count": 95,
            "notification_count": notifications,
            "calls_count": 3,
            "call_duration_minutes": 8,
            "battery_drain_percent": None,
            "late_night_minutes": 50,
            "sleep_hours": 7.0,
            "sleep_is_estimated": True,
            "had_any_usage": True,
        }
        for offset in range(1, days + 1)
    ]
    return client.post("/api/data/sync", json={"days": payload})


#: The three models that genuinely require notification counts. The
#: classifier is absent on purpose - it was trained without the field, so it
#: must keep working when the field is missing. Including it here would assert
#: the opposite of a deliberate design decision.
NEEDS_NOTIFICATIONS = [
    "/api/ml/persona?scope=device",
    "/api/ml/risk-score?scope=device",
    "/api/ml/recommendations?scope=device",
]


@pytest.mark.parametrize("path", NEEDS_NOTIFICATIONS)
def test_a_missing_measurement_is_a_409_naming_the_field(client, path):
    """Never a 500: this is an expected state with a clear user meaning."""
    assert _sync(client, 10, notifications=None).status_code == 200

    response = client.get(path)
    assert response.status_code == 409, (
        f"{path} returned {response.status_code}, so a missing measurement is "
        "being treated as a server fault"
    )
    assert "notification" in response.text.lower(), (
        f"{path} refused without naming the measurement responsible"
    )


def test_the_classifier_survives_a_field_it_was_trained_without(client):
    """The headline must not fail for want of a field it never used.

    This is the practical payoff of excluding zero-information features: with
    notification access granted only today, the Home screen still works.
    """
    assert _sync(client, 10, notifications=None).status_code == 200

    response = client.get("/api/ml/risk-classification?scope=device")
    assert response.status_code == 200, response.text
    assert response.json()["risk_class"] in {"Low", "Medium", "High"}
