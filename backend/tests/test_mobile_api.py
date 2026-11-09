"""Tests for the phone's entry point into Python.

WHY THESE MATTER MORE THAN THE HTTP TESTS
-----------------------------------------
Every other test in this suite reaches the services through FastAPI, which
quietly handles serialisation, parameter coercion and error mapping. On the
device none of that exists: Kotlin calls mobile_api.call() and receives a JSON
string, so everything FastAPI was doing invisibly has to be done here - and a
gap would show up only on a real phone.

The serialisation test exists because of a bug that reached a running device
simulation. `json.dumps(payload, cls=_Encoder, default=str)` looks like a
harmless safety net and silently REPLACES the encoder class's default method,
so every dataclass arrived as its repr string: the usage summary came back as
text, and indexing it raised "string indices must be integers".
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app import mobile_api


@pytest.fixture()
def device(tmp_path):
    """A configured backend writing into a throwaway directory."""
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


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


def test_configure_creates_the_database_in_app_storage(tmp_path):
    from app.config import get_settings, reset_settings

    artifacts = get_settings().artifacts_dir
    reset_settings()
    mobile_api._CONFIGURED = False

    report = json.loads(mobile_api.configure(str(tmp_path), str(artifacts)))
    assert report["ok"] is True
    assert (tmp_path / "data" / "wellbeing.db").exists(), (
        "the database was not created inside the app's private directory"
    )

    reset_settings()
    mobile_api._CONFIGURED = False


def test_calling_before_configure_is_refused_not_crashed():
    """Kotlin gets a readable reason rather than an exception across the JNI."""
    mobile_api._CONFIGURED = False
    out = json.loads(mobile_api.call("health"))

    assert out["status"] == 503
    assert "configure()" in out["body"]["detail"]


# --------------------------------------------------------------------------
# Dispatch contract
# --------------------------------------------------------------------------


def test_every_route_the_app_uses_exists():
    """No screen may ask for something unroutable."""
    names = set(json.loads(mobile_api.routes()))
    required = {
        "health",
        "ml/status",
        "ml/risk-classification",
        "ml/forecast",
        "ml/actual-vs-predicted",
        "ml/persona",
        "ml/anomalies",
        "ml/risk-score",
        "ml/recommendations",
        "ml/model-performance",
        "usage/records",
        "usage/summary",
        "data/feature-dictionary",
        "data/sync",
        "data/sync-status",
    }
    assert required <= names, f"missing: {sorted(required - names)}"


def test_an_unknown_endpoint_is_a_404_listing_what_exists(device):
    out = device("ml/does-not-exist")

    assert out["status"] == 404
    assert "ml/forecast" in out["body"]["available"]


def test_malformed_parameters_are_a_400(device):
    out = json.loads(mobile_api.call("health", "{not json"))

    assert out["status"] == 400
    assert "valid JSON" in out["body"]["detail"]


def test_a_refusal_is_409_with_a_reason(device):
    """An empty database refuses, and says what is short."""
    out = device("ml/risk-classification", scope="all")

    assert out["status"] == 409
    detail = out["body"]["detail"].lower()
    assert "needed" in detail or "no usage records" in detail


# --------------------------------------------------------------------------
# Serialisation: what FastAPI used to do invisibly
# --------------------------------------------------------------------------


def test_dataclass_responses_arrive_as_objects_not_strings(device):
    """The bug this file was written for.

    usage/summary returns a UsageSummary dataclass. Stringified, every field
    access on the device would have failed.
    """
    device("data/seed-demo", days=30, seed=5)
    body = device("usage/summary")["body"]

    assert isinstance(body, dict), f"expected an object, got {type(body).__name__}"
    assert body["total_records"] == 30
    assert isinstance(body["has_enough_for_baseline"], bool)


def test_dates_and_enums_serialise(device):
    device("data/seed-demo", days=30, seed=5)
    rows = device("usage/records", limit=3)["body"]

    assert isinstance(rows, list) and rows
    dt.date.fromisoformat(rows[0]["record_date"])  # date -> ISO string
    assert rows[0]["source"] == "synthetic"  # enum -> its value


def test_results_contain_no_nan_that_would_break_the_parser(device):
    """NaN is not valid JSON, and an uncomputable metric is null.

    A silently coerced 0.0 would read on screen as a real measurement.
    """
    device("data/seed-demo", days=90, seed=7)
    raw = mobile_api.call("ml/model-performance", "{}")

    assert "NaN" not in raw and "Infinity" not in raw
    json.loads(raw)  # raises if the encoder emitted either


# --------------------------------------------------------------------------
# The ML actually runs through this path
# --------------------------------------------------------------------------


def test_the_models_produce_real_results_in_process(device):
    """No server, no socket: the ML runs inside the caller's process."""
    device("data/seed-demo", days=90, seed=7)

    clf = device("ml/risk-classification", scope="all")
    assert clf["status"] == 200
    assert clf["body"]["risk_class"] in {"Low", "Medium", "High"}
    assert sum(clf["body"]["probabilities"].values()) == pytest.approx(1.0, abs=0.01)

    fc = device("ml/forecast", scope="all", horizon=3)
    assert fc["status"] == 200
    assert len(fc["body"]["predictions"]) == 3
    # The forecaster must still beat the naive baseline through this path.
    assert (
        fc["body"]["accuracy"]["rmse_minutes"]
        < fc["body"]["accuracy"]["naive_baseline_rmse"]
    )

    an = device("ml/anomalies", scope="all", limit=5)
    assert an["status"] == 200
    assert an["body"]["days_assessed"] > 0


def test_a_sync_round_trips_through_the_dispatch_layer(device):
    """The phone's own capture path, end to end, with no HTTP involved."""
    today = dt.date.today()
    days = [
        {
            "record_date": (today - dt.timedelta(days=offset)).isoformat(),
            "total_screen_minutes": 480,
            "social_minutes": 150,
            "app_launches": 200,
            "unlock_count": 90,
            "notification_count": 210,
            "notification_observed_minutes": 1440,
            "notification_day_minutes": 1440,
            "late_night_minutes": 40,
            "sleep_hours": 7.0,
            "sleep_is_estimated": True,
            "had_any_usage": True,
        }
        for offset in range(1, 4)
    ]

    out = device("data/sync", days=days)
    assert out["status"] == 200
    assert out["body"]["created"] == 3
    assert len(out["body"]["accepted_dates"]) == 3

    status = device("data/sync-status")["body"]
    assert status["device_days"] == 3
