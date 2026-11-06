"""Usage record endpoints - create, read, update, delete and summarise.

Handlers stay deliberately thin: they translate HTTP to service calls and
service exceptions to status codes. All the rules live in the schemas
(field validation) and the service layer (business logic).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.models import DataSource
from app.database.session import get_db
from app.schemas.usage import (
    UsageRecordCreate,
    UsageRecordRead,
    UsageSummary,
)
from app.services import usage_service

router = APIRouter(prefix="/usage", tags=["usage"])


@router.post(
    "/records",
    response_model=UsageRecordRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add one day of usage",
)
def create_record(
    payload: UsageRecordCreate,
    db: Session = Depends(get_db),
) -> UsageRecordRead:
    try:
        record = usage_service.create_record(db, payload)
    except usage_service.DuplicateDateError as exc:
        # 409 rather than overwriting: silently replacing a day's data would
        # make the stored history untrustworthy.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UsageRecordRead.from_record(record)


@router.get(
    "/records",
    response_model=list[UsageRecordRead],
    summary="List usage records, newest first",
)
def list_records(
    limit: int = Query(default=365, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    start_date: dt.date | None = Query(default=None),
    end_date: dt.date | None = Query(default=None),
    sources: list[DataSource] | None = Query(
        default=None,
        description=(
            "Restrict to given provenances. Omit to return everything, "
            "including generated demonstration rows - correct for the Data "
            "screen, which is an inventory of what is stored, but wrong for a "
            "chart of the user's own behaviour."
        ),
    ),
    db: Session = Depends(get_db),
) -> list[UsageRecordRead]:
    records = usage_service.list_records(
        db,
        limit=limit,
        offset=offset,
        start_date=start_date,
        end_date=end_date,
        sources=sources,
    )
    return [UsageRecordRead.from_record(r) for r in records]


@router.get(
    "/summary",
    response_model=UsageSummary,
    summary="History totals and whether a baseline is possible yet",
)
def get_summary(db: Session = Depends(get_db)) -> UsageSummary:
    return usage_service.get_summary(db)


@router.get(
    "/records/{record_id}",
    response_model=UsageRecordRead,
    summary="Fetch one record",
)
def get_record(record_id: int, db: Session = Depends(get_db)) -> UsageRecordRead:
    try:
        record = usage_service.get_record(db, record_id)
    except usage_service.RecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return UsageRecordRead.from_record(record)


@router.patch(
    "/records/{record_id}",
    response_model=UsageRecordRead,
    summary="Partially update a record",
)
def update_record(
    record_id: int,
    # A raw mapping rather than a schema object, because a partial update
    # needs to know which fields were SENT - and a dataclass cannot tell an
    # omitted field from one explicitly set to null. The keys are checked
    # against an allow-list and the merged row is fully revalidated; see
    # usage_service.update_record.
    payload: dict,
    db: Session = Depends(get_db),
) -> UsageRecordRead:
    try:
        record = usage_service.update_record(db, record_id, payload)
    except usage_service.RecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        # Raised when the MERGED record is internally inconsistent, e.g. the
        # new category minutes no longer fit inside the stored total.
        # Starlette deprecated HTTP_422_UNPROCESSABLE_ENTITY in favour of
        # HTTP_422_UNPROCESSABLE_CONTENT. Same code (422), new name.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return UsageRecordRead.from_record(record)


@router.delete(
    "/records/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a record",
)
def delete_record(record_id: int, db: Session = Depends(get_db)) -> None:
    try:
        usage_service.delete_record(db, record_id)
    except usage_service.RecordNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete(
    "/records",
    summary="Delete all records from one source (e.g. clear demo data)",
)
def delete_by_source(
    source: DataSource = Query(description="Which provenance to clear."),
    db: Session = Depends(get_db),
) -> dict:
    deleted = usage_service.delete_by_source(db, source)
    return {"deleted": deleted, "source": source.value}
