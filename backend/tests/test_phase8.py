"""Tests for the KNN recommendation engine.

The central concern is CLAIM DISCIPLINE. The dataset is cross-sectional, so
it contains no outcomes, and the brief forbids inventing peer outcomes. These
tests assert that:

  * no recommendation is ever marked as a causal claim,
  * recommendations only cite features the data measurably supports,
  * features with no measured risk relationship cannot produce a
    population-grounded claim, and
  * every recommendation carries the numbers behind it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.ml.anomaly_detection import personal_baseline as pb
from app.ml.preprocessing.feature_engineering import build_personal_features
from app.ml.recommendations import inference as rec_inference


# --------------------------------------------------------------------------
# Claim discipline
# --------------------------------------------------------------------------


def test_no_recommendation_is_ever_a_causal_claim(client):
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    client.post("/api/data/seed-demo", params={"days": 120, "seed": 4})
    body = client.get("/api/ml/recommendations").json()

    for rec in body["recommendations"]:
        assert rec["claim_type"] in {"associational", "personal_deviation"}
        assert rec["claim_type"] != "causal"
        # The wording must not promise an outcome.
        text = f"{rec['title']} {rec['detail']}".lower()
        for forbidden in ("will lower", "will reduce", "will improve", "guarantee"):
            assert forbidden not in text, f"outcome promise in: {text}"


def test_claim_policy_is_published(client):
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    body = client.get("/api/ml/recommendations-method").json()

    assert "allowed" in body["claim_policy"]
    assert "forbidden" in body["claim_policy"]
    assert "cross-sectional" in body["claim_policy"]["forbidden"]


def test_only_measurably_relevant_features_get_population_claims(client):
    """A feature with no measured risk relationship must not be cited as one.

    Phase 4 and the recommender's own training both measured
    notifications_per_day, gaming_hours and app_opens_per_day at essentially
    zero mutual information with the risk label. Advising on them as a risk
    matter would be unsupported by this dataset.
    """
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    method = client.get("/api/ml/recommendations-method").json()
    grounded = set(method["population_grounded_features"])

    # The two features with real signal must be grounded.
    assert "daily_screen_time_hours" in grounded
    assert "social_media_hours" in grounded
    # The measured-noise features must NOT be.
    for noisy in ("notifications_per_day", "gaming_hours", "app_opens_per_day"):
        assert noisy not in grounded, f"{noisy} has no risk signal but is grounded"

    client.post("/api/data/seed-demo", params={"days": 120, "seed": 6})
    body = client.get("/api/ml/recommendations").json()

    for rec in body["recommendations"]:
        if rec["evidence_basis"] == "population_comparison":
            assert rec["evidence"]["feature"] in grounded


def test_every_recommendation_shows_its_numbers(client):
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    client.post("/api/data/seed-demo", params={"days": 120, "seed": 9})
    body = client.get("/api/ml/recommendations").json()

    for rec in body["recommendations"]:
        assert rec["title"] and rec["detail"] and rec["action"]
        assert rec["priority"] >= 1
        evidence = rec["evidence"]
        assert evidence["feature"]
        assert evidence["note"]

        if rec["evidence_basis"] == "population_comparison":
            assert "your_value" in evidence
            assert "low_risk_peer_median" in evidence
            assert evidence["n_low_risk_neighbours"] >= rec_inference.MIN_LOW_RISK_NEIGHBOURS
        else:
            assert "days_deviant" in evidence
            assert "your_usual" in evidence


# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------


def test_absolute_gap_threshold_catches_what_relative_misses():
    """Regression test for a real bug found during Phase 8.

    A user averaging 6.67 h/day against a low-risk peer median of 5.70 h/day
    has a gap of 0.97 h - nearly an hour of every day - but only 14.6% of
    their own total. A purely relative 15% rule discarded it silently. Both
    thresholds now apply, and either can qualify a gap.
    """
    gap = 0.97
    user_value = 6.67
    relative = gap / user_value

    assert relative < rec_inference.MIN_RELATIVE_GAP, "premise of the test changed"
    assert gap >= rec_inference.MIN_ABSOLUTE_GAP["daily_screen_time_hours"]


def test_small_differences_are_not_advised_on():
    """Noise must not become advice."""
    gap = 0.05
    user_value = 6.0
    assert gap / user_value < rec_inference.MIN_RELATIVE_GAP
    assert gap < rec_inference.MIN_ABSOLUTE_GAP["daily_screen_time_hours"]


# --------------------------------------------------------------------------
# Personal-baseline recommendations
# --------------------------------------------------------------------------


def _history_with_repeated_late_nights() -> pd.DataFrame:
    """A history where late-night use is repeatedly elevated at the end.

    Constructed because the generated demo history happens to contain only
    isolated one-off anomalies, each on a different feature, so the
    repeated-pattern path is never exercised by it.
    """
    rng = np.random.default_rng(3)
    rows = []
    start = dt.date(2026, 1, 1)
    for i in range(40):
        total = int(rng.normal(300, 20))
        # Normal late-night share for 34 days, then a sustained shift.
        late = int(total * (0.08 if i < 34 else 0.60))
        rows.append(
            {
                "record_date": start + dt.timedelta(days=i),
                "total_screen_minutes": total,
                "social_minutes": int(total * 0.3),
                "gaming_minutes": int(total * 0.1),
                "productivity_minutes": int(total * 0.2),
                "app_launches": 110,
                "notification_count": 190,
                "late_night_minutes": late,
                "sleep_hours": 7.0,
            }
        )
    return pd.DataFrame(rows)


def test_repeated_personal_deviation_produces_a_recommendation():
    history = _history_with_repeated_late_nights()
    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    recs = rec_inference.personal_recommendations(
        assessments, recent_days=14, min_occurrences=2
    )

    assert recs, "a sustained late-night shift produced no recommendation"
    late = next((r for r in recs if r.evidence["feature"] == "late_night_ratio"), None)
    assert late is not None, [r.evidence["feature"] for r in recs]

    assert late.evidence_basis == "personal_baseline"
    assert late.claim_type == "personal_deviation"
    assert late.evidence["days_deviant"] >= 2
    # The claim must be explicitly about the user's own history.
    assert "your own history" in late.evidence["note"]


def test_a_single_unusual_day_is_not_turned_into_advice():
    """One event is not a pattern; min_occurrences guards against that."""
    rng = np.random.default_rng(11)
    rows = []
    start = dt.date(2026, 3, 1)
    for i in range(40):
        total = int(rng.normal(300, 20))
        late = int(total * (0.60 if i == 20 else 0.08))
        rows.append(
            {
                "record_date": start + dt.timedelta(days=i),
                "total_screen_minutes": total,
                "social_minutes": int(total * 0.3),
                "gaming_minutes": int(total * 0.1),
                "productivity_minutes": int(total * 0.2),
                "app_launches": 110,
                "notification_count": 190,
                "late_night_minutes": late,
                "sleep_hours": 7.0,
            }
        )

    features, _ = build_personal_features(pd.DataFrame(rows))
    assessments = pb.assess_history(features)

    # The spike day must itself be detected...
    spike_date = (start + dt.timedelta(days=20)).isoformat()
    spike = next(a for a in assessments if a.date == spike_date)
    assert spike.is_anomalous

    # ...but a window containing only that one day must not yield advice.
    window = [a for a in assessments if a.date <= spike_date][-10:]
    recs = rec_inference.personal_recommendations(
        window, recent_days=10, min_occurrences=2
    )
    late = [r for r in recs if r.evidence["feature"] == "late_night_ratio"]
    assert not late, "a single spike was turned into a behavioural recommendation"


# --------------------------------------------------------------------------
# Neighbourhood reporting
# --------------------------------------------------------------------------


def test_neighbourhood_context_is_reported(client):
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    client.post("/api/data/seed-demo", params={"days": 90, "seed": 15})
    body = client.get("/api/ml/recommendations").json()

    n = body["neighbourhood"]
    assert n["n_neighbours"] > 0
    # The real risk mix of the neighbours must be reported - this is a fact
    # about the data, unlike an outcome claim.
    assert set(n["neighbour_risk_distribution"]) == {"Low", "Medium", "High"}
    assert sum(n["neighbour_risk_distribution"].values()) == n["n_neighbours"]
    assert isinstance(n["comparison_possible"], bool)

    if not n["comparison_possible"]:
        # If the comparison was refused, the reason must be stated.
        assert n["why_no_comparison"]
        assert body["sources"]["population_comparison"] == 0


def test_recommendations_refuse_without_enough_history(client, valid_payload):
    if not rec_inference.is_trained():
        pytest.skip("recommender not trained; run scripts/train_recommender.py")

    client.post("/api/usage/records", json=valid_payload)

    response = client.get("/api/ml/recommendations")
    assert response.status_code == 409
    assert "not enough history" in response.json()["detail"].lower()
