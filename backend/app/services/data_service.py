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


# ===========================================================================
# Digital Wellbeing history import
# ===========================================================================

#: An older phone reading is replaced by the imported figure only when they
#: disagree by more than this. Readings by the fixed screen-time calculation
#: land within a few percent of Digital Wellbeing; readings taken before the
#: fix ran 50-75% high (the launcher was over-counted), and those days have
#: aged out of Android's history so they cannot be re-read.
WELLBEING_REPLACE_TOLERANCE = 0.15

#: Phone readings this recent are always kept: they can still be re-measured
#: and carry fields Digital Wellbeing does not show (late night, categories).
WELLBEING_RECENT_DAYS = 7


def import_wellbeing(db: Session, days: list[dict], read_at: str | None = None) -> dict:
    """Store daily totals copied from Digital Wellbeing's history screens.

    Each day: {"date": "YYYY-MM-DD", "screen_minutes": int,
               "unlocks": int | None, "notifications": int | None}

    Per date, an existing record decides what happens:
      none              -> inserted as source=wellbeing
      synthetic (demo)  -> replaced
      manual / csv      -> kept; the user's own entries are never overwritten
      wellbeing         -> refreshed with the newer import
      device, last 7 d  -> kept (re-measurable, richer)
      device, older     -> replaced only if it differs by more than 15%
                           (a reading from before the screen-time fix)
    """
    today = dt.date.today()
    report: dict = {
        "inserted": [], "replaced_demo": [], "refreshed": [],
        "replaced_old_phone_reading": [], "kept": [], "rejected": [],
    }
    note = "Copied from Digital Wellbeing" + (f" on {read_at}" if read_at else "")

    for raw in days:
        try:
            date = dt.date.fromisoformat(str(raw["date"]))
            minutes = int(raw["screen_minutes"])
            unlocks = None if raw.get("unlocks") is None else int(raw["unlocks"])
            notifications = None if raw.get("notifications") is None else int(raw["notifications"])
            if date >= today:
                raise ValueError("today is still in progress; only finished days are imported")
            if not 0 <= minutes <= 1440:
                raise ValueError(f"screen time {minutes} min is outside 0-1440")
            for name, v in (("unlocks", unlocks), ("notifications", notifications)):
                if v is not None and not 0 <= v <= 10_000:
                    raise ValueError(f"{name} {v} is outside 0-10000")
        except (KeyError, TypeError, ValueError) as exc:
            report["rejected"].append({"date": str(raw.get("date")), "reason": str(exc)})
            continue

        existing = db.scalar(select(UsageRecord).where(UsageRecord.record_date == date))
        values = {
            "total_screen_minutes": minutes,
            "unlock_count": unlocks,
            "notification_count": notifications,
        }

        def write_fresh(record: UsageRecord) -> None:
            # Every measured field is reset, so nothing from a replaced row
            # (demo filler, an inflated reading) survives beside the import.
            for field in (
                "social_minutes", "gaming_minutes", "productivity_minutes",
                "app_launches", "calls_count", "call_duration_minutes",
                "battery_drain_percent", "late_night_minutes", "sleep_hours",
            ):
                setattr(record, field, None)
            for field, value in values.items():
                setattr(record, field, value)
            record.source = DataSource.WELLBEING
            record.note = note

        if existing is None:
            record = UsageRecord(record_date=date, source=DataSource.WELLBEING, note=note,
                                 total_screen_minutes=minutes)
            write_fresh(record)
            db.add(record)
            report["inserted"].append(date.isoformat())
        elif existing.source is DataSource.SYNTHETIC:
            write_fresh(existing)
            report["replaced_demo"].append(date.isoformat())
        elif existing.source in (DataSource.MANUAL, DataSource.CSV):
            report["kept"].append({"date": date.isoformat(),
                                   "reason": f"you entered this day yourself ({existing.source.value})"})
        elif existing.source is DataSource.WELLBEING:
            write_fresh(existing)
            report["refreshed"].append(date.isoformat())
        else:  # DEVICE
            age = (today - date).days
            stored = existing.total_screen_minutes or 0
            gap = abs(stored - minutes) / max(minutes, 1)
            if age <= WELLBEING_RECENT_DAYS:
                report["kept"].append({"date": date.isoformat(),
                                       "reason": "recent phone reading; still re-measurable"})
            elif gap > WELLBEING_REPLACE_TOLERANCE:
                write_fresh(existing)
                report["replaced_old_phone_reading"].append(
                    {"date": date.isoformat(), "phone_minutes": stored, "wellbeing_minutes": minutes}
                )
            else:
                report["kept"].append({"date": date.isoformat(),
                                       "reason": f"phone reading agrees with Digital Wellbeing (within {gap:.0%})"})

    db.commit()
    report["counts"] = {k: len(v) for k, v in report.items() if isinstance(v, list)}
    return report
