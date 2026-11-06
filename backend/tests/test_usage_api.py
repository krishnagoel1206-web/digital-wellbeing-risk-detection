"""Tests for the usage endpoints.

These focus on the validation rules that protect data integrity, because those
are what stop bad input from quietly becoming a bad model later. A test that
only checks "POST returns 201" would pass even if every validator were broken.
"""

from __future__ import annotations

import datetime as dt


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


def test_health_reports_database_connected(client):
    response = client.get("/api/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["connected"] is True

    # `models_trained` must reflect the artifacts that genuinely exist on
    # disk - never more, never fewer.
    #
    # Expressed against the directory itself rather than a hard-coded list of
    # model names. Two earlier versions of this assertion broke as phases were
    # added (first by assuming no models existed, then by naming only two),
    # which was the test being wrong, not the endpoint. The real invariant is
    # "the endpoint reports exactly the artifacts on disk".
    from app.config import get_settings

    artifacts_dir = get_settings().artifacts_dir
    on_disk = (
        {p.stem for p in artifacts_dir.glob("*.joblib")} if artifacts_dir.exists() else set()
    )

    assert set(body["models_trained"]) == on_disk


# --------------------------------------------------------------------------
# Creating records
# --------------------------------------------------------------------------


def test_create_record_succeeds_and_round_trips(client, valid_payload):
    response = client.post("/api/usage/records", json=valid_payload)
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["record_date"] == "2026-11-01"
    assert body["total_screen_minutes"] == 384
    # Default provenance is manual entry.
    assert body["source"] == "manual"
    assert body["id"] > 0


def test_duplicate_date_is_rejected_with_409(client, valid_payload):
    """A second record for the same day must not silently overwrite the first."""
    assert client.post("/api/usage/records", json=valid_payload).status_code == 201

    second = client.post("/api/usage/records", json=valid_payload)
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


def test_category_minutes_cannot_exceed_total(client, valid_payload):
    """3h of social media on a 1h day is impossible and must be refused."""
    bad = valid_payload | {
        "total_screen_minutes": 60,
        "social_minutes": 180,
        "gaming_minutes": 0,
        "productivity_minutes": 0,
    }
    response = client.post("/api/usage/records", json=bad)
    assert response.status_code == 422
    assert "exceed" in response.text


def test_late_night_minutes_cannot_exceed_total(client, valid_payload):
    bad = valid_payload | {"total_screen_minutes": 30, "late_night_minutes": 120,
                           "social_minutes": 0, "gaming_minutes": 0,
                           "productivity_minutes": 0}
    response = client.post("/api/usage/records", json=bad)
    assert response.status_code == 422
    assert "late_night_minutes" in response.text


def test_future_dates_are_rejected(client, valid_payload):
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    bad = valid_payload | {"record_date": tomorrow.isoformat()}
    response = client.post("/api/usage/records", json=bad)
    assert response.status_code == 422
    assert "future" in response.text


def test_screen_time_cannot_exceed_a_day(client, valid_payload):
    bad = valid_payload | {"total_screen_minutes": 2000}
    response = client.post("/api/usage/records", json=bad)
    assert response.status_code == 422


def test_negative_values_are_rejected(client, valid_payload):
    bad = valid_payload | {"notification_count": -5}
    response = client.post("/api/usage/records", json=bad)
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_records_are_listed_newest_first(client, valid_payload):
    for day in ("2026-10-28", "2026-10-30", "2026-10-29"):
        payload = valid_payload | {"record_date": day}
        assert client.post("/api/usage/records", json=payload).status_code == 201

    body = client.get("/api/usage/records").json()
    assert [r["record_date"] for r in body] == ["2026-10-30", "2026-10-29", "2026-10-28"]


def test_date_range_filter(client, valid_payload):
    for day in ("2026-10-01", "2026-10-15", "2026-10-31"):
        client.post("/api/usage/records", json=valid_payload | {"record_date": day})

    body = client.get(
        "/api/usage/records", params={"start_date": "2026-10-10", "end_date": "2026-10-20"}
    ).json()
    assert [r["record_date"] for r in body] == ["2026-10-15"]


def test_missing_record_returns_404(client):
    assert client.get("/api/usage/records/9999").status_code == 404


# --------------------------------------------------------------------------
# Updating
# --------------------------------------------------------------------------


def test_partial_update_applies(client, valid_payload):
    record_id = client.post("/api/usage/records", json=valid_payload).json()["id"]

    response = client.patch(
        f"/api/usage/records/{record_id}", json={"notification_count": 400}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notification_count"] == 400
    # Untouched fields must be preserved.
    assert body["total_screen_minutes"] == 384


def test_update_revalidates_the_merged_record(client, valid_payload):
    """The key update test.

    Sending only `social_minutes` is valid in isolation - the value is a
    sensible positive integer. It is only invalid once merged with the STORED
    total. If the merged row were not re-validated, this would corrupt the
    per-app ratio features silently.
    """
    record_id = client.post("/api/usage/records", json=valid_payload).json()["id"]

    # Stored total is 384; 900 minutes of social media cannot fit inside it.
    response = client.patch(f"/api/usage/records/{record_id}", json={"social_minutes": 900})
    assert response.status_code == 422
    assert "exceed" in response.text


# --------------------------------------------------------------------------
# Deleting
# --------------------------------------------------------------------------


def test_delete_record(client, valid_payload):
    record_id = client.post("/api/usage/records", json=valid_payload).json()["id"]
    assert client.delete(f"/api/usage/records/{record_id}").status_code == 204
    assert client.get(f"/api/usage/records/{record_id}").status_code == 404


def test_delete_by_source_only_clears_that_source(client, valid_payload):
    client.post("/api/usage/records", json=valid_payload | {"record_date": "2026-10-01"})
    client.post(
        "/api/usage/records",
        json=valid_payload | {"record_date": "2026-10-02", "source": "synthetic"},
    )

    response = client.delete("/api/usage/records", params={"source": "synthetic"})
    assert response.status_code == 200
    assert response.json()["deleted"] == 1

    remaining = client.get("/api/usage/records").json()
    assert len(remaining) == 1
    assert remaining[0]["source"] == "manual"


# --------------------------------------------------------------------------
# Summary / baseline gate
# --------------------------------------------------------------------------


def test_summary_is_empty_on_a_fresh_database(client):
    body = client.get("/api/usage/summary").json()
    assert body["total_records"] == 0
    assert body["first_date"] is None
    assert body["has_enough_for_baseline"] is False
    assert body["contains_synthetic_data"] is False


def test_baseline_gate_opens_only_at_the_threshold(client, valid_payload):
    """The brief requires insufficient history to be handled gracefully.

    With 13 of the required 14 days the API must still report that a reliable
    baseline is not possible; the 14th day flips it.
    """
    summary = client.get("/api/usage/summary").json()
    threshold = summary["min_days_for_baseline"]

    start = dt.date(2026, 6, 1)
    for offset in range(threshold - 1):
        day = (start + dt.timedelta(days=offset)).isoformat()
        client.post("/api/usage/records", json=valid_payload | {"record_date": day})

    body = client.get("/api/usage/summary").json()
    assert body["total_records"] == threshold - 1
    assert body["has_enough_for_baseline"] is False

    final_day = (start + dt.timedelta(days=threshold - 1)).isoformat()
    client.post("/api/usage/records", json=valid_payload | {"record_date": final_day})

    body = client.get("/api/usage/summary").json()
    assert body["total_records"] == threshold
    assert body["has_enough_for_baseline"] is True


def test_summary_flags_synthetic_data(client, valid_payload):
    client.post(
        "/api/usage/records",
        json=valid_payload | {"record_date": "2026-10-05", "source": "synthetic"},
    )
    body = client.get("/api/usage/summary").json()
    assert body["contains_synthetic_data"] is True
    assert body["records_by_source"]["synthetic"] == 1


# --------------------------------------------------------------------------
# Provenance filtering on the records endpoint
# --------------------------------------------------------------------------


def test_records_can_be_restricted_to_measured_days(client):
    """A chart of the user's behaviour must not include generated days.

    Without this filter the Home screen's trend chart and the Analytics
    charts had no way to exclude demonstration rows, so they plotted 359
    generated days alongside the user's own.
    """
    client.post("/api/data/seed-demo", params={"days": 10, "seed": 3})

    everything = client.get("/api/usage/records", params={"limit": 2000}).json()
    assert len(everything) == 10
    assert {r["source"] for r in everything} == {"synthetic"}

    measured = client.get(
        "/api/usage/records", params={"limit": 2000, "sources": ["device"]}
    ).json()
    assert measured == [], "generated rows leaked into a device-only query"


def test_omitting_the_filter_still_returns_everything(client):
    """The Data screen is an inventory, and deliberately shows all sources."""
    # 7 is the endpoint's minimum; an earlier version of this test asked for 5,
    # was rejected with 422, seeded nothing, and so looked like a filter bug.
    seeded = client.post("/api/data/seed-demo", params={"days": 7, "seed": 3})
    assert seeded.status_code == 200, seeded.text

    rows = client.get("/api/usage/records", params={"limit": 2000}).json()
    assert len(rows) == 7


def test_several_sources_can_be_requested_at_once(client, valid_payload):
    """`real` scope spans device, manual and csv, so the filter takes a list."""
    client.post("/api/usage/records", json=valid_payload)
    client.post("/api/data/seed-demo", params={"days": 7, "seed": 3})

    rows = client.get(
        "/api/usage/records",
        params={"limit": 2000, "sources": ["device", "manual", "csv"]},
    ).json()

    assert len(rows) == 1
    assert rows[0]["source"] == "manual"


def test_an_unknown_source_is_rejected(client):
    """A typo must fail loudly rather than silently returning everything."""
    response = client.get(
        "/api/usage/records", params={"limit": 10, "sources": ["devise"]}
    )
    assert response.status_code == 422
