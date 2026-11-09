"""Schemas for device sync.

WHY SYNC NEEDS DIFFERENT SEMANTICS FROM MANUAL ENTRY
----------------------------------------------------
Manual entry refuses to overwrite an existing day (HTTP 409), because
silently replacing data a person typed would make their history
untrustworthy.

Device sync needs the opposite behaviour for a specific, non-negotiable
reason: today's usage is still accumulating. A sync at 2pm reports fewer
minutes than a sync at 10pm for the same date, and the later figure is the
correct one. Refusing the update would freeze every day at whatever its first
sync happened to catch.

So sync UPSERTS, with two protections:

  1. Only rows whose `source` is DEVICE may be overwritten by a sync. A day
     the user entered by hand, or imported from a CSV, is never clobbered by
     the phone - those are reported as skipped with the reason.

  2. Fields the device could not measure are sent as null and do NOT
     overwrite an existing non-null value. Android cannot backfill
     notification counts, so a sync of last week must not wipe a notification
     figure that arrived from somewhere else.
"""

from __future__ import annotations

import datetime as dt

from dataclasses import dataclass, field

from app.schemas.usage import (
    check_internal_consistency,
    check_range,
    coerce_date,
)

MINUTES_IN_DAY = 1440

#: The band within which a sleep figure DERIVED FROM INACTIVITY is believable.
#:
#: These two numbers must stay equal to MIN/MAX_PLAUSIBLE_SLEEP_HOURS in
#: modules/usage-stats/android/.../DeviceSignals.kt. They are repeated here on
#: purpose, because keeping the rule only on the device turned out to be a real
#: defect rather than a theoretical one:
#:
#:   A sleep estimate of 13.6 h was collected while the band was wider. The
#:   band was then tightened on the device, so the phone stopped sending that
#:   figure - but the backend had already stored it, had no opinion about it
#:   (it accepted anything from 0 to 24), and so kept serving a number the
#:   system no longer stood behind.
#:
#: Enforcing the band at the point where data persists means a figure outside
#: it cannot be stored by any client, and a stored figure is re-checked on
#: every sync instead of being grandfathered in.
MIN_PLAUSIBLE_SLEEP_HOURS = 4.0
MAX_PLAUSIBLE_SLEEP_HOURS = 11.0

#: How much of a day may go unobserved before its notification count is
#: unusable.
#:
#: Must stay equal to MAX_UNOBSERVED_MINUTES in
#: modules/usage-stats/android/.../NotificationCounterService.kt, duplicated
#: for the same reason as the sleep band: the rule has to hold even when an
#: older client sends something it should not.
#:
#: NOT ZERO, deliberately. Android unbinds notification listeners routinely -
#: at reboot, on app update, under battery-saver teardown - and rebinds within
#: seconds to a couple of minutes. A zero tolerance would let a single reboot
#: disqualify a whole day, so a phone restarted daily would never report a
#: notification count at all: technically correct and useless.
#:
#: 15 minutes bounds the possible understatement at roughly 1% of a day, well
#: below the noise in a daily notification total, while still rejecting every
#: case this rule exists for - the day access is granted leaves ~1359 minutes
#: unobserved, and a day inside a revoked gap leaves all of them.
MAX_UNOBSERVED_MINUTES = 15


@dataclass(kw_only=True)
class DeviceDayPayload:
    """One day of usage as measured on the device.

    Every behavioural field is optional because Android genuinely cannot
    supply all of them. Notification counts and battery drain have no
    historical record on the device at all, so for past days they arrive as
    null - which means "not known", never zero.
    """

    record_date: dt.date
    total_screen_minutes: int

    social_minutes: int | None = None
    gaming_minutes: int | None = None
    productivity_minutes: int | None = None
    app_launches: int | None = None
    unlock_count: int | None = None
    notification_count: int | None = None

    #: How much of this local day the notification listener was observing, and
    #: the day's own length in minutes.
    #:
    #: The day's length is sent rather than assumed to be 1440, because a
    #: daylight-saving change makes a day 23 or 25 hours long, and comparing
    #: such a day against a hard-coded 1440 would mark a fully observed day
    #: incomplete (or an incomplete one full).
    #:
    #: Both are optional because an older app build does not send them. Absent,
    #: the backend cannot verify coverage and passes the count through - see
    #: the model validator. Treating "unverifiable" as "incomplete" would wipe
    #: real notification history the moment the backend was upgraded ahead of
    #: the app.
    notification_observed_minutes: int | None = None
    notification_day_minutes: int | None = None
    calls_count: int | None = None
    call_duration_minutes: int | None = None
    battery_drain_percent: float | None = None
    late_night_minutes: int | None = None
    sleep_hours: float | None = None

    #: True when sleep_hours is an estimate derived from inactivity rather
    #: than a measurement. Recorded in the note so it is never mistaken for
    #: observed data.
    sleep_is_estimated: bool = False

    #: False when Android reported no activity for this date. That is NOT the
    #: same as a confirmed zero-usage day: the device retains detailed events
    #: for only about a week, so an older day with no events is simply
    #: unknown. Such days are skipped rather than stored as zero.
    had_any_usage: bool = True

    def __post_init__(self) -> None:
        """Validation, replacing pydantic's field and model validators.

        Device data is not trusted more than typed data: the cross-field rule
        is literally the same function manual entry calls, so the two cannot
        drift apart. See app/schemas/usage.py for why these are dataclasses.
        """
        # Accept an ISO string as pydantic did; several callers rely on it.
        self.record_date = coerce_date(self.record_date)

        today = dt.date.today()
        if self.record_date > today:
            raise ValueError(
                f"record_date {self.record_date} is in the future (today is {today})"
            )

        # Outcomes of the two discard rules below, read by sync_service so the
        # response can explain a blank rather than leaving the user guessing.
        self._sleep_rejected: str | None = None
        self._notifications_rejected: str | None = None

        check_range("total_screen_minutes", self.total_screen_minutes, 0, MINUTES_IN_DAY)
        for name in (
            "social_minutes",
            "gaming_minutes",
            "productivity_minutes",
            "call_duration_minutes",
            "late_night_minutes",
        ):
            check_range(name, getattr(self, name), 0, MINUTES_IN_DAY)
        for name in ("app_launches", "unlock_count", "notification_count"):
            check_range(name, getattr(self, name), 0, 10_000)
        for name in ("notification_observed_minutes", "notification_day_minutes"):
            check_range(name, getattr(self, name), 0, 2 * MINUTES_IN_DAY)
        check_range("calls_count", self.calls_count, 0, 500)
        check_range("battery_drain_percent", self.battery_drain_percent, 0, 100)
        check_range("sleep_hours", self.sleep_hours, 0, 24)

        check_internal_consistency(
            self.total_screen_minutes,
            self.social_minutes,
            self.gaming_minutes,
            self.productivity_minutes,
            self.late_night_minutes,
        )

        # A notification count is only meaningful for a day the listener
        # watched from start to finish.
        #
        # Android exposes no notification history, so a partially observed day
        # yields a tally of the hours that WERE watched - a number that looks
        # like a quiet day and is actually a short measurement. Two cases
        # produce one routinely:
        #
        #   the day access was granted, counted only from that moment;
        #   any day inside a gap where access had been revoked and later
        #   restored, which is the case that used to slip through entirely.
        #
        # Only this field is dropped. Screen time, launches, unlocks, calls
        # and the sleep estimate come from UsageStatsManager and the call log,
        # which are queried historically and are unaffected by whether a
        # notification listener happened to be bound - so discarding the whole
        # day would throw away good measurements to punish one bad one.
        if (
            self.notification_count is not None
            and self.notification_observed_minutes is not None
            and self.notification_day_minutes is not None
            and self.notification_day_minutes
            - self.notification_observed_minutes
            > MAX_UNOBSERVED_MINUTES
        ):
            observed = self.notification_observed_minutes
            total = self.notification_day_minutes
            share = observed / total if total else 0.0
            self._notifications_rejected = (
                f"a count of {self.notification_count} was discarded: the "
                f"notification listener observed only {observed} of this "
                f"day's {total} observable minutes "
                f"({share:.0%}), leaving {total - observed} unobserved "
                f"against a {MAX_UNOBSERVED_MINUTES}-minute tolerance. The "
                "tally therefore covers part of the day and would read as a "
                "quiet day rather than a short measurement. Every other field "
                "for this day is kept."
            )
            self.notification_count = None

        # An ESTIMATED sleep figure outside the plausible band is discarded
        # rather than rejected.
        #
        # Discarded, not a 422: the estimate is one derived field among a dozen
        # measured ones. Refusing the whole day would throw away genuine screen
        # time because an inference about sleep came out wrong - the estimate is
        # the only part that is unsafe, so the estimate is the only part dropped.
        #
        # Measured sleep (sleep_is_estimated=False) is left alone. The band
        # describes what can be inferred from a gap in phone activity, and has
        # no authority over a figure that was actually observed.
        if (
            self.sleep_hours is not None
            and self.sleep_is_estimated
            and not (
                MIN_PLAUSIBLE_SLEEP_HOURS <= self.sleep_hours <= MAX_PLAUSIBLE_SLEEP_HOURS
            )
        ):
            side = "above" if self.sleep_hours > MAX_PLAUSIBLE_SLEEP_HOURS else "below"
            self._sleep_rejected = (
                f"an estimated {self.sleep_hours:.1f} h was discarded as "
                f"implausible ({side} the "
                f"{MIN_PLAUSIBLE_SLEEP_HOURS:.0f}-{MAX_PLAUSIBLE_SLEEP_HOURS:.0f} h "
                "band for sleep inferred from inactivity); the phone was most "
                "likely simply unused for that stretch"
            )
            self.sleep_hours = None


@dataclass(kw_only=True)
class SyncRequest:
    """A batch of measured days from the device."""

    days: list[DeviceDayPayload]

    #: Which permissions were granted when this batch was collected. Recorded
    #: so the app can explain WHY a field is missing rather than leaving the
    #: user guessing.
    permissions: dict[str, bool] = field(default_factory=dict)

    #: Free-text provenance, e.g. the Android version and module version.
    device_note: str | None = None

    def __post_init__(self) -> None:
        # Previously Field(min_length=1, max_length=400). An empty batch is a
        # client bug worth surfacing rather than a silent no-op, and the upper
        # bound stops a malformed request from trying to write a decade of
        # rows in one transaction.
        if not self.days:
            raise ValueError("a sync must contain at least one day")
        if len(self.days) > 400:
            raise ValueError(
                f"a sync may contain at most 400 days; got {len(self.days)}"
            )
        if self.device_note is not None and len(self.device_note) > 280:
            raise ValueError(
                f"device_note is {len(self.device_note)} characters; the maximum is 280"
            )


@dataclass(kw_only=True)
class SyncDaySkipped:
    record_date: dt.date
    reason: str


@dataclass(kw_only=True)
class SyncResponse:
    """What the sync actually did, per day."""

    days_received: int
    created: int
    updated: int
    skipped: int
    skipped_detail: list[SyncDaySkipped] = field(default_factory=list)

    #: Dates whose stored row now reflects what the device sent.
    #:
    #: Needed because `skipped` conflates two opposite outcomes, and the
    #: device cannot tell them apart from the counts alone:
    #:
    #:   "already up to date; no values changed" - fully accepted, nothing to
    #:   do. The day is in the database exactly as measured.
    #:
    #:   "an existing record came from 'manual'" - genuinely refused. The
    #:   device's version was NOT stored.
    #:
    #: The phone keeps captured days until the backend confirms them, so
    #: without this distinction the first kind would stay queued forever and
    #: be re-sent on every capture. Matching on the reason text instead would
    #: make a wording change silently break the queue.
    accepted_dates: list[dt.date] = field(default_factory=list)
    #: Fields that arrived null for every day, with the likely reason. Lets
    #: the app tell the user which permission would fill them.
    missing_fields: dict[str, str] = field(default_factory=dict)
    first_date: dt.date | None = None
    last_date: dt.date | None = None
    notes: list[str] = field(default_factory=list)
