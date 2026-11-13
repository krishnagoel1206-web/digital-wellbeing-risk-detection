"""Data endpoints: CSV import, the feature dictionary, and demo-data seeding.

These back the Usage Data screen. The feature-dictionary endpoint is what
lets the app explain any number it shows in plain language without
duplicating the wording in the mobile code.
"""

from __future__ import annotations

from fastapi import Body, APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database.models import DataSource
from app.database.session import get_db
from app.ml.preprocessing import feature_dictionary as fd
from app.ml.preprocessing.synthetic import PROFILES, generate_history
from app.schemas.sync import SyncRequest, SyncResponse
from app.schemas.usage import UsageRecordCreate
from app.services import csv_import, data_service, sync_service, usage_service

router = APIRouter(prefix="/data", tags=["data"])

#: Refuse very large uploads outright rather than reading them into memory.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


@router.get("/feature-dictionary", summary="Documented meaning of every feature")
def get_feature_dictionary() -> dict:
    """Return the feature dictionary, grouped by origin."""
    return {
        "personal_raw": [fd.to_dict(s) for s in fd.PERSONAL_RAW],
        "personal_derived": [fd.to_dict(s) for s in fd.PERSONAL_DERIVED],
        "population": [fd.to_dict(s) for s in fd.POPULATION_FEATURES],
        "targets": [fd.to_dict(s) for s in fd.TARGETS],
        "classifier_features": fd.classifier_features(),
    }


@router.post(
    "/import-csv",
    summary="Import daily usage records from a CSV file",
)
async def import_csv(
    file: UploadFile = File(description="CSV with a date column and a screen-time column."),
    db: Session = Depends(get_db),
) -> dict:
    """Import a CSV and return a full validation report.

    Rows are validated through exactly the same schema as manual entry, so a
    CSV cannot introduce data the API would otherwise reject.
    """
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only .csv files are accepted",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="the file is empty"
        )

    result = csv_import.import_usage_csv(db, content=content, filename=filename)

    return {
        "filename": result.filename,
        "rows_in_file": result.rows_in_file,
        "rows_accepted": result.rows_accepted,
        "rows_rejected": result.rows_rejected,
        "rows_duplicate": result.rows_duplicate,
        "recognised_columns": result.recognised_columns,
        "ignored_columns": result.ignored_columns,
        "notes": result.notes,
        # Capped so a badly-formed file cannot return a megabyte of errors.
        "problems": [
            {"row": p.row_number, "reason": p.reason} for p in result.problems[:100]
        ],
        "problems_truncated": len(result.problems) > 100,
    }


@router.get("/demo-profiles", summary="Available synthetic behaviour profiles")
def list_demo_profiles() -> dict:
    """Describe the synthetic personas available for demonstration data."""
    return {
        "profiles": [
            {
                "key": key,
                "screen_minutes_mean": profile.screen_minutes_mean,
                "social_share": profile.social_share,
                "late_night_share": profile.late_night_share,
                "notifications_mean": profile.notifications_mean,
            }
            for key, profile in PROFILES.items()
        ]
    }


@router.post(
    "/seed-demo",
    summary="Generate clearly-labelled synthetic history for testing",
)
def seed_demo(
    days: int = Query(default=60, ge=7, le=365),
    profile: str = Query(default="balanced"),
    seed: int = Query(default=42, description="RNG seed; the same seed reproduces the same data."),
    replace: bool = Query(
        default=True, description="Clear existing synthetic rows first. Manual rows are kept."
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Insert synthetic daily records, tagged so they are never mistaken for real data.

    The logic lives in data_service so the phone runs the same code; this
    turns a rejected request into a status code and nothing else.
    """
    try:
        return data_service.seed_demo(
            db, days=days, profile=profile, seed=seed, replace=replace
        )
    except data_service.BadRequest as exc:
        # 400, matching what this endpoint returned before the extraction. An
        # unknown profile name is a bad request, not an unprocessable body -
        # and changing a status code while moving code is exactly the kind of
        # silent behaviour change a refactor must not make.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get("/import-history", summary="Past CSV imports and their reports")
def import_history(db: Session = Depends(get_db)) -> dict:
    """List previous imports, newest first, so the audit trail is visible."""
    from sqlalchemy import select

    from app.database.models import ImportBatch

    batches = list(
        db.scalars(select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(50))
    )
    return {
        "imports": [
            {
                "id": b.id,
                "filename": b.filename,
                "rows_in_file": b.rows_in_file,
                "rows_accepted": b.rows_accepted,
                "rows_rejected": b.rows_rejected,
                "rows_duplicate": b.rows_duplicate,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in batches
        ]
    }


# ===========================================================================
# DEVICE SYNC
# ===========================================================================


@router.post("/sync", summary="Upsert days measured on the device")
def sync_device_days(
    payload: SyncRequest,
    db: Session = Depends(get_db),
) -> SyncResponse:
    """Accept a batch of days read from Android's usage statistics.

    Unlike manual entry, this UPSERTS - today's usage grows through the day,
    so a later sync must be able to replace an earlier one for the same date.

    Four rules protect the stored history, all implemented in
    app/services/sync_service.py:

      1. Only device-sourced rows may be overwritten. A day the user typed or
         imported is reported as skipped, never clobbered.
      2. A null from the device never overwrites a stored value.
      3. Days the device reported no activity for are skipped rather than
         stored as zero, because Android cannot distinguish "unused" from
         "older than my event retention".
      4. Device rows are validated as strictly as typed rows.
    """
    return sync_service.sync_days(db, payload)


@router.post("/import-wellbeing", summary="Import daily totals copied from Digital Wellbeing")
def import_wellbeing(payload: dict = Body(...), db: Session = Depends(get_db)) -> dict:
    """See data_service.import_wellbeing for the per-date rules."""
    return data_service.import_wellbeing(db, payload.get("days", []), read_at=payload.get("read_at"))


@router.get("/sync-status", summary="What the device has contributed so far")
def sync_status(db: Session = Depends(get_db)) -> dict:
    """What the device has contributed so far. See data_service.sync_status."""
    return data_service.sync_status(db)
