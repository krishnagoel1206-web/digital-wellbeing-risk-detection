"""Data-management logic, with no web framework.

Extracted from app/api/data.py for the same reason as analysis_service: the
phone runs this code directly through the Kotlin bridge, and a second copy for
the device would drift from this one the first time either was corrected.

What stayed behind in the router is HTTP: file upload handling and turning a
rejected request into a status code.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import DataSource, UsageRecord
from app.ml.preprocessing import synthetic
from app.ml.preprocessing.synthetic import PROFILES, generate_history
from app.schemas.usage import UsageRecordCreate
from app.services import usage_service


class BadRequest(Exception):
    """The request cannot be satisfied as asked - e.g. an unknown profile."""


def seed_demo(
    db: Session,
    days: int = 60,
    profile: str = "balanced",
    seed: int = 42,
    replace: bool = True,
) -> dict:
    """Insert synthetic daily records, tagged so they are never mistaken for real data.

    Returns the injected-anomaly ground truth, which the anomaly-detection
    module is scored against later.
    """
    if profile not in PROFILES:
        raise BadRequest(
            f"unknown profile {profile!r}; available: {sorted(PROFILES)}"
        )

    generated = generate_history(days=days, profile_name=profile, seed=seed)

    cleared = 0
    if replace:
        cleared = usage_service.delete_by_source(db, DataSource.SYNTHETIC)

    inserted = 0
    skipped = 0
    failures: list[str] = []

    for row in generated.records:
        try:
            payload = UsageRecordCreate(**row)
        except Exception as exc:
            # A generated row failing our own validation is a generator bug.
            failures.append(f"{row['record_date']}: {exc}")
            continue
        try:
            usage_service.create_record(db, payload)
            inserted += 1
        except usage_service.DuplicateDateError:
            skipped += 1

    return {
        "profile": profile,
        "seed": seed,
        "days_requested": days,
        "synthetic_rows_cleared": cleared,
        "inserted": inserted,
        "skipped_existing_dates": skipped,
        "generator_failures": failures,
        "injected_anomalies": generated.anomalies,
        "notes": generated.notes,
        "warning": (
            "This is DEMONSTRATION data. Any model result derived from it must "
            "not be presented as validated real-world performance."
        ),
    }




def sync_status(
    db: Session,
) -> dict:
    """Summarise device-sourced data, so the app can show sync health."""
    from sqlalchemy import func, select

    from app.database.models import UsageRecord

    total = db.scalar(
        select(func.count())
        .select_from(UsageRecord)
        .where(UsageRecord.source == DataSource.DEVICE)
    ) or 0
    first = db.scalar(
        select(func.min(UsageRecord.record_date)).where(
            UsageRecord.source == DataSource.DEVICE
        )
    )
    last = db.scalar(
        select(func.max(UsageRecord.record_date)).where(
            UsageRecord.source == DataSource.DEVICE
        )
    )

    # Count how many device days actually carry each optional field, so the
    # app can say "notifications: 3 of 30 days" rather than implying full
    # coverage.
    coverage: dict[str, int] = {}
    #: The earliest device day carrying each field.
    #:
    #: Reported because a count alone does not answer the question the user
    #: actually has. "notifications: 1 of 8 days" looks like a fault; "counts
    #: begin 2026-11-06" explains it, and lets the app say when a model that
    #: needs seven such days becomes available instead of only that it cannot
    #: run yet.
    first_seen: dict[str, str | None] = {}

    for field in ("notification_count", "calls_count", "unlock_count", "sleep_hours"):
        column = getattr(UsageRecord, field)
        condition = (UsageRecord.source == DataSource.DEVICE, column.isnot(None))

        coverage[field] = db.scalar(
            select(func.count()).select_from(UsageRecord).where(*condition)
        ) or 0

        earliest = db.scalar(select(func.min(UsageRecord.record_date)).where(*condition))
        first_seen[field] = earliest.isoformat() if earliest else None

    return {
        "device_days": total,
        "first_date": first.isoformat() if first else None,
        "last_date": last.isoformat() if last else None,
        "field_coverage": coverage,
        "field_first_seen": first_seen,
        "coverage_note": (
            "field_coverage counts device days that actually carry a value. A "
            "low count is expected for notifications, which Android cannot "
            "backfill, and for sleep, which is only estimated when the "
            "activity gaps allow it. field_first_seen gives the earliest day "
            "each measurement is present on, which is usually the day its "
            "permission was granted."
        ),
    }
