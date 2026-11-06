"""Tests for the PCA risk score and the Bayesian confidence layer.

The Bayesian tests are the strongest here because the behaviour is
mathematically determined and can be asserted exactly: the interval MUST
widen with less data, and the autocorrelation correction MUST widen it
further. Those are the properties that make the number honest.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml.risk_scoring import bayesian
from app.ml.risk_scoring import inference as score_inference


# --------------------------------------------------------------------------
# Bayesian: the interval must behave correctly
# --------------------------------------------------------------------------


def test_interval_narrows_as_evidence_accumulates():
    """More data must mean less uncertainty. Non-negotiable."""
    widths = []
    for n in (5, 10, 30, 90, 365):
        k = max(1, round(0.2 * n))
        est = bayesian.estimate_rate(successes=k, trials=n)
        widths.append(est.width)

    # Strictly decreasing.
    assert all(a > b for a, b in zip(widths, widths[1:])), widths
    # And the first interval must be genuinely uninformative.
    assert widths[0] > 0.5
    # While the last is reasonably tight.
    assert widths[-1] < 0.15


def test_autocorrelation_correction_widens_the_interval():
    """Correlated days carry less information, so the interval must widen.

    If this failed, the app would be overstating its certainty - the exact
    failure the brief warns against.
    """
    naive = bayesian.estimate_rate(successes=73, trials=365, autocorrelation=None)
    corrected = bayesian.estimate_rate(successes=73, trials=365, autocorrelation=0.52)

    assert corrected.width > naive.width
    assert corrected.effective_trials < naive.effective_trials
    # The posterior mean should barely move; only the uncertainty changes.
    assert corrected.mean == pytest.approx(naive.mean, abs=0.02)


def test_effective_sample_size_formula():
    """n_eff = n * (1 - rho) / (1 + rho) for an AR(1) process."""
    n_eff, _ = bayesian.effective_sample_size(365, 0.52)
    expected = 365 * (1 - 0.52) / (1 + 0.52)
    assert n_eff == pytest.approx(expected, rel=1e-6)

    # Zero or negative autocorrelation must leave n untouched.
    assert bayesian.effective_sample_size(100, 0.0)[0] == 100.0
    assert bayesian.effective_sample_size(100, -0.3)[0] == 100.0
    assert bayesian.effective_sample_size(100, None)[0] == 100.0


def test_tiny_samples_are_marked_unreliable():
    est = bayesian.estimate_rate(successes=1, trials=4)
    assert est.is_reliable is False
    assert any("too weak" in note for note in est.notes)

    big = bayesian.estimate_rate(successes=20, trials=100)
    assert big.is_reliable is True


def test_interval_always_contains_the_posterior_mean():
    for successes, trials in ((0, 10), (1, 10), (5, 10), (10, 10), (73, 365)):
        est = bayesian.estimate_rate(successes=successes, trials=trials)
        assert est.lower <= est.mean <= est.upper
        assert 0.0 <= est.lower <= 1.0
        assert 0.0 <= est.upper <= 1.0


def test_extreme_observations_do_not_produce_degenerate_intervals():
    """Zero or all successes must still yield a usable interval.

    This is why Jeffreys is used: with k=0 a naive estimate would be exactly
    0% with zero width, which asserts certainty from a handful of days.
    """
    none_observed = bayesian.estimate_rate(successes=0, trials=20)
    assert none_observed.mean > 0.0, "a 0% point estimate claims impossible certainty"
    assert none_observed.upper > none_observed.lower

    all_observed = bayesian.estimate_rate(successes=20, trials=20)
    assert all_observed.mean < 1.0
    assert all_observed.lower < all_observed.upper


def test_invalid_counts_are_rejected():
    with pytest.raises(ValueError):
        bayesian.estimate_rate(successes=11, trials=10)
    with pytest.raises(ValueError):
        bayesian.estimate_rate(successes=-1, trials=10)


def test_prior_is_documented_not_magic():
    prior = bayesian.describe_prior()
    assert prior["distribution"] == "Beta(0.5, 0.5)"
    assert prior["name"] == "Jeffreys"
    assert prior["equivalent_prior_observations"] == 1.0
    assert "non-informative" in prior["rationale"]


def test_lag1_autocorrelation_detects_real_structure():
    rng = np.random.default_rng(0)

    # Independent series: autocorrelation near zero.
    independent = rng.normal(0, 1, 500)
    assert abs(bayesian.lag1_autocorrelation(independent)) < 0.15

    # AR(1) with rho 0.7: should be detected near 0.7.
    ar = np.zeros(500)
    for i in range(1, 500):
        ar[i] = 0.7 * ar[i - 1] + rng.normal(0, 1)
    assert bayesian.lag1_autocorrelation(ar) == pytest.approx(0.7, abs=0.12)

    # Too short, or constant: None rather than a fabricated number.
    assert bayesian.lag1_autocorrelation(np.array([1.0, 2.0])) is None
    assert bayesian.lag1_autocorrelation(np.full(50, 3.0)) is None


# --------------------------------------------------------------------------
# Score bands
# --------------------------------------------------------------------------


def test_score_bands_are_monotonic_and_cover_the_range():
    assert score_inference.band_for(0.0) == "Low"
    assert score_inference.band_for(49.9) == "Low"
    assert score_inference.band_for(50.0) == "Medium"
    assert score_inference.band_for(69.9) == "Medium"
    assert score_inference.band_for(70.0) == "High"
    assert score_inference.band_for(100.0) == "High"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_risk_score_endpoint_surfaces_its_validation_status(client):
    if not score_inference.is_trained():
        pytest.skip("risk score not trained; run scripts/train_risk_score.py")

    client.post("/api/data/seed-demo", params={"days": 90, "seed": 21})

    response = client.get("/api/ml/risk-score")
    assert response.status_code == 200, response.text
    body = response.json()

    assert 0 <= body["current_score"] <= 100
    assert body["band"] in {"Low", "Medium", "High"}
    assert body["days_scored"] > 0
    assert len(body["score_history"]) == body["days_scored"]

    # The validation verdict must ALWAYS be present, so an unvalidated score
    # can never be shown as authoritative.
    assert "validation" in body
    assert "validated" in body["validation"]
    assert "suitable_as_headline_risk" in body
    assert body["suitable_as_headline_risk"] == body["validation"]["validated"]

    # And a caveat must say so in words.
    assert body["caveats"]
    if not body["validation"]["validated"]:
        assert any("did NOT pass validation" in c for c in body["caveats"])


def test_risk_score_endpoint_includes_a_credible_interval(client):
    if not score_inference.is_trained():
        pytest.skip("risk score not trained; run scripts/train_risk_score.py")

    client.post("/api/data/seed-demo", params={"days": 120, "seed": 33})

    body = client.get("/api/ml/risk-score").json()
    b = body["bayesian"]

    lower, upper = b["credible_interval"]
    assert 0.0 <= lower < upper <= 1.0
    assert lower <= b["estimated_rate"] <= upper
    assert b["credible_mass"] == 0.95
    # The prior must be documented in the response, not hidden in code.
    assert b["prior"]["distribution"] == "Beta(0.5, 0.5)"
    # Effective sample size must be reported alongside the raw count.
    assert b["effective_trials"] <= body["days_scored"]
    assert b["explanation"]


def test_risk_score_refuses_without_enough_history(client, valid_payload):
    if not score_inference.is_trained():
        pytest.skip("risk score not trained; run scripts/train_risk_score.py")

    client.post("/api/usage/records", json=valid_payload)

    response = client.get("/api/ml/risk-score")
    assert response.status_code == 409
    assert "not enough" in response.json()["detail"].lower()


def test_method_endpoint_documents_construction_and_validation(client):
    if not score_inference.is_trained():
        pytest.skip("risk score not trained; run scripts/train_risk_score.py")

    body = client.get("/api/ml/risk-score-method").json()

    # Every construction step must be documented.
    assert len(body["method"]) >= 5
    # Orientation must be recorded, because a component's sign is arbitrary.
    assert "orientation" in body
    assert "sign_flipped" in body
    # Validation must be present with its correlation.
    assert "spearman_rho" in body["validation"]
    # Kernel PCA must have been compared, not ignored.
    assert body["kernel_pca_comparison"]["attempted"] is True
    assert body["adoption_reason"]
    # Loadings must be reported per retained component.
    for comp in body["components"]:
        assert comp["loadings"]
        assert comp["dominant_features"]
