"""Tests for CSV import and the preprocessing pipeline.

These deliberately feed in broken files. An importer that only handles clean
input is worthless - the whole point is that bad rows are rejected with a
reason rather than quietly corrupting the history.
"""

from __future__ import annotations

import io


def _upload(client, text: str, filename: str = "usage.csv"):
    return client.post(
        "/api/data/import-csv",
        files={"file": (filename, io.BytesIO(text.encode("utf-8")), "text/csv")},
    )


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_import_minutes_csv(client):
    csv_text = (
        "date,screen_time_minutes,social_minutes,notifications\n"
        "2026-10-01,360,120,210\n"
        "2026-10-02,420,150,260\n"
    )
    response = _upload(client, csv_text)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["rows_in_file"] == 2
    assert body["rows_accepted"] == 2
    assert body["rows_rejected"] == 0

    stored = client.get("/api/usage/records").json()
    assert len(stored) == 2
    # Imported rows must be tagged as CSV, not manual.
    assert all(r["source"] == "csv" for r in stored)


def test_hours_columns_are_converted_to_minutes(client):
    """A 6.5-hour day must become 390 minutes, not 6.5."""
    csv_text = "date,screen_time_hours,social_media_hours\n2026-10-03,6.5,2.0\n"
    assert _upload(client, csv_text).json()["rows_accepted"] == 1

    record = client.get("/api/usage/records").json()[0]
    assert record["total_screen_minutes"] == 390
    assert record["social_minutes"] == 120


def test_various_date_formats_are_accepted(client):
    csv_text = (
        "date,screen_time_minutes\n"
        "2026-10-05,300\n"
        "06/10/2026,310\n"
        "07-10-2026,320\n"
    )
    body = _upload(client, csv_text).json()
    assert body["rows_accepted"] == 3


# --------------------------------------------------------------------------
# Column introspection
# --------------------------------------------------------------------------


def test_unrecognised_columns_are_reported_not_silently_dropped(client):
    csv_text = (
        "date,screen_time_minutes,mystery_column,another_unknown\n"
        "2026-10-01,360,foo,bar\n"
    )
    body = _upload(client, csv_text).json()

    assert body["rows_accepted"] == 1
    assert set(body["ignored_columns"]) == {"mystery_column", "another_unknown"}
    assert any("unrecognised" in note for note in body["notes"])


def test_missing_date_column_is_refused_with_guidance(client):
    csv_text = "screen_time_minutes,social_minutes\n360,120\n"
    body = _upload(client, csv_text).json()

    assert body["rows_accepted"] == 0
    assert any("no date column" in note for note in body["notes"])
    # The message must tell the user what IS accepted.
    assert any("record_date" in note or "date" in note for note in body["notes"])


def test_missing_screen_time_column_is_refused(client):
    csv_text = "date,social_minutes\n2026-10-01,120\n"
    body = _upload(client, csv_text).json()
    assert body["rows_accepted"] == 0
    assert any("no screen-time column" in note for note in body["notes"])


# --------------------------------------------------------------------------
# Bad data is rejected WITH REASONS
# --------------------------------------------------------------------------


def test_impossible_rows_are_rejected_with_a_reason(client):
    """Row 3 claims 3h of social media inside a 1h day."""
    csv_text = (
        "date,screen_time_minutes,social_minutes\n"
        "2026-10-01,360,120\n"
        "2026-10-02,60,180\n"
    )
    body = _upload(client, csv_text).json()

    assert body["rows_accepted"] == 1
    assert body["rows_rejected"] == 1
    assert len(body["problems"]) == 1
    # Row numbering must match the spreadsheet (header is row 1).
    assert body["problems"][0]["row"] == 3
    assert "exceed" in body["problems"][0]["reason"]


def test_unparseable_date_is_rejected_with_a_reason(client):
    csv_text = "date,screen_time_minutes\nnot-a-date,360\n"
    body = _upload(client, csv_text).json()

    assert body["rows_rejected"] == 1
    assert "unrecognised date" in body["problems"][0]["reason"]


def test_future_dates_are_rejected_on_import_too(client):
    import datetime as dt

    future = (dt.date.today() + dt.timedelta(days=5)).isoformat()
    csv_text = f"date,screen_time_minutes\n{future},300\n"
    body = _upload(client, csv_text).json()

    assert body["rows_rejected"] == 1
    assert "future" in body["problems"][0]["reason"]


def test_duplicate_dates_are_counted_separately_from_rejections(client):
    """A duplicate is not the same as invalid, and must be reported distinctly."""
    csv_text = "date,screen_time_minutes\n2026-10-01,360\n"
    assert _upload(client, csv_text).json()["rows_accepted"] == 1

    body = _upload(client, csv_text, filename="again.csv").json()
    assert body["rows_accepted"] == 0
    assert body["rows_duplicate"] == 1
    assert body["rows_rejected"] == 0


def test_empty_cells_mean_missing_not_zero(client):
    """A blank social_minutes must store NULL, not 0.

    Storing 0 would assert "this user spent no time on social media", which
    the file never said.
    """
    csv_text = "date,screen_time_minutes,social_minutes\n2026-10-01,360,\n"
    assert _upload(client, csv_text).json()["rows_accepted"] == 1

    record = client.get("/api/usage/records").json()[0]
    assert record["social_minutes"] is None


def test_non_csv_extension_is_rejected(client):
    response = client.post(
        "/api/data/import-csv",
        files={"file": ("data.txt", io.BytesIO(b"date,screen_time_minutes\n"), "text/plain")},
    )
    assert response.status_code == 400


def test_empty_file_is_rejected(client):
    response = client.post(
        "/api/data/import-csv",
        files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
    )
    assert response.status_code == 400


def test_import_history_records_the_batch(client):
    csv_text = "date,screen_time_minutes\n2026-10-01,360\n"
    _upload(client, csv_text, filename="my-export.csv")

    history = client.get("/api/data/import-history").json()["imports"]
    assert len(history) == 1
    assert history[0]["filename"] == "my-export.csv"
    assert history[0]["rows_accepted"] == 1


# --------------------------------------------------------------------------
# Feature dictionary
# --------------------------------------------------------------------------


def test_feature_dictionary_is_served_and_documented(client):
    body = client.get("/api/data/feature-dictionary").json()

    assert len(body["personal_raw"]) > 5
    assert len(body["personal_derived"]) > 5
    assert len(body["population"]) > 3

    # Every entry must carry a plain-language label and description - the
    # brief forbids exposing raw ML jargon to ordinary users.
    for group in ("personal_raw", "personal_derived", "population"):
        for spec in body[group]:
            assert spec["label"], f"{spec['key']} has no label"
            assert spec["description"], f"{spec['key']} has no description"

    # The zero-information finding must be recorded, not quietly forgotten.
    notifications = next(
        s for s in body["personal_raw"] if s["key"] == "notification_count"
    )
    assert notifications["caveat"] is not None
    assert "0.0000" in notifications["caveat"]

    # The classifier must exclude the noise columns.
    assert "gaming_hours" not in body["classifier_features"]
    assert "notifications_per_day" not in body["classifier_features"]
    assert "daily_screen_time_hours" in body["classifier_features"]


# --------------------------------------------------------------------------
# Synthetic seeding
# --------------------------------------------------------------------------


def test_seed_demo_inserts_labelled_synthetic_rows(client):
    body = client.post("/api/data/seed-demo", params={"days": 30, "seed": 7}).json()

    assert body["inserted"] == 30
    # Any generated row failing our own validation is a generator bug.
    assert body["generator_failures"] == []
    assert len(body["injected_anomalies"]) == 3
    assert "DEMONSTRATION" in body["warning"]

    summary = client.get("/api/usage/summary").json()
    assert summary["contains_synthetic_data"] is True
    assert summary["records_by_source"]["synthetic"] == 30


def test_seed_demo_is_reproducible_for_a_given_seed(client):
    first = client.post("/api/data/seed-demo", params={"days": 20, "seed": 99}).json()
    records_first = client.get("/api/usage/records").json()

    second = client.post(
        "/api/data/seed-demo", params={"days": 20, "seed": 99, "replace": True}
    ).json()
    records_second = client.get("/api/usage/records").json()

    assert first["inserted"] == second["inserted"]
    assert [r["total_screen_minutes"] for r in records_first] == [
        r["total_screen_minutes"] for r in records_second
    ]


def test_seed_demo_preserves_manual_records(client, valid_payload):
    """Reseeding demo data must never delete the user's real entries."""
    client.post("/api/usage/records", json=valid_payload)

    client.post("/api/data/seed-demo", params={"days": 20, "replace": True})
    client.post("/api/data/seed-demo", params={"days": 20, "replace": True})

    summary = client.get("/api/usage/summary").json()
    assert summary["records_by_source"]["manual"] == 1


def test_unknown_demo_profile_is_refused(client):
    response = client.post("/api/data/seed-demo", params={"profile": "nonexistent"})
    assert response.status_code == 400
