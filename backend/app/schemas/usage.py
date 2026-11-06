"""Pydantic schemas for usage records - the API's validation boundary.

The validators here are not decoration. They encode real constraints that
catch genuinely impossible data before it reaches the database:

  * A day cannot contain more than 1440 minutes of screen time.
  * The per-category minutes (social + gaming + productivity) cannot exceed
    the total screen time. A row claiming 3h social on a 1h day is wrong, and
    silently storing it would corrupt every per-app ratio feature.
  * Late-night minutes cannot exceed total screen minutes.
  * A record cannot be dated in the future.

Catching these at the boundary is what stops bad input from quietly becoming
a bad model later.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.database.models import DataSource

MINUTES_IN_DAY = 1440


class UsageRecordBase(BaseModel):
    """Fields shared by create and read schemas."""

    record_date: dt.date = Field(description="The calendar day these figures describe.")

    total_screen_minutes: int = Field(
        ge=0,
        le=MINUTES_IN_DAY,
        description="Total minutes the screen was on and in use.",
    )

    social_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    gaming_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    productivity_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)

    app_launches: int | None = Field(default=None, ge=0, le=10_000)
    notification_count: int | None = Field(default=None, ge=0, le=10_000)
    unlock_count: int | None = Field(default=None, ge=0, le=10_000)

    calls_count: int | None = Field(default=None, ge=0, le=500)
    call_duration_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)

    battery_drain_percent: float | None = Field(default=None, ge=0, le=100)
    late_night_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)

    note: str | None = Field(default=None, max_length=280)

    @field_validator("record_date")
    @classmethod
    def _reject_future_dates(cls, value: dt.date) -> dt.date:
        """A usage record describes a day that has happened."""
        today = dt.date.today()
        if value > today:
            raise ValueError(f"record_date {value} is in the future (today is {today})")
        return value

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> UsageRecordBase:
        """Cross-field checks that no single field can perform alone."""
        # Category minutes must fit inside the total.
        categories = {
            "social_minutes": self.social_minutes,
            "gaming_minutes": self.gaming_minutes,
            "productivity_minutes": self.productivity_minutes,
        }
        present = {name: v for name, v in categories.items() if v is not None}
        if present:
            subtotal = sum(present.values())
            if subtotal > self.total_screen_minutes:
                detail = ", ".join(f"{n}={v}" for n, v in present.items())
                raise ValueError(
                    f"category minutes ({detail}; total {subtotal}) exceed "
                    f"total_screen_minutes={self.total_screen_minutes}"
                )

        # Late-night usage is a subset of total usage.
        if (
            self.late_night_minutes is not None
            and self.late_night_minutes > self.total_screen_minutes
        ):
            raise ValueError(
                f"late_night_minutes={self.late_night_minutes} exceeds "
                f"total_screen_minutes={self.total_screen_minutes}"
            )

        return self


class UsageRecordCreate(UsageRecordBase):
    """Payload for creating a record.

    `source` defaults to MANUAL. The CSV importer and the synthetic generator
    set it explicitly so that demonstration data is always distinguishable
    from data the user actually entered.
    """

    source: DataSource = DataSource.MANUAL


class UsageRecordUpdate(BaseModel):
    """Partial update. Every field optional; only what is sent is changed.

    Note this does NOT inherit the cross-field validators, because a partial
    payload cannot be checked in isolation - the service layer re-validates
    the merged record before saving.
    """

    model_config = ConfigDict(extra="forbid")

    total_screen_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    social_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    gaming_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    productivity_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    app_launches: int | None = Field(default=None, ge=0, le=10_000)
    notification_count: int | None = Field(default=None, ge=0, le=10_000)
    unlock_count: int | None = Field(default=None, ge=0, le=10_000)
    calls_count: int | None = Field(default=None, ge=0, le=500)
    call_duration_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    battery_drain_percent: float | None = Field(default=None, ge=0, le=100)
    late_night_minutes: int | None = Field(default=None, ge=0, le=MINUTES_IN_DAY)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)
    note: str | None = Field(default=None, max_length=280)


class UsageRecordRead(UsageRecordBase):
    """A record as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: DataSource
    created_at: dt.datetime
    updated_at: dt.datetime


class UsageSummary(BaseModel):
    """Aggregate view of the stored history.

    `has_enough_for_baseline` is the honest gate the brief requires: the app
    must not claim a reliable personal baseline until enough days exist.
    """

    total_records: int
    first_date: dt.date | None
    last_date: dt.date | None
    days_of_history: int
    records_by_source: dict[str, int]
    min_days_for_baseline: int
    has_enough_for_baseline: bool
    # True when ANY stored record is synthetic, so the UI can warn that
    # results are partly derived from demonstration data.
    contains_synthetic_data: bool
