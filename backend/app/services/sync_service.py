"""Device sync: upserting measured days from the phone.

THE UPSERT RULES, AND WHY EACH EXISTS
-------------------------------------
1. A sync may overwrite a row whose source is DEVICE or SYNTHETIC, but never
   one that is MANUAL or CSV.

   Manual entries and CSV imports are the user's own deliberate records. If
   the phone could overwrite them, a day someone corrected by hand would
   silently revert on the next sync.

   SYNTHETIC rows are the opposite case: generated demonstration filler, with
   no claim to being true. A real measurement must always win over them.

   The first version of this rule protected synthetic rows too, and the
   effect was absurd in practice - the device read 7 genuine days, but 6 of
   them collided with generated demo days and were refused, so only the one
   date the generator had not covered was stored. Protecting invented data
   from real data is exactly backwards.

2. A null overwrites an existing value ONLY for fields the device actually
   evaluated.

   The distinction matters, and a blanket rule gets it wrong either way:

     notification_count and battery_drain_percent are UNKNOWABLE for past
     days - Android keeps no history of either. A null there means "I cannot
     see this", so overwriting would wipe counts collected while the
     listener was running.

     Every other field IS evaluated on each sync. A null means "I looked and
     there is nothing to report". Refusing to write it leaves a stale value
     the system no longer stands behind.

   That second case was a live bug: a sleep estimate of 13.6h was stored
   before the plausibility band was tightened to 4-11h. Afterwards the device
   correctly sent null for that day - and the discredited 13.6h survived,
   because the old blanket rule would not let a null through.

   2b. An UNKNOWABLE field's null still overwrites when it is a VERDICT.

   The split above asks which field the null is for. That is the wrong
   question on its own, because notification_count produces both kinds:

     "I cannot see this day" - no history exists to read. Must not overwrite.

     "I can see this day, and its count is unusable" - the listener covered
     only part of it, so the tally is a short measurement masquerading as a
     quiet day. That IS a statement, and refusing it would pin a discredited
     count in place permanently - the same trap as the 13.6h sleep estimate.

   So the real question is whether the device reached a conclusion. See
   _rejected_fields(); the coverage rule itself lives in app/schemas/sync.py,
   enforced where data persists rather than only on the phone.

3. Days where the device reported no activity at all are skipped, not stored.
   Android retains detailed events for roughly a week. An older day with no
   events is UNKNOWN, not zero. Storing it as a zero-usage day would feed the
   personal baseline a fabricated quiet day and make genuine quiet days look
   normal by comparison.

4. Device rows are validated exactly as strictly as typed rows.
   Device data is not more trustworthy than human data. If session
   reconstruction ever produced category minutes exceeding the daily total,
   that is a bug in the native module and must surface here.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import DataSource, UsageRecord
from app.schemas.sync import (
    DeviceDayPayload,
    SyncDaySkipped,
    SyncRequest,
    SyncResponse,
)

#: Fields the device may supply. Anything absent here is never touched by sync.
DEVICE_FIELDS = (
    "total_screen_minutes",
    "social_minutes",
    "gaming_minutes",
    "productivity_minutes",
    "app_launches",
    "unlock_count",
    "notification_count",
    "calls_count",
    "call_duration_minutes",
    "battery_drain_percent",
    "late_night_minutes",
    "sleep_hours",
)

#: Fields whose null means "the device cannot know this", not "there is
#: nothing to report".
#:
#: Android retains no history for either: notification counts exist only from
#: the moment notification access is granted, and daily battery consumption is
#: never retained. A null in these two says nothing about the stored value, so
#: it must not overwrite one.
#:
#: Every other field is re-evaluated on every sync, so a null there is a real
#: statement and DOES overwrite - see the module docstring for the stale
#: sleep-estimate bug this fixes.
UNKNOWABLE_WHEN_NULL = frozenset({"notification_count", "battery_drain_percent"})

#: Why a field may be missing, so the app can name the fix rather than just
#: showing a blank. Keyed by field name.
MISSING_FIELD_REASONS = {
    "notification_count": (
        "Notification access is not granted, or these days predate it. Android "
        "keeps no notification history, so earlier days cannot be recovered."
    ),
    "calls_count": "Call log permission is not granted.",
    "call_duration_minutes": "Call log permission is not granted.",
    "battery_drain_percent": (
        "Android does not retain a historical record of daily battery "
        "consumption, so this cannot be read for past days."
    ),
    "unlock_count": "The device emitted no usable unlock or screen-on signal.",
    "sleep_hours": "Sleep could not be estimated from the available activity gaps.",
}


def _rejected_fields(day: DeviceDayPayload) -> frozenset[str]:
    """Fields whose null is a VERDICT, not an absence of information.

    This is the exception that keeps Rule 2 honest. Rule 2 protects
    `notification_count` from being wiped by a null, because Android cannot
    backfill notification counts and a null normally means "I cannot see this
    day" rather than "there were none".

    But a null also arrives when the device CAN see the day and has judged its
    count unusable - the listener covered only part of it. That null is a
    statement, and refusing it would pin a discredited figure in place
    permanently, which is exactly the bug that kept a 13.6 h sleep estimate
    alive after the plausibility band was tightened.

    So the question is not which field the null is for, but whether the device
    reached a conclusion about it.
    """
    rejected: set[str] = set()
    if getattr(day, "_notifications_rejected", None):
        rejected.add("notification_count")
    return frozenset(rejected)


def _note_for(day: DeviceDayPayload) -> str:
    """Provenance note stored on the row."""
    parts = ["synced from device"]
    if day.sleep_hours is not None and day.sleep_is_estimated:
        parts.append("sleep is an estimate from inactivity")
    return "; ".join(parts)[:280]


def sync_days(db: Session, payload: SyncRequest) -> SyncResponse:
    """Upsert a batch of device-measured days."""
    created = 0
    updated = 0
    skipped: list[SyncDaySkipped] = []

    # Track which device fields arrived with a value at least once, so we can
    # report the ones that never did.
    seen_values: dict[str, bool] = {field: False for field in DEVICE_FIELDS}

    dates: list[dt.date] = []

    # Dates whose stored row now matches what the device sent, so the phone can
    # stop queueing them. Created, updated AND already-current all qualify;
    # only a genuine refusal does not.
    accepted: list[dt.date] = []

    # Estimates the schema threw out as implausible. Reported rather than
    # dropped silently - a blank with no explanation looks like a bug.
    discarded_estimates: list[str] = []

    # Notification counts dropped for incomplete coverage of the day. Reported
    # rather than dropped silently: a count that vanishes with no explanation
    # looks like a sync failure.
    discarded_counts: list[str] = []

    for day in payload.days:
        dates.append(day.record_date)

        rejected = getattr(day, "_sleep_rejected", None)
        if rejected:
            discarded_estimates.append(f"{day.record_date}: {rejected}")

        rejected_notifications = getattr(day, "_notifications_rejected", None)
        if rejected_notifications:
            discarded_counts.append(f"{day.record_date}: {rejected_notifications}")

        for field in DEVICE_FIELDS:
            if getattr(day, field, None) is not None:
                seen_values[field] = True

        # Rule 3: no activity reported is not the same as zero activity.
        if not day.had_any_usage:
            skipped.append(
                SyncDaySkipped(
                    record_date=day.record_date,
                    reason=(
                        "the device reported no activity for this day, which may "
                        "mean it was unused or that the day is older than "
                        "Android's event retention. Stored as unknown rather "
                        "than as a zero-usage day."
                    ),
                )
            )
            continue

        existing = db.scalar(
            select(UsageRecord).where(UsageRecord.record_date == day.record_date)
        )

        if existing is None:
            record = UsageRecord(
                record_date=day.record_date,
                source=DataSource.DEVICE,
                note=_note_for(day),
            )
            for field in DEVICE_FIELDS:
                setattr(record, field, getattr(day, field, None))
            db.add(record)
            created += 1
            accepted.append(day.record_date)
            continue

        # Rule 1: never overwrite a HUMAN-authored row, but do replace
        # generated demonstration data with a real measurement.
        if existing.source in (DataSource.MANUAL, DataSource.CSV):
            skipped.append(
                SyncDaySkipped(
                    record_date=day.record_date,
                    reason=(
                        f"an existing record for this date came from "
                        f"'{existing.source.value}' - entries you made or "
                        "imported yourself are never overwritten by a sync."
                    ),
                )
            )
            continue

        replaced_synthetic = existing.source is DataSource.SYNTHETIC
        if replaced_synthetic:
            # A real measurement supersedes demo filler. Every device field is
            # written, including nulls, so no generated value can survive
            # inside an otherwise-real row and masquerade as measured.
            for field in DEVICE_FIELDS:
                setattr(existing, field, getattr(day, field, None))
            existing.source = DataSource.DEVICE
            existing.note = _note_for(day) + "; replaced generated demo data"
            updated += 1
            accepted.append(day.record_date)
            continue

        # Rule 2: a null overwrites only where the device actually looked.
        changed = False
        for field in DEVICE_FIELDS:
            incoming = getattr(day, field, None)

            if (
                incoming is None
                and field in UNKNOWABLE_WHEN_NULL
                and field not in _rejected_fields(day)
            ):
                # The device cannot see this for past days, so its null says
                # nothing about the stored value. Leave it alone.
                continue

            if getattr(existing, field) != incoming:
                setattr(existing, field, incoming)
                changed = True

        existing.note = _note_for(day)
        accepted.append(day.record_date)
        if changed:
            updated += 1
        else:
            # Counted as updated anyway would overstate what happened; the day
            # was accepted but nothing moved.
            skipped.append(
                SyncDaySkipped(
                    record_date=day.record_date,
                    reason="already up to date; no values changed.",
                )
            )

    db.commit()

    missing_fields = {
        field: MISSING_FIELD_REASONS.get(
            field, "The device did not supply this field."
        )
        for field, seen in seen_values.items()
        if not seen
    }

    notes = [
        "Device rows are validated with the same rules as manual entry - "
        "device data is not trusted more than typed data.",
    ]
    if missing_fields:
        notes.append(
            f"{len(missing_fields)} field(s) arrived empty for every day; see "
            "missing_fields for why."
        )
    if discarded_estimates:
        notes.append(
            f"{len(discarded_estimates)} sleep estimate(s) discarded as "
            "implausible: " + "; ".join(discarded_estimates[:5])
        )
    if discarded_counts:
        notes.append(
            f"{len(discarded_counts)} notification count(s) discarded for "
            "incomplete coverage of the day: "
            + "; ".join(discarded_counts[:5])
        )
    if payload.permissions:
        denied = [name for name, granted in payload.permissions.items() if not granted]
        if denied:
            notes.append(f"permissions not granted at collection time: {denied}")

    return SyncResponse(
        days_received=len(payload.days),
        created=created,
        updated=updated,
        skipped=len(skipped),
        skipped_detail=skipped,
        accepted_dates=accepted,
        missing_fields=missing_fields,
        first_date=min(dates) if dates else None,
        last_date=max(dates) if dates else None,
        notes=notes,
    )
