"""CSV import for personal daily usage records.

DESIGN PRINCIPLES
-----------------
1. NEVER ASSUME A COLUMN EXISTS. Headers are introspected and matched against
   a documented alias table. Anything unrecognised is reported back to the
   user as "ignored", not silently dropped.

2. UNITS ARE DETECTED, NOT GUESSED. A column named `*_hours` is multiplied by
   60; a column named `*_minutes` is taken as-is. Where a name is ambiguous
   the importer refuses to guess and says so, because silently treating hours
   as minutes would understate usage 60-fold.

3. EVERY REJECTED ROW HAS A REASON. The import returns a per-row report so
   the Usage Data screen can show a real validation summary rather than a
   bare success count.

4. ROWS ARE VALIDATED THROUGH THE SAME SCHEMA AS MANUAL ENTRY. There is no
   lenient import path - a CSV cannot insert data that the API would reject,
   so the database holds one consistent standard of validity.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database.models import DataSource, ImportBatch
from app.schemas.usage import UsageRecordCreate
from app.services import usage_service

#: Canonical field -> accepted header names (lower-cased, stripped).
#: Deliberately explicit rather than fuzzy-matched: a wrong automatic match
#: is far more damaging than asking the user to rename a column.
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "record_date": ("date", "record_date", "day", "usage_date"),
    "total_screen_minutes": (
        "total_screen_minutes",
        "screen_time_minutes",
        "screen_minutes",
    ),
    "total_screen_hours": (
        "total_screen_hours",
        "screen_time_hours",
        "screen_hours",
        "daily_screen_time_hours",
    ),
    "social_minutes": ("social_minutes", "social_media_minutes"),
    "social_hours": ("social_hours", "social_media_hours"),
    "gaming_minutes": ("gaming_minutes", "games_minutes"),
    "gaming_hours": ("gaming_hours", "games_hours"),
    "productivity_minutes": ("productivity_minutes", "work_study_minutes"),
    "productivity_hours": ("productivity_hours", "work_study_hours"),
    "app_launches": ("app_launches", "app_opens", "app_opens_per_day", "launches"),
    "notification_count": (
        "notification_count",
        "notifications",
        "notifications_per_day",
    ),
    "unlock_count": ("unlock_count", "unlocks", "pickups"),
    "calls_count": ("calls_count", "calls"),
    "call_duration_minutes": ("call_duration_minutes", "call_minutes"),
    "battery_drain_percent": ("battery_drain_percent", "battery_drain"),
    "late_night_minutes": ("late_night_minutes",),
    "late_night_hours": ("late_night_hours",),
    "sleep_hours": ("sleep_hours", "sleep"),
    "note": ("note", "notes", "comment"),
}

#: Fields expressed in hours that must be converted to stored minutes.
HOURS_TO_MINUTES: dict[str, str] = {
    "total_screen_hours": "total_screen_minutes",
    "social_hours": "social_minutes",
    "gaming_hours": "gaming_minutes",
    "productivity_hours": "productivity_minutes",
    "late_night_hours": "late_night_minutes",
}

#: Accepted date formats, tried in order.
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d")


@dataclass
class RowProblem:
    """One row that could not be imported."""

    row_number: int
    reason: str
    raw: dict = field(default_factory=dict)


@dataclass
class ImportResult:
    """Full account of an import, returned to the API and stored for audit."""

    filename: str
    rows_in_file: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_duplicate: int = 0
    #: Header -> canonical field that it matched.
    recognised_columns: dict[str, str] = field(default_factory=dict)
    #: Headers that matched nothing.
    ignored_columns: list[str] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "recognised_columns": self.recognised_columns,
                "ignored_columns": self.ignored_columns,
                "notes": self.notes,
                "problems": [
                    {"row": p.row_number, "reason": p.reason} for p in self.problems[:200]
                ],
            },
            indent=2,
        )


def _map_headers(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Match file headers to canonical fields.

    Returns (header -> canonical, unmatched headers).
    """
    lookup: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            lookup[alias] = canonical

    recognised: dict[str, str] = {}
    ignored: list[str] = []

    for header in headers:
        key = header.strip().lower().replace(" ", "_")
        canonical = lookup.get(key)
        if canonical:
            recognised[header] = canonical
        else:
            ignored.append(header)

    return recognised, ignored


def _parse_date(value: str) -> dt.date:
    """Parse a date using the accepted formats, or raise with a clear message."""
    text = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        f"unrecognised date {value!r}; accepted formats: {', '.join(DATE_FORMATS)}"
    )


def _parse_number(value: str) -> float | None:
    """Parse a numeric cell. An empty cell means missing, not zero."""
    text = value.strip()
    if text == "":
        return None
    return float(text)


def import_usage_csv(
    db: Session,
    *,
    content: bytes,
    filename: str,
) -> ImportResult:
    """Import personal daily usage rows from CSV bytes.

    Rows are inserted with source=CSV. Existing dates are counted as
    duplicates and skipped rather than overwritten, matching the manual-entry
    rule that stored history is never silently replaced.
    """
    result = ImportResult(filename=filename)

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("latin-1")
            result.notes.append("file was not UTF-8; decoded as latin-1")
        except UnicodeDecodeError as exc:
            result.notes.append(f"could not decode file: {exc}")
            return result

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        result.notes.append("file appears to be empty - no header row found")
        return result

    recognised, ignored = _map_headers(list(reader.fieldnames))
    result.recognised_columns = recognised
    result.ignored_columns = ignored

    canonical_present = set(recognised.values())

    if "record_date" not in canonical_present:
        result.notes.append(
            "no date column found. Expected one of: "
            f"{', '.join(COLUMN_ALIASES['record_date'])}"
        )
        return result

    has_minutes = "total_screen_minutes" in canonical_present
    has_hours = "total_screen_hours" in canonical_present
    if not (has_minutes or has_hours):
        result.notes.append(
            "no screen-time column found. Expected one of: "
            f"{', '.join(COLUMN_ALIASES['total_screen_minutes'] + COLUMN_ALIASES['total_screen_hours'])}"
        )
        return result
    if has_minutes and has_hours:
        result.notes.append(
            "both a minutes and an hours screen-time column are present; "
            "the minutes column is used and the hours column ignored"
        )

    if ignored:
        result.notes.append(f"ignored {len(ignored)} unrecognised column(s): {ignored}")

    # Row numbering starts at 2 so it matches what the user sees in a
    # spreadsheet (row 1 being the header).
    for row_number, raw_row in enumerate(reader, start=2):
        result.rows_in_file += 1

        payload: dict = {"source": DataSource.CSV}
        failed = False

        for header, canonical in recognised.items():
            cell = raw_row.get(header)
            if cell is None:
                continue

            try:
                if canonical == "record_date":
                    payload["record_date"] = _parse_date(cell)
                elif canonical == "note":
                    text_value = cell.strip()
                    if text_value:
                        payload["note"] = text_value[:280]
                else:
                    number = _parse_number(cell)
                    if number is None:
                        continue
                    target = HOURS_TO_MINUTES.get(canonical)
                    if target:
                        # Skip an hours column when the minutes equivalent
                        # was also supplied.
                        if target in canonical_present:
                            continue
                        payload[target] = int(round(number * 60))
                    else:
                        payload[canonical] = (
                            number
                            if canonical in {"battery_drain_percent", "sleep_hours"}
                            else int(round(number))
                        )
            except ValueError as exc:
                result.problems.append(RowProblem(row_number, str(exc)))
                failed = True
                break

        if failed:
            result.rows_rejected += 1
            continue

        try:
            validated = UsageRecordCreate(**payload)
        except (TypeError, ValueError) as exc:
            # str(exc), not exc.errors(). The schemas raise plain ValueError
            # now that they are dataclasses rather than pydantic models, and
            # a single clear sentence is what the import report was building
            # out of the structured list anyway.
            #
            # TypeError is caught too: a dataclass rejects an unexpected
            # keyword with TypeError where pydantic reported it as a
            # validation error, and a CSV with a stray recognised-looking
            # column must be a rejected ROW, not a 500.
            result.problems.append(RowProblem(row_number, str(exc)))
            result.rows_rejected += 1
            continue

        try:
            usage_service.create_record(db, validated)
            result.rows_accepted += 1
        except usage_service.DuplicateDateError:
            result.rows_duplicate += 1
            result.problems.append(
                RowProblem(row_number, f"a record already exists for {validated.record_date}")
            )

    # Store the audit record so the history of imports is inspectable.
    batch = ImportBatch(
        filename=filename,
        rows_in_file=result.rows_in_file,
        rows_accepted=result.rows_accepted,
        rows_rejected=result.rows_rejected,
        rows_duplicate=result.rows_duplicate,
        report_json=result.to_json(),
    )
    db.add(batch)
    db.commit()

    return result
