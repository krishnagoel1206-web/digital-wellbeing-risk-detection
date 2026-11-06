"""Tests for device sync.

The upsert rules carry real consequences, and each has already been wrong
once in a way that produced visibly incorrect data. These tests pin the
corrected behaviour.
"""

from __future__ import annotations

import datetime as dt

TODAY = dt.date.today()


def _day(offset: int, **overrides) -> dict:
    """A device day, `offset` days before today.

    Category minutes are derived as FRACTIONS of the total rather than fixed
    numbers. An earlier version used constants, so overriding the total to a
    small value produced categories exceeding it - and the API correctly
    rejected the row with 422, making the test look like a sync bug when the
    fixture was simply invalid.
    """
    total = overrides.pop("total_screen_minutes", 400)

    payload = {
        "record_date": (TODAY - dt.timedelta(days=offset)).isoformat(),
        "total_screen_minutes": total,
        "social_minutes": int(total * 0.30),
        "gaming_minutes": int(total * 0.08),
        "productivity_minutes": int(total * 0.15),
        "app_launches": 200,
        "unlock_count": 90,
        "notification_count": None,
        # Full coverage by default: these tests are about the upsert rules,
        # not about notification coverage, so the default day must not trip
        # the coverage check. The coverage tests set these explicitly.
        "notification_observed_minutes": 1440,
        "notification_day_minutes": 1440,
        "calls_count": 5,
        "call_duration_minutes": 12,
        "battery_drain_percent": None,
        "late_night_minutes": int(total * 0.10),
        "sleep_hours": 7.0,
        "sleep_is_estimated": True,
        "had_any_usage": True,
    }
    payload.update(overrides)
    return payload


def _sync(client, days: list[dict], **extra):
    return client.post("/api/data/sync", json={"days": days, **extra})


# --------------------------------------------------------------------------
# Basic upsert
# --------------------------------------------------------------------------


def test_sync_creates_days(client):
    response = _sync(client, [_day(1), _day(2)])
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["created"] == 2
    assert body["updated"] == 0

    stored = client.get("/api/usage/records").json()
    assert len(stored) == 2
    assert all(r["source"] == "device" for r in stored)


def test_resyncing_the_same_day_updates_it(client):
    """Today's usage grows through the day, so a later sync must win."""
    _sync(client, [_day(0, total_screen_minutes=200)])
    response = _sync(client, [_day(0, total_screen_minutes=380)])

    assert response.json()["updated"] == 1
    record = client.get("/api/usage/records").json()[0]
    assert record["total_screen_minutes"] == 380


# --------------------------------------------------------------------------
# Rule 1: what may and may not be overwritten
# --------------------------------------------------------------------------


def test_sync_never_overwrites_a_manual_entry(client, valid_payload):
    """Data the user typed is theirs. A sync must not silently replace it."""
    date = (TODAY - dt.timedelta(days=3)).isoformat()
    client.post("/api/usage/records", json=valid_payload | {"record_date": date})

    body = _sync(client, [_day(3, total_screen_minutes=999)]).json()

    assert body["created"] == 0
    assert body["updated"] == 0
    assert body["skipped"] == 1
    assert "entries you made" in body["skipped_detail"][0]["reason"]

    # The manual value survived untouched.
    record = client.get("/api/usage/records").json()[0]
    assert record["source"] == "manual"
    assert record["total_screen_minutes"] == valid_payload["total_screen_minutes"]


def test_sync_replaces_generated_demo_data(client):
    """A real measurement must win over demonstration filler.

    The first version of this rule protected synthetic rows too. The effect
    was absurd: the device read 7 genuine days, 6 collided with generated
    demo days and were refused, and only one was stored.
    """
    client.post("/api/data/seed-demo", params={"days": 30, "seed": 1})

    before = client.get("/api/usage/summary").json()
    assert before["records_by_source"]["synthetic"] == 30

    body = _sync(client, [_day(1, total_screen_minutes=321)]).json()
    assert body["updated"] == 1
    assert body["skipped"] == 0

    after = client.get("/api/usage/summary").json()
    assert after["records_by_source"]["device"] == 1
    assert after["records_by_source"]["synthetic"] == 29

    target = (TODAY - dt.timedelta(days=1)).isoformat()
    record = next(
        r for r in client.get("/api/usage/records").json() if r["record_date"] == target
    )
    assert record["source"] == "device"
    assert record["total_screen_minutes"] == 321


# --------------------------------------------------------------------------
# Rule 2: when a null overwrites
# --------------------------------------------------------------------------


def test_a_withdrawn_estimate_clears_the_stored_value(client):
    """Regression test for a live bug.

    A sleep estimate of 13.6 h was stored before the plausibility band was
    tightened. Afterwards the device correctly sent null for that day - and
    the discredited value survived, because the old rule refused every null.

    This test sets up with a figure the band ACCEPTS, because a 13.6 h
    estimate can no longer be stored through sync at all (see
    test_an_implausible_sleep_estimate_is_discarded_not_stored). The two rules
    are independent and both still matter: the band stops a bad estimate
    entering, and this rule lets a stored estimate be withdrawn when a later
    sync no longer stands behind it.
    """
    _sync(client, [_day(2, sleep_hours=7.0)])
    assert client.get("/api/usage/records").json()[0]["sleep_hours"] == 7.0

    # Same day, but this time the device has nothing to report for sleep.
    _sync(client, [_day(2, sleep_hours=None)])

    record = client.get("/api/usage/records").json()[0]
    assert record["sleep_hours"] is None, "a withdrawn estimate was not cleared"


def test_unknowable_fields_are_not_wiped_by_a_null(client):
    """Notification counts must survive a sync of older days.

    Android keeps no notification history, so syncing last week sends null
    for those days. That null means "I cannot see this", not "there were
    none", and must leave a previously collected count alone.
    """
    _sync(client, [_day(1, notification_count=240)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 240

    # A later sync covering the same day cannot see notifications for it.
    _sync(client, [_day(1, notification_count=None)])

    record = client.get("/api/usage/records").json()[0]
    assert record["notification_count"] == 240, "an unknowable null wiped real data"


# --------------------------------------------------------------------------
# Rule 3: days with no activity
# --------------------------------------------------------------------------


def test_days_with_no_reported_activity_are_skipped_not_zeroed(client):
    """Android cannot distinguish "unused" from "older than my retention".

    Storing such a day as zero would feed the personal baseline a fabricated
    quiet day and make genuine quiet days look normal by comparison.
    """
    body = _sync(client, [_day(9, total_screen_minutes=0, had_any_usage=False)]).json()

    assert body["created"] == 0
    assert body["skipped"] == 1
    assert "no activity" in body["skipped_detail"][0]["reason"]
    assert client.get("/api/usage/records").json() == []


# --------------------------------------------------------------------------
# Validation parity with manual entry
# --------------------------------------------------------------------------


def test_device_data_is_validated_as_strictly_as_typed_data(client):
    """Device data is not trusted more than human data.

    If session reconstruction ever produced category minutes exceeding the
    daily total, that is a bug in the native module and must surface here
    rather than reaching the database.
    """
    response = _sync(
        client,
        [_day(1, total_screen_minutes=60, social_minutes=180, gaming_minutes=0,
              productivity_minutes=0, late_night_minutes=0)],
    )
    assert response.status_code == 422
    assert "exceed" in response.text


def test_future_dates_are_rejected(client):
    response = _sync(client, [_day(-2)])  # two days in the future
    assert response.status_code == 422
    assert "future" in response.text


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_missing_fields_are_explained_not_just_blank(client):
    body = _sync(client, [_day(1), _day(2)]).json()

    # notification_count was null on every day, so it must be reported with
    # a reason the user can act on.
    assert "notification_count" in body["missing_fields"]
    assert "Android" in body["missing_fields"]["notification_count"]
    assert "battery_drain_percent" in body["missing_fields"]


def test_sync_status_reports_per_field_coverage(client):
    _sync(client, [_day(1, notification_count=100), _day(2)])

    status = client.get("/api/data/sync-status").json()
    assert status["device_days"] == 2
    # One of the two days carries a notification count; the other does not.
    assert status["field_coverage"]["notification_count"] == 1
    assert status["field_coverage"]["unlock_count"] == 2


# --------------------------------------------------------------------------
# The sleep plausibility band
# --------------------------------------------------------------------------


def test_an_implausible_sleep_estimate_is_discarded_not_stored(client):
    """The band is enforced where data persists, not only on the device.

    A 13.6 h estimate was collected while the band was wider, the band was
    then tightened on the phone, and the already-stored figure outlived the
    rule that rejected it - the backend had no opinion on sleep beyond 0-24 h.
    """
    response = _sync(client, [_day(1, sleep_hours=13.6, sleep_is_estimated=True)])
    assert response.status_code == 200, response.text

    record = client.get("/api/usage/records").json()[0]
    assert record["sleep_hours"] is None

    # The rest of the day is intact: one bad derived field must not cost a
    # day of genuine screen time.
    assert record["total_screen_minutes"] == 400

    # And the discard is explained rather than left as a silent blank.
    assert any("discarded as implausible" in n for n in response.json()["notes"])


def test_a_plausible_sleep_estimate_is_kept(client):
    _sync(client, [_day(1, sleep_hours=7.5, sleep_is_estimated=True)])
    assert client.get("/api/usage/records").json()[0]["sleep_hours"] == 7.5


def test_the_band_does_not_apply_to_measured_sleep(client):
    """The band describes what can be INFERRED from a gap in activity.

    It has no authority over a figure that was actually observed, so a
    measured 13.6 h is stored as given.
    """
    _sync(client, [_day(1, sleep_hours=13.6, sleep_is_estimated=False)])
    assert client.get("/api/usage/records").json()[0]["sleep_hours"] == 13.6


# --------------------------------------------------------------------------
# Notification coverage
# --------------------------------------------------------------------------


def test_a_partially_observed_day_has_its_count_discarded(client):
    """The day notification access is granted is counted only from that hour.

    Measured on the real device: access was granted at 22:38 on 2026-11-05,
    and that day's tally was 72 - a figure covering 81 minutes, which would
    read as a very quiet day rather than as a short measurement.
    """
    response = _sync(
        client,
        [
            _day(
                3,
                notification_count=72,
                notification_observed_minutes=81,
                notification_day_minutes=1440,
            )
        ],
    )
    assert response.status_code == 200, response.text

    record = client.get("/api/usage/records").json()[0]
    assert record["notification_count"] is None

    assert any("discarded for incomplete coverage" in n for n in response.json()["notes"])


def test_discarding_the_count_keeps_every_other_measurement(client):
    """Only the notification field is dropped.

    Screen time, launches, unlocks, calls and the sleep estimate come from
    UsageStatsManager and the call log, which are queried historically and do
    not care whether a notification listener happened to be bound.
    """
    _sync(
        client,
        [
            _day(
                3,
                total_screen_minutes=500,
                notification_count=72,
                notification_observed_minutes=81,
                notification_day_minutes=1440,
            )
        ],
    )

    record = client.get("/api/usage/records").json()[0]
    assert record["notification_count"] is None
    assert record["total_screen_minutes"] == 500
    assert record["social_minutes"] == 150
    assert record["unlock_count"] == 90
    assert record["calls_count"] == 5
    assert record["sleep_hours"] == 7.0


def test_a_fully_observed_day_keeps_its_count(client):
    _sync(
        client,
        [
            _day(
                2,
                notification_count=214,
                notification_observed_minutes=1440,
                notification_day_minutes=1440,
            )
        ],
    )
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 214


def test_a_daylight_saving_day_is_judged_against_its_own_length(client):
    """A 23-hour day fully observed is still fully observed.

    The day's length travels with the payload rather than being assumed to be
    1440, so a short day is not mistaken for incomplete coverage.
    """
    _sync(
        client,
        [
            _day(
                2,
                notification_count=190,
                notification_observed_minutes=1380,
                notification_day_minutes=1380,
            )
        ],
    )
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 190


def test_a_revoked_day_is_not_recorded_as_zero_notifications(client):
    """The defect that motivated observation windows.

    `observing_since_ms` was written once and never reset, and revoking
    notification access does not clear SharedPreferences. So after
    revoke -> gap -> re-grant, every day in the gap passed the old
    `observingSince <= dayStart` test and took the `?: 0` branch - asserting
    "no notifications arrived" for days nobody was watching.
    """
    _sync(
        client,
        [
            _day(
                4,
                notification_count=0,
                notification_observed_minutes=0,
                notification_day_minutes=1440,
            )
        ],
    )

    record = client.get("/api/usage/records").json()[0]
    assert record["notification_count"] is None, (
        "a day nobody observed was stored as a confirmed zero"
    )


def test_an_unverifiable_count_is_passed_through(client):
    """An older app build sends no coverage, and must not lose its data.

    Treating "cannot verify" as "incomplete" would wipe real notification
    history the moment the backend was upgraded ahead of the app.
    """
    _sync(
        client,
        [
            _day(
                2,
                notification_count=180,
                notification_observed_minutes=None,
                notification_day_minutes=None,
            )
        ],
    )
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 180


def test_a_rejected_count_clears_one_already_stored(client):
    """Rule 2b: a null that is a VERDICT must overwrite.

    Rule 2 protects notification_count from nulls, because Android cannot
    backfill them. But a rejection is a statement, and refusing it would pin a
    discredited count in place forever - the same trap that kept a 13.6 h
    sleep estimate alive after the plausibility band was tightened.
    """
    # An older build stored a partial count, with no coverage to check it by.
    _sync(client, [_day(3, notification_count=72,
                        notification_observed_minutes=None,
                        notification_day_minutes=None)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 72

    # The upgraded app reports the same day, now with coverage attached.
    _sync(client, [_day(3, notification_count=72,
                        notification_observed_minutes=81,
                        notification_day_minutes=1440)])

    record = client.get("/api/usage/records").json()[0]
    assert record["notification_count"] is None, (
        "a count the device has disowned survived in the database"
    )


def test_an_ordinary_unknowable_null_still_does_not_overwrite(client):
    """Rule 2b must not swallow Rule 2.

    Syncing an older day sends null for notifications with no coverage verdict
    attached, and that null must still leave a stored count alone.
    """
    _sync(client, [_day(1, notification_count=240)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 240

    _sync(client, [_day(1, notification_count=None)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 240


def test_a_brief_listener_restart_does_not_disqualify_the_day(client):
    """A zero tolerance would make this feature useless.

    Android unbinds notification listeners at reboot, on app update and under
    battery-saver teardown, rebinding within seconds to a couple of minutes.
    If any gap disqualified a day, a phone restarted daily would never report
    a single notification count.
    """
    _sync(
        client,
        [
            _day(
                2,
                notification_count=205,
                notification_observed_minutes=1430,  # a 10-minute reboot gap
                notification_day_minutes=1440,
            )
        ],
    )
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 205


def test_the_tolerance_boundary_is_where_it_claims_to_be(client):
    """Exactly at the tolerance is accepted; one minute beyond is not.

    Pinned because the threshold is a stated judgement, and a silent drift in
    it would quietly change which days the models see.
    """
    from app.schemas.sync import MAX_UNOBSERVED_MINUTES

    day = 1440
    at_limit = day - MAX_UNOBSERVED_MINUTES
    beyond = at_limit - 1

    _sync(client, [_day(2, notification_count=205,
                        notification_observed_minutes=at_limit,
                        notification_day_minutes=day)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 205

    _sync(client, [_day(2, notification_count=205,
                        notification_observed_minutes=beyond,
                        notification_day_minutes=day)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] is None


def test_a_day_in_progress_is_judged_against_the_part_that_exists(client):
    """Coverage for today is measured against elapsed time, not 1440.

    The device sends the observable minutes so far. Comparing against a full
    day would make every sync before midnight look like a coverage failure and
    throw away today's count for no reason.
    """
    _sync(client, [_day(0, notification_count=67,
                        notification_observed_minutes=61,
                        notification_day_minutes=61)])
    assert client.get("/api/usage/records").json()[0]["notification_count"] == 67


# --------------------------------------------------------------------------
# accepted_dates: what the phone may stop queueing
# --------------------------------------------------------------------------


def test_accepted_dates_covers_newly_stored_days(client):
    body = _sync(client, [_day(1), _day(2)]).json()

    expected = {
        (TODAY - dt.timedelta(days=1)).isoformat(),
        (TODAY - dt.timedelta(days=2)).isoformat(),
    }
    assert set(body["accepted_dates"]) == expected


def test_an_unchanged_day_is_still_accepted(client):
    """The bug this field exists to fix.

    Re-sending an identical day lands in `skipped` with the reason "already up
    to date; no values changed" - which is full acceptance, not a refusal. The
    phone keeps captured days until the backend confirms them, so treating
    that bucket as unaccepted left days queued forever and re-sent on every
    capture. Observed on real data: five of seven days stuck pending with
    nothing actually wrong.
    """
    _sync(client, [_day(1)])
    body = _sync(client, [_day(1)]).json()

    target = (TODAY - dt.timedelta(days=1)).isoformat()
    assert body["skipped"] == 1, "expected the unchanged day to land in skipped"
    assert "already up to date" in body["skipped_detail"][0]["reason"]
    assert target in body["accepted_dates"], (
        "an accepted day was reported in a way that keeps it queued forever"
    )


def test_a_refused_day_is_not_accepted(client, valid_payload):
    """A manual entry is genuinely not overwritten, so it must stay queued.

    The two cases both appear in `skipped`, which is exactly why the counts
    alone cannot drive the phone's queue.
    """
    date = (TODAY - dt.timedelta(days=3)).isoformat()
    client.post("/api/usage/records", json=valid_payload | {"record_date": date})

    body = _sync(client, [_day(3)]).json()

    assert body["skipped"] == 1
    assert body["accepted_dates"] == []


def test_a_day_with_no_activity_is_not_accepted(client):
    """Nothing was stored, so the phone should not consider it done."""
    body = _sync(client, [_day(9, total_screen_minutes=0, had_any_usage=False)]).json()
    assert body["accepted_dates"] == []
