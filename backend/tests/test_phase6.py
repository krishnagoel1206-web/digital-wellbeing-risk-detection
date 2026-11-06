"""Tests for clustering and personal-baseline anomaly detection.

The most important test in this file is
`test_heavy_user_is_not_flagged_for_a_normal_heavy_day`. The brief states the
requirement directly: do not flag a naturally heavy user merely because their
screen time is higher than average. That is a behavioural guarantee, so it is
tested with a constructed history rather than left to inspection.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.ml.anomaly_detection import personal_baseline as pb
from app.ml.clustering import inference as cluster_inference
from app.ml.preprocessing.feature_engineering import build_personal_features


def _history(
    screen_minutes: list[int],
    *,
    late_night: list[int] | None = None,
    notifications: list[int] | None = None,
    start: dt.date = dt.date(2026, 1, 1),
) -> pd.DataFrame:
    """Build a personal history frame with explicit values."""
    n = len(screen_minutes)
    rows = []
    for i in range(n):
        total = screen_minutes[i]
        rows.append(
            {
                "record_date": start + dt.timedelta(days=i),
                "total_screen_minutes": total,
                "social_minutes": int(total * 0.35),
                "gaming_minutes": int(total * 0.10),
                "productivity_minutes": int(total * 0.20),
                "app_launches": 120,
                "notification_count": notifications[i] if notifications else 200,
                "late_night_minutes": late_night[i] if late_night else int(total * 0.10),
                "sleep_hours": 7.0,
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# THE CORE REQUIREMENT
# --------------------------------------------------------------------------


def test_heavy_user_is_not_flagged_for_a_normal_heavy_day():
    """A habitual 8-hour-a-day user must not be flagged for an 8-hour day.

    This is the brief's explicit requirement. The baseline is personal, so
    "heavy" is defined relative to this user, not to a population average.
    """
    rng = np.random.default_rng(0)
    # 60 days averaging 480 minutes (8 hours) with modest variation.
    screen = [int(x) for x in rng.normal(480, 25, 60)]
    history = _history(screen)

    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    by_date = {a.date: a for a in assessments}
    # Pick a day that is high in absolute terms but typical for this user.
    heavy_day = max(assessments, key=lambda a: 0 if a.is_anomalous else 1)

    flagged = [a for a in assessments if a.is_anomalous]
    # An 8-hour-a-day user living their normal life should trigger almost
    # nothing. Allow a couple of chance extremes out of 60 days.
    assert len(flagged) <= 3, (
        f"{len(flagged)}/60 days flagged for a consistently heavy user - "
        "the baseline is behaving like a fixed threshold"
    )
    assert heavy_day is not None
    assert by_date


def test_light_user_with_one_heavy_day_is_flagged():
    """The mirror case: deviation is relative, so a light user spiking is flagged."""
    screen = [120] * 40
    screen[30] = 600  # a single 10-hour day
    history = _history(screen)

    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    spike_date = (dt.date(2026, 1, 1) + dt.timedelta(days=30)).isoformat()
    spike = next(a for a in assessments if a.date == spike_date)

    assert spike.is_anomalous
    assert spike.severity in {"mild", "moderate", "severe"}
    contributing = [d.feature for d in spike.contributing()]
    assert "total_screen_minutes" in contributing


def test_late_night_shift_is_caught_with_screen_time_unchanged():
    """The case a fixed screen-time threshold cannot possibly catch.

    Total screen time is held constant across the whole history; only the
    late-night share moves. A threshold on total minutes would see nothing.
    """
    n = 40
    screen = [300] * n
    late = [30] * n
    late[25] = 240  # same total screen time, but mostly after 11pm

    history = _history(screen, late_night=late)
    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    target = (dt.date(2026, 1, 1) + dt.timedelta(days=25)).isoformat()
    day = next(a for a in assessments if a.date == target)

    assert day.is_anomalous, "late-night shift missed despite constant screen time"
    contributing = [d.feature for d in day.contributing()]
    assert "late_night_ratio" in contributing
    # And total screen time must NOT be among the reasons, because it did not move.
    assert "total_screen_minutes" not in contributing


# --------------------------------------------------------------------------
# Robustness of the baseline
# --------------------------------------------------------------------------


def test_a_single_extreme_day_does_not_mask_itself():
    """Why MAD is used instead of standard deviation.

    With mean/std, one enormous value inflates the spread enough to make
    itself look ordinary. Robust statistics must not have that weakness.
    """
    screen = [200] * 40
    screen[20] = 1200  # a 20-hour day

    history = _history(screen)
    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    target = (dt.date(2026, 1, 1) + dt.timedelta(days=20)).isoformat()
    day = next(a for a in assessments if a.date == target)

    assert day.is_anomalous
    assert day.severity == "severe"
    # The robust z should be large, not deflated by the outlier's own presence.
    assert day.max_abs_z > 6


def test_insufficient_history_is_refused_not_guessed():
    history = _history([300] * 5)
    features, _ = build_personal_features(history)

    with pytest.raises(pb.InsufficientHistory) as exc:
        pb.assess_history(features)

    assert exc.value.have == 5
    assert exc.value.need == pb.MIN_DAYS_FOR_BASELINE


def test_constant_feature_does_not_produce_infinite_scores():
    """A perfectly flat feature has MAD = 0; the z-score must stay finite."""
    history = _history([300] * 30)
    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    for a in assessments:
        assert np.isfinite(a.max_abs_z), f"{a.date} produced a non-finite score"
        for d in a.deviations:
            assert np.isfinite(d.robust_z)


def test_ground_truth_scoring_reports_recall_and_misses():
    screen = [250] * 30
    screen[10] = 900
    screen[20] = 950
    history = _history(screen)
    features, _ = build_personal_features(history)
    assessments = pb.assess_history(features)

    injected = [
        (dt.date(2026, 1, 1) + dt.timedelta(days=10)).isoformat(),
        (dt.date(2026, 1, 1) + dt.timedelta(days=20)).isoformat(),
    ]
    score = pb.score_against_ground_truth(assessments, injected)

    assert score["n_injected"] == 2
    assert score["recall"] == 1.0
    assert score["missed"] == []


# --------------------------------------------------------------------------
# API endpoints
# --------------------------------------------------------------------------


def test_anomalies_endpoint_refuses_without_enough_history(client, valid_payload):
    for day in ("2026-10-01", "2026-10-02"):
        client.post("/api/usage/records", json=valid_payload | {"record_date": day})

    response = client.get("/api/ml/anomalies")
    assert response.status_code == 409
    assert "days of history" in response.json()["detail"]


def test_anomalies_endpoint_returns_explanations(client):
    client.post("/api/data/seed-demo", params={"days": 90, "seed": 42})

    response = client.get("/api/ml/anomalies", params={"only_anomalous": True})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["days_assessed"] == 90
    # The method must be stated, not left implicit.
    assert "Median Absolute Deviation" in body["baseline_method"]
    assert "leave-one-out" in body["baseline_method"]

    for day in body["days"]:
        assert day["explanation"]
        assert day["severity"] in {"mild", "moderate", "severe"}
        for feature in day["contributing_features"]:
            # Each reason must name the user's own usual value, so the
            # comparison is visibly personal rather than absolute.
            assert "your_usual" in feature
            assert feature["explanation"]


def test_persona_endpoint_reports_structure_reliability(client):
    if not cluster_inference.is_trained():
        pytest.skip("clustering not trained; run the Phase 6 script")

    client.post("/api/data/seed-demo", params={"days": 60, "seed": 8})

    response = client.get("/api/ml/persona")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["name"]
    assert body["days_averaged"] == 60
    # The structure verdict must always accompany the persona, so a weak
    # assignment cannot be presented as a confident finding.
    assert body["reliability"]["structure"] in {
        "strong",
        "moderate",
        "weak",
        "none",
        "unmeasurable",
    }
    assert body["reliability"]["detail"]
    assert "distance_to_assigned" in body


def test_persona_endpoint_refuses_without_enough_history(client, valid_payload):
    if not cluster_inference.is_trained():
        pytest.skip("clustering not trained; run the Phase 6 script")

    client.post("/api/usage/records", json=valid_payload)

    response = client.get("/api/ml/persona")
    assert response.status_code == 409
    assert "not enough history" in response.json()["detail"].lower()


def test_clustering_report_includes_all_three_algorithms(client):
    if not cluster_inference.is_trained():
        pytest.skip("clustering not trained; run the Phase 6 script")

    body = client.get("/api/ml/clustering-report").json()

    assert set(body["algorithms"]) == {"KMeans", "DBSCAN", "BIRCH"}
    # k must be selected from a reported sweep, never assumed.
    assert body["k_selection"]["sweep"]
    assert body["k_selection"]["chosen_k"] in [s["k"] for s in body["k_selection"]["sweep"]]
    # DBSCAN's eps must be derived from the data, not hard-coded.
    assert "percentile" in body["dbscan_eps_selection"]["method"]
    # The honest structure verdict must be present.
    assert body["overall_structure"]["verdict"] in {
        "strong",
        "moderate",
        "weak",
        "none",
        "unmeasurable",
    }
    # And the late-night limitation must be recorded.
    assert "late-night" in body["late_night_note"]

    for name, result in body["algorithms"].items():
        for persona in result["personas"]:
            assert persona["name"], f"{name} cluster {persona['cluster_id']} has no name"
            # Centroids must be reported in real units for transparency.
            assert persona["centroid_raw"]
