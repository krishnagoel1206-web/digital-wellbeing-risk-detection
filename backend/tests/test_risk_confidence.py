"""Tests for the Bayesian risk-confidence layer (week-by-week classification
combined with a Dirichlet posterior)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app.ml.classification import bayesian_confidence as bc
from app.ml.classification import inference as clf

pytestmark = pytest.mark.skipif(not clf.is_trained(), reason="risk classifier not trained")


def _history(days: int, minutes: float = 360, sleep: float | None = 7.0, end: dt.date | None = None):
    end = end or dt.date(2026, 11, 1)
    rng = np.random.default_rng(1)
    rows = []
    for i in range(days):
        rows.append(
            {
                "record_date": end - dt.timedelta(days=days - 1 - i),
                "total_screen_minutes": float(minutes + rng.normal(0, 20)),
                "social_minutes": float(minutes * 0.4),
                "productivity_minutes": float(minutes * 0.2),
                "sleep_hours": sleep,
                "source": "device",
            }
        )
    return pd.DataFrame(rows)


def test_posterior_is_a_valid_distribution():
    r = bc.estimate(_history(56))
    assert len(r.weeks) == 8
    assert sum(r.share_mean.values()) == pytest.approx(1.0)
    assert sum(r.prob_most_common.values()) == pytest.approx(1.0, abs=1e-9)
    for c in r.classes:
        lo, hi = r.share_interval[c]
        assert 0 <= lo <= r.share_mean[c] <= hi <= 1
    assert 0 <= r.prob_elevated_majority <= 1


def test_posterior_alpha_is_prior_plus_soft_counts():
    r = bc.estimate(_history(28))
    soft = {c: sum(w.probabilities[c] for w in r.weeks) for c in r.classes}
    for c in r.classes:
        assert r.posterior_alpha[c] == pytest.approx(bc.PRIOR + soft[c], abs=1e-3)


def test_more_consistent_weeks_mean_more_certainty():
    few = bc.estimate(_history(14, minutes=600))
    many = bc.estimate(_history(84, minutes=600))
    top = many.most_likely_class
    assert few.most_likely_class == top
    width = lambda r: r.share_interval[top][1] - r.share_interval[top][0]  # noqa: E731
    assert width(many) < width(few)
    assert many.prob_most_common[top] >= few.prob_most_common[top]


def test_refuses_with_too_few_weeks():
    with pytest.raises(bc.InsufficientWeeks):
        bc.estimate(_history(7))


def test_weeks_without_sleep_are_skipped_not_guessed():
    with pytest.raises(bc.InsufficientWeeks):
        bc.estimate(_history(28, sleep=None))


def test_flags_demo_data_in_caveats():
    h = _history(28)
    h["source"] = "synthetic"
    r = bc.estimate(h)
    assert "demo" in r.caveats[0]


def test_endpoint_refuses_without_history(client):
    response = client.get("/api/ml/risk-confidence")
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Through the in-app dispatch layer (what the phone actually calls)
# --------------------------------------------------------------------------

import json  # noqa: E402

from app import mobile_api  # noqa: E402


@pytest.fixture()
def device(tmp_path):
    from app.config import get_settings, reset_settings

    artifacts = get_settings().artifacts_dir
    reset_settings()
    mobile_api._CONFIGURED = False
    mobile_api._ROUTES.clear()
    report = json.loads(mobile_api.configure(str(tmp_path), str(artifacts)))
    assert report["ok"], report.get("traceback")

    def call(name: str, **params):
        return json.loads(mobile_api.call(name, json.dumps(params)))

    yield call
    reset_settings()
    mobile_api._CONFIGURED = False
    mobile_api._ROUTES.clear()


def test_risk_confidence_through_the_device_bridge(device):
    device("data/seed-demo", days=90, seed=7)
    out = device("ml/risk-confidence", scope="all", weeks=8)
    assert out["status"] == 200, out
    body = out["body"]
    assert body["n_weeks"] >= 2
    assert sum(body["prob_most_common"].values()) == pytest.approx(1.0, abs=1e-6)
    assert body["most_likely_class"] in {"Low", "Medium", "High"}
    # Demo data is named, never silently used.
    assert any("demo" in (c or "") for c in body["caveats"])


def test_risk_confidence_respects_scope(device):
    device("data/seed-demo", days=90, seed=7)
    out = device("ml/risk-confidence", scope="device")
    assert out["status"] == 409  # demo rows are not device data


def test_anomalies_expose_the_multivariate_check(device):
    device("data/seed-demo", days=90, seed=7)
    body = device("ml/anomalies", scope="all", limit=90)["body"]
    assert "combination_only_days" in body
    assert body["features_checked"]
    assessed = [d for d in body["days"] if d["mahalanobis_p"] is not None]
    assert assessed, "the combination test should run on most days"
    for d in assessed:
        assert 0.0 <= d["mahalanobis_p"] <= 1.0
        # "Unusual combination" is reserved for days where the combination is
        # the ONLY signal - no single measure extreme on its own.
        assert d["unusual_combination"] == (d["mahalanobis_p"] < 0.01 and d["max_abs_z"] < 3.0)


def test_forecast_compares_the_week_ahead_with_last_week(device):
    device("data/seed-demo", days=90, seed=7)
    body = device("ml/forecast", scope="all", horizon=7)["body"]
    wc = body["week_comparison"]
    assert wc is not None
    assert wc["change_minutes_per_day"] == pytest.approx(
        wc["next_week_avg_minutes"] - wc["last_week_avg_minutes"], abs=0.2
    )
    assert wc["within_model_error"] == (
        abs(wc["change_minutes_per_day"]) < wc["noise_threshold_minutes"]
    )
