"""Tests for the ML endpoints.

The most important cases here are the REFUSALS. The brief forbids showing a
number that could be mistaken for a prediction when no prediction is
possible, so the endpoints must fail with a clear 409 rather than returning
a plausible-looking default.

Note these tests share the real artifacts directory, so whether a model is
trained depends on the developer having run scripts/train_models.py. Tests
that need a specific trained/untrained state check first and skip rather than
assert a state they did not create - a test that fails purely because models
have not been trained yet would be noise, not signal.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.ml.classification import inference as clf_inference
from app.ml.regression import inference as reg_inference


# --------------------------------------------------------------------------
# Status reporting must be truthful
# --------------------------------------------------------------------------


def test_status_reports_real_training_state(client):
    body = client.get("/api/ml/status").json()

    assert body["regression"]["trained"] == reg_inference.is_trained()
    assert body["classification"]["trained"] == clf_inference.is_trained()
    assert "how_to_train" in body


def test_health_lists_only_artifacts_that_exist(client):
    body = client.get("/api/health").json()
    listed = set(body["models_trained"])

    if reg_inference.is_trained():
        assert "regression_forecast" in listed
    else:
        assert "regression_forecast" not in listed

    if clf_inference.is_trained():
        assert "risk_classifier" in listed
    else:
        assert "risk_classifier" not in listed


# --------------------------------------------------------------------------
# Refusals: no data
# --------------------------------------------------------------------------


def test_forecast_refuses_when_there_is_no_history(client):
    """A fresh database has no records, so there is nothing to forecast."""
    response = client.get("/api/ml/forecast")
    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "no usage records" in detail or "not trained" in detail


def test_classification_refuses_on_insufficient_history(client, valid_payload):
    """Three days is not enough to characterise a person's typical behaviour."""
    if not clf_inference.is_trained():
        pytest.skip("classifier not trained; run scripts/train_models.py")

    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        client.post("/api/usage/records", json=valid_payload | {"record_date": day})

    response = client.get("/api/ml/risk-classification")
    assert response.status_code == 409
    assert "not enough history" in response.json()["detail"].lower()


def test_forecast_refuses_when_history_is_too_short_for_lags(client, valid_payload):
    """Lag features need 8 consecutive days; fewer must refuse, not impute.

    Imputing a missing lag would invent an observation the user never made.
    """
    if not reg_inference.is_trained():
        pytest.skip("forecaster not trained; run scripts/train_models.py")

    start = dt.date(2026, 10, 1)
    for offset in range(4):
        day = (start + dt.timedelta(days=offset)).isoformat()
        client.post("/api/usage/records", json=valid_payload | {"record_date": day})

    response = client.get("/api/ml/forecast")
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Working predictions
# --------------------------------------------------------------------------


def test_forecast_returns_predictions_with_enough_history(client):
    if not reg_inference.is_trained():
        pytest.skip("forecaster not trained; run scripts/train_models.py")

    client.post("/api/data/seed-demo", params={"days": 90, "seed": 5})

    response = client.get("/api/ml/forecast", params={"horizon": 5})
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["predictions"]) == 5
    # Only the first day rests purely on observed data.
    assert body["predictions"][0]["is_recursive"] is False
    assert all(p["is_recursive"] for p in body["predictions"][1:])

    for p in body["predictions"]:
        # A day cannot contain more than 1440 minutes of screen time.
        assert 0 <= p["predicted_minutes"] <= 1440
        assert p["predicted_hours"] == pytest.approx(p["predicted_minutes"] / 60, abs=0.01)

    # The compounding-error caveat must be present, not optional.
    assert any(c and "compounds" in c for c in body["caveats"])


def test_risk_classification_returns_a_real_prediction(client):
    if not clf_inference.is_trained():
        pytest.skip("classifier not trained; run scripts/train_models.py")

    client.post("/api/data/seed-demo", params={"days": 60, "seed": 11})

    response = client.get("/api/ml/risk-classification")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["risk_class"] in {"Low", "Medium", "High"}
    # Probabilities must come from the model and form a distribution.
    assert body["probabilities"]
    assert sum(body["probabilities"].values()) == pytest.approx(1.0, abs=0.01)
    assert body["confidence"] == pytest.approx(max(body["probabilities"].values()))
    assert body["days_averaged"] == 60

    # The inputs actually fed to the model must be visible, in hours.
    assert set(body["model_inputs"]) == {
        "daily_screen_time_hours",
        "social_media_hours",
        "weekend_screen_time",
        "work_study_hours",
        "sleep_hours",
    }

    # Accuracy must be reported alongside the prediction, with the baseline
    # so the number can be judged rather than taken on trust.
    assert body["model_accuracy"]["majority_baseline"] is not None
    assert body["model_accuracy"]["cv_f1_macro"] is not None


def test_actual_vs_predicted_declares_it_is_in_sample(client):
    if not reg_inference.is_trained():
        pytest.skip("forecaster not trained; run scripts/train_models.py")

    client.post("/api/data/seed-demo", params={"days": 60, "seed": 3})

    body = client.get("/api/ml/actual-vs-predicted").json()
    assert body["n_points"] > 0
    assert len(body["series"]) == body["n_points"]
    for point in body["series"][:5]:
        assert "actual_minutes" in point and "predicted_minutes" in point

    # The chart flatters the model, and the response must say so.
    assert "IN-SAMPLE" in body["note"]


# --------------------------------------------------------------------------
# Model performance reporting
# --------------------------------------------------------------------------


def test_model_performance_reports_measured_scores(client):
    if not clf_inference.is_trained():
        pytest.skip("classifier not trained; run scripts/train_models.py")

    body = client.get("/api/ml/model-performance").json()
    clf = body["classification"]

    # All seven required models must be present.
    names = {m["name"] for m in clf["models"]}
    assert names == {
        "LogisticRegression",
        "SVM",
        "DecisionTree",
        "KNN",
        "RandomForest",
        "Bagging",
        "GradientBoosting",
    }

    for entry in clf["models"]:
        test = entry["test"]
        assert 0.0 <= test["accuracy"] <= 1.0
        assert 0.0 <= test["f1_macro"] <= 1.0
        # ROC-AUC must be a real number, not silently missing. A None here
        # is how it quietly vanished for every model in the first run.
        assert test["roc_auc_ovr"] is not None, f"{entry['name']} has no ROC-AUC"
        assert 0.0 <= test["roc_auc_ovr"] <= 1.0
        # Confusion matrix must be square and match the class count.
        n = len(test["labels"])
        assert len(test["confusion"]) == n
        assert all(len(row) == n for row in test["confusion"])

    # Every model must beat the majority baseline, else something is wrong.
    baseline = clf["majority_baseline"]
    for entry in clf["models"]:
        assert entry["cv_accuracy"] > baseline, f"{entry['name']} fails to beat baseline"

    # Selection must be documented and must not reference the test set.
    assert "TRAINING" in clf["selection_rule"]


def test_regression_metrics_include_the_naive_baseline(client):
    if not reg_inference.is_trained():
        pytest.skip("forecaster not trained; run scripts/train_models.py")

    body = client.get("/api/ml/model-performance").json()
    reg = body["regression"]

    assert {m["name"] for m in reg["models"]} == {
        "LinearRegression",
        "Ridge",
        "Lasso",
    }
    # TimeSeriesSplit, never shuffled KFold, for ordered data.
    assert "TimeSeriesSplit" in reg["cv_strategy"]
    assert reg["naive_persistence_rmse"] is not None

    for entry in reg["models"]:
        assert entry["rmse"] > 0
        assert entry["coefficients"]
