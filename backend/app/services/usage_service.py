"""Business logic for usage records.

Sits between the API handlers and the database so that the handlers stay thin
and this logic can be unit-tested without going through HTTP. The ML layer
will also read history through this module rather than writing its own
queries, so there is exactly one definition of "the user's history".
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import DataSource, UsageRecord
from app.schemas.usage import (
    UPDATABLE_FIELDS,
    UsageRecordCreate,
    UsageSummary,
)


class DuplicateDateError(Exception):
    """Raised when a record already exists for the given date.

    The API turns this into HTTP 409 rather than silently overwriting, because
    quietly replacing a day's data would make the history untrustworthy.
    """

    def __init__(self, record_date: dt.date) -> None:
        self.record_date = record_date
        super().__init__(f"a record already exists for {record_date}")


class RecordNotFoundError(Exception):
    """Raised when the requested record does not exist."""

    def __init__(self, record_id: int) -> None:
        self.record_id = record_id
        super().__init__(f"no usage record with id {record_id}")


def create_record(db: Session, payload: UsageRecordCreate) -> UsageRecord:
    """Insert a new daily record.

    Rejects a duplicate date explicitly instead of relying on the database's
    unique constraint to raise, so the caller gets a clear, typed error.
    """
    existing = db.scalar(
        select(UsageRecord).where(UsageRecord.record_date == payload.record_date)
    )
    if existing is not None:
        raise DuplicateDateError(payload.record_date)

    record = UsageRecord(**payload.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_records(
    db: Session,
    *,
    limit: int = 365,
    offset: int = 0,
    start_date: dt.date | None = None,
    end_date: dt.date | None = None,
    sources: list[DataSource] | None = None,
) -> list[UsageRecord]:
    """Return records newest-first, optionally filtered.

    `sources` restricts the result to particular provenances. This is what
    lets the analysis run on the user's OWN measured data while demonstration
    rows remain in the database for screenshots - rather than forcing a choice
    between a working demo and honest analysis.
    """
    stmt = select(UsageRecord).order_by(UsageRecord.record_date.desc())

    if start_date is not None:
        stmt = stmt.where(UsageRecord.record_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(UsageRecord.record_date <= end_date)
    if sources:
        stmt = stmt.where(UsageRecord.source.in_(sources))

    stmt = stmt.limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_record(db: Session, record_id: int) -> UsageRecord:
    """Fetch one record by id, or raise RecordNotFoundError."""
    record = db.get(UsageRecord, record_id)
    if record is None:
        raise RecordNotFoundError(record_id)
    return record


def update_record(db: Session, record_id: int, payload: Mapping[str, object]) -> UsageRecord:
    """Apply a partial update, re-validating the MERGED result.

    A partial payload cannot be cross-validated on its own (sending only
    `social_minutes` says nothing about whether it fits inside the day's
    total), so the updated values are merged onto the stored row and the whole
    record is re-checked through the full create schema before committing.
    """
    record = get_record(db, record_id)

    # Only the keys actually SENT are applied, which is what pydantic's
    # exclude_unset did. A mapping carries that information directly; a
    # dataclass cannot, because an omitted field and an explicit null are
    # indistinguishable once it is constructed.
    unknown = set(payload) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(
            f"unknown field(s) in update: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(UPDATABLE_FIELDS))}"
        )

    changes = dict(payload)
    if not changes:
        return record

    merged = {
        "record_date": record.record_date,
        "total_screen_minutes": record.total_screen_minutes,
        "social_minutes": record.social_minutes,
        "gaming_minutes": record.gaming_minutes,
        "productivity_minutes": record.productivity_minutes,
        "app_launches": record.app_launches,
        "notification_count": record.notification_count,
        "unlock_count": record.unlock_count,
        "calls_count": record.calls_count,
        "call_duration_minutes": record.call_duration_minutes,
        "battery_drain_percent": record.battery_drain_percent,
        "late_night_minutes": record.late_night_minutes,
        "sleep_hours": record.sleep_hours,
        "note": record.note,
        "source": record.source,
    }
    merged.update(changes)

    # Raises ValueError if the merged row is inconsistent.
    validated = UsageRecordCreate(**merged)

    for field, value in validated.model_dump(exclude={"record_date", "source"}).items():
        setattr(record, field, value)

    db.commit()
    db.refresh(record)
    return record


def delete_record(db: Session, record_id: int) -> None:
    """Delete one record."""
    record = get_record(db, record_id)
    db.delete(record)
    db.commit()


def delete_by_source(db: Session, source: DataSource) -> int:
    """Delete every record from one source and return how many were removed.

    Primarily used to clear synthetic demonstration data, so a user can reset
    the app to only their real records.
    """
    records = list(db.scalars(select(UsageRecord).where(UsageRecord.source == source)))
    for record in records:
        db.delete(record)
    db.commit()
    return len(records)


def get_summary(db: Session) -> UsageSummary:
    """Aggregate the stored history.

    `days_of_history` counts STORED RECORDS, not the calendar span between the
    first and last date. A user with records on 1 Jan and 31 Dec has 2 days of
    history, not 365, and the baseline gate must use the former.
    """
    settings = get_settings()

    total = db.scalar(select(func.count()).select_from(UsageRecord)) or 0
    first_date = db.scalar(select(func.min(UsageRecord.record_date)))
    last_date = db.scalar(select(func.max(UsageRecord.record_date)))

    by_source_rows = db.execute(
        select(UsageRecord.source, func.count()).group_by(UsageRecord.source)
    ).all()
    records_by_source = {
        (src.value if isinstance(src, DataSource) else str(src)): count
        for src, count in by_source_rows
    }

    return UsageSummary(
        total_records=total,
        first_date=first_date,
        last_date=last_date,
        days_of_history=total,
        records_by_source=records_by_source,
        min_days_for_baseline=settings.min_days_for_baseline,
        has_enough_for_baseline=total >= settings.min_days_for_baseline,
        contains_synthetic_data=records_by_source.get(DataSource.SYNTHETIC.value, 0) > 0,
    )
