"""Schemas for usage records - the API's validation boundary.

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

WHY THESE ARE PLAIN DATACLASSES RATHER THAN PYDANTIC MODELS
===========================================================
They were pydantic models. Pydantic v2's core is a compiled Rust extension
with no Android build, so anything importing this module could not run inside
the app's embedded interpreter - and that included usage_service,
sync_service and, transitively, the regression model.

Plain stdlib dataclasses solve it without giving anything up, which was
verified rather than assumed: FastAPI accepts a dataclass as a request body,
coerces types, reports missing fields, and turns a ValueError raised in
__post_init__ into the same HTTP 422 with the same message. So the laptop API
behaves exactly as before while this module imports cleanly on the phone.

The range checks that were `Field(ge=..., le=...)` are now explicit in
__post_init__. That is more lines, and the gain is that the rules are
ordinary Python which runs identically in both places.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass, field

from app.database.models import DataSource

MINUTES_IN_DAY = 1440


def check_range(name: str, value: float | None, low: float, high: float) -> None:
    """Reject a value outside its permitted range.

    Mirrors what `Field(ge=low, le=high)` did. None always passes: absence is
    handled by the field being optional, and is a different statement from a
    value that is out of range.
    """
    if value is None:
        return
    if value < low or value > high:
        raise ValueError(f"{name}={value} is outside the valid range {low} to {high}")


def _check_text(name: str, value: str | None, max_length: int) -> None:
    if value is not None and len(value) > max_length:
        raise ValueError(
            f"{name} is {len(value)} characters; the maximum is {max_length}"
        )


def coerce_date(value: object) -> dt.date:
    """Accept what pydantic used to accept, and nothing more.

    Pydantic coerced an ISO string to a date silently, and several internal
    callers relied on it without anyone noticing - the synthetic generator
    passes `record_date` as a string. Dropping pydantic dropped the coercion
    too, and the failure was not a clean type error but a comparison deep
    inside a validator:

        '>' not supported between instances of 'str' and 'datetime.date'

    Doing it explicitly here keeps those callers working and makes the
    conversion visible, which the implicit version never was.
    """
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"record_date {value!r} is not a valid ISO date") from exc
    raise ValueError(f"record_date must be a date or ISO string, got {type(value).__name__}")


def _reject_future(record_date: dt.date) -> None:
    """A usage record describes a day that has happened."""
    today = dt.date.today()
    if record_date > today:
        raise ValueError(
            f"record_date {record_date} is in the future (today is {today})"
        )


def check_internal_consistency(
    total_screen_minutes: int,
    social_minutes: int | None,
    gaming_minutes: int | None,
    productivity_minutes: int | None,
    late_night_minutes: int | None,
) -> None:
    """Cross-field checks that no single field can perform alone.

    Shared with app/schemas/sync.py: device data is validated exactly as
    strictly as typed data, and having ONE implementation is what guarantees
    that rather than merely claiming it.
    """
    categories = {
        "social_minutes": social_minutes,
        "gaming_minutes": gaming_minutes,
        "productivity_minutes": productivity_minutes,
    }
    present = {name: v for name, v in categories.items() if v is not None}
    if present:
        subtotal = sum(present.values())
        if subtotal > total_screen_minutes:
            detail = ", ".join(f"{n}={v}" for n, v in present.items())
            raise ValueError(
                f"category minutes ({detail}; total {subtotal}) exceed "
                f"total_screen_minutes={total_screen_minutes}"
            )

    if late_night_minutes is not None and late_night_minutes > total_screen_minutes:
        raise ValueError(
            f"late_night_minutes={late_night_minutes} exceeds "
            f"total_screen_minutes={total_screen_minutes}"
        )


@dataclass(kw_only=True)
class UsageRecordBase:
    """Fields shared by create and read schemas.

    kw_only because subclasses add required fields after optional ones, which
    a positional dataclass refuses to build.
    """

    record_date: dt.date

    total_screen_minutes: int

    social_minutes: int | None = None
    gaming_minutes: int | None = None
    productivity_minutes: int | None = None

    app_launches: int | None = None
    notification_count: int | None = None
    unlock_count: int | None = None

    calls_count: int | None = None
    call_duration_minutes: int | None = None

    battery_drain_percent: float | None = None
    late_night_minutes: int | None = None
    sleep_hours: float | None = None

    note: str | None = None

    def __post_init__(self) -> None:
        self.record_date = coerce_date(self.record_date)
        _reject_future(self.record_date)

        check_range(
            "total_screen_minutes", self.total_screen_minutes, 0, MINUTES_IN_DAY
        )
        for name in (
            "social_minutes",
            "gaming_minutes",
            "productivity_minutes",
            "call_duration_minutes",
            "late_night_minutes",
        ):
            check_range(name, getattr(self, name), 0, MINUTES_IN_DAY)
        for name in ("app_launches", "notification_count", "unlock_count"):
            check_range(name, getattr(self, name), 0, 10_000)
        check_range("calls_count", self.calls_count, 0, 500)
        check_range("battery_drain_percent", self.battery_drain_percent, 0, 100)
        check_range("sleep_hours", self.sleep_hours, 0, 24)
        _check_text("note", self.note, 280)

        check_internal_consistency(
            self.total_screen_minutes,
            self.social_minutes,
            self.gaming_minutes,
            self.productivity_minutes,
            self.late_night_minutes,
        )

    def model_dump(self, *, exclude: set[str] | None = None) -> dict:
        """Field values as a plain dict.

        Named `model_dump` deliberately: it keeps the call sites in
        usage_service unchanged, so swapping the validation library touched
        the schemas and almost nothing else.

        `exclude_unset` is NOT offered. A dataclass cannot tell an omitted
        field from one explicitly set to None, and pretending otherwise would
        silently break PATCH semantics. Partial updates take a mapping
        instead - see usage_service.update_record.
        """
        data = dataclasses.asdict(self)
        for name in exclude or ():
            data.pop(name, None)
        return data


@dataclass(kw_only=True)
class UsageRecordCreate(UsageRecordBase):
    """Payload for creating a record.

    `source` defaults to MANUAL. The CSV importer and the synthetic generator
    set it explicitly so that demonstration data is always distinguishable
    from data the user actually entered.
    """

    source: DataSource = DataSource.MANUAL


#: Fields a partial update may change.
#:
#: An explicit allow-list, because a PATCH body now arrives as a raw mapping.
#: Anything outside this set is rejected rather than ignored, which is what
#: `extra="forbid"` did before: a typo in a field name must fail loudly, not
#: appear to succeed while changing nothing.
UPDATABLE_FIELDS = frozenset(
    {
        "total_screen_minutes",
        "social_minutes",
        "gaming_minutes",
        "productivity_minutes",
        "app_launches",
        "notification_count",
        "unlock_count",
        "calls_count",
        "call_duration_minutes",
        "battery_drain_percent",
        "late_night_minutes",
        "sleep_hours",
        "note",
    }
)


@dataclass(kw_only=True)
class UsageRecordRead(UsageRecordBase):
    """A record as returned by the API."""

    id: int
    source: DataSource
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def from_record(cls, record) -> UsageRecordRead:
        """Build from a database row.

        Replaces pydantic's `model_validate(record)` plus from_attributes.
        The fields are listed explicitly rather than reflected off the ORM
        object, so a column added to the model does not silently start
        appearing in API responses.
        """
        return cls(
            id=record.id,
            record_date=record.record_date,
            total_screen_minutes=record.total_screen_minutes,
            social_minutes=record.social_minutes,
            gaming_minutes=record.gaming_minutes,
            productivity_minutes=record.productivity_minutes,
            app_launches=record.app_launches,
            notification_count=record.notification_count,
            unlock_count=record.unlock_count,
            calls_count=record.calls_count,
            call_duration_minutes=record.call_duration_minutes,
            battery_drain_percent=record.battery_drain_percent,
            late_night_minutes=record.late_night_minutes,
            sleep_hours=record.sleep_hours,
            note=record.note,
            source=record.source,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


@dataclass(kw_only=True)
class UsageSummary:
    """Aggregate view of the stored history.

    `has_enough_for_baseline` is the honest gate the brief requires: the app
    must not claim a reliable personal baseline until enough days exist.
    """

    total_records: int
    first_date: dt.date | None
    last_date: dt.date | None
    days_of_history: int
    records_by_source: dict[str, int] = field(default_factory=dict)
    min_days_for_baseline: int = 14
    has_enough_for_baseline: bool = False
    # True when ANY stored record is synthetic, so the UI can warn that
    # results are partly derived from demonstration data.
    contains_synthetic_data: bool = False
