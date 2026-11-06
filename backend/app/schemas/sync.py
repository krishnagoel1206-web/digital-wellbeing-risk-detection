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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
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


class DeviceDayPayload(BaseModel):
    """One day of usage as measured on the device.

    Every behavioural field is optional because Android genuinely cannot
    supply all of them. Notification counts and battery drain have no
    historical record on the device at all, so for past days they arrive as
    null - which means "not known", never zero.
    """

    model_config = ConfigDict(extra="forbid")

    record_date: dt.date
    total_screen_minutes: int = Field(ge=0, le=MINUTES_IN_DAY)

    social_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    gaming_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    productivity_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    app_launches: int | None = Field(default=None, ge=0, le=10_000)
    unlock_count: int | None = Field(default=None, ge=0, le=10_000)
    notification_count: int | None = Field(default=None, ge=0, le=10_000)

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
    notification_observed_minutes: int | None = Field(
        default=None, ge=0, le=2 * MINUTES_IN_DAY
    )
    notification_day_minutes: int | None = Field(
        default=None, ge=0, le=2 * MINUTES_IN_DAY
    )
    calls_count: int | None = Field(default=None, ge=0, le=500)
    call_duration_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    battery_drain_percent: float | None = Field(default=None, ge=0, le=100)
    late_night_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)

    #: True when sleep_hours is an estimate derived from inactivity rather
    #: than a measurement. Recorded in the note so it is never mistaken for
    #: observed data.
    sleep_is_estimated: bool = False

    #: Set when an estimated sleep figure was discarded as implausible, so
    #: the sync response can say so rather than just showing a blank.
    _sleep_rejected: str | None = PrivateAttr(default=None)

    #: Set when a notification count was discarded for incomplete coverage.
    _notifications_rejected: str | None = PrivateAttr(default=None)

    #: False when Android reported no activity for this date. That is NOT the
    #: same as a confirmed zero-usage day: the device retains detailed events
    #: for only about a week, so an older day with no events is simply
    #: unknown. Such days are skipped rather than stored as zero.
    had_any_usage: bool = True

    @field_validator("record_date")
    @classmethod
    def _reject_future(cls, value: dt.date) -> dt.date:
        today = dt.date.today()
        if value > today:
            raise ValueError(f"record_date {value} is in the future (today is {today})")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> DeviceDayPayload:
        """The same cross-field rules manual entry enforces.

        Device data is not trusted more than typed data. If the reconstruction
        produced category minutes exceeding the total, that is a bug in the
        native module and must surface here rather than reaching the database.
        """
        categories = {
            "social_minutes": self.social_minutes,
            "gaming_minutes": self.gaming_minutes,
            "productivity_minutes": self.productivity_minutes,
        }
        present = {k: v for k, v in categories.items() if v is not None}
        if present:
            subtotal = sum(present.values())
            if subtotal > self.total_screen_minutes:
                detail = ", ".join(f"{k}={v}" for k, v in present.items())
                raise ValueError(
                    f"category minutes ({detail}; total {subtotal}) exceed "
                    f"total_screen_minutes={self.total_screen_minutes}"
                )

        if (
            self.late_night_minutes is not None
            and self.late_night_minutes > self.total_screen_minutes
        ):
            raise ValueError(
                f"late_night_minutes={self.late_night_minutes} exceeds "
                f"total_screen_minutes={self.total_screen_minutes}"
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

        return self


class SyncRequest(BaseModel):
    """A batch of measured days from the device."""

    model_config = ConfigDict(extra="forbid")

    days: list[DeviceDayPayload] = Field(min_length=1, max_length=400)

    #: Which permissions were granted when this batch was collected. Recorded
    #: so the app can explain WHY a field is missing rather than leaving the
    #: user guessing.
    permissions: dict[str, bool] = Field(default_factory=dict)

    #: Free-text provenance, e.g. the Android version and module version.
    device_note: str | None = Field(default=None, max_length=280)


class SyncDaySkipped(BaseModel):
    record_date: dt.date
    reason: str


class SyncResponse(BaseModel):
    """What the sync actually did, per day."""

    days_received: int
    created: int
    updated: int
    skipped: int
    skipped_detail: list[SyncDaySkipped] = Field(default_factory=list)

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
    accepted_dates: list[dt.date] = Field(default_factory=list)
    #: Fields that arrived null for every day, with the likely reason. Lets
    #: the app tell the user which permission would fill them.
    missing_fields: dict[str, str] = Field(default_factory=dict)
    first_date: dt.date | None = None
    last_date: dt.date | None = None
    notes: list[str] = Field(default_factory=list)
