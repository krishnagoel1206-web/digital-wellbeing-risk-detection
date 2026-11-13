"""SQLAlchemy ORM models - the persistence layer.

DESIGN NOTES
------------
1. WHAT LIVES HERE vs WHAT LIVES IN CSV
   This database stores the USER'S OWN daily usage records. The population
   datasets (the 7,500-row smartphone dataset and the 705-student dataset) are
   NOT stored here - they are cross-sectional training data, read straight
   from CSV by the ML layer. Keeping them out of the database avoids confusing
   "data about this user" with "data used to fit models".

2. UNITS
   All durations are stored as INTEGER MINUTES, never float hours. The source
   datasets express usage in fractional hours, which is converted on import.
   Minutes avoid accumulating float error when ratios and weekly sums are
   computed, and they round-trip through JSON exactly.

3. NULLABILITY IS DELIBERATE
   Only `record_date` and `total_screen_minutes` are required. Every other
   behavioural field is nullable, because the brief is explicit that a field
   must never be assumed to exist: manual entry cannot supply battery drain,
   and neither dataset supplies call duration. Downstream code must therefore
   handle missing values rather than assume a complete row.

4. `source` SEPARATES REAL FROM DEMONSTRATION DATA
   Every row records whether it came from manual entry, a CSV import, or the
   synthetic generator. The brief requires demonstration data to be clearly
   distinguishable from real user data, and this column is what makes that
   possible at query time rather than by convention.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class DataSource(str, enum.Enum):
    """Where a usage record came from.

    MANUAL    - the user typed it in.
    CSV       - imported from a dataset file.
    DEVICE    - measured by Android and read through the native usage-stats
                module. For the fields it can supply this is the most
                trustworthy source, because it is measured rather than
                recalled. It cannot supply every field: notification counts
                and battery drain have no historical record on the device.
                See app/services/sync_service.py for the upsert rules.
    SYNTHETIC - produced by the sample-data generator for UI/pipeline testing.
                Results derived from SYNTHETIC rows must never be presented as
                validated real-world performance.
    """

    MANUAL = "manual"
    CSV = "csv"
    DEVICE = "device"
    SYNTHETIC = "synthetic"
    #: Copied from Digital Wellbeing's own history screens for days the app
    #: could not measure (Android keeps detailed events for about a week,
    #: Digital Wellbeing keeps its daily totals for about four). Real data
    #: about this phone, but not measured by this app, so kept distinct.
    WELLBEING = "wellbeing"


class UsageRecord(Base):
    """One calendar day of this user's phone usage."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # One row per calendar day. Unique, so re-importing a file cannot silently
    # create duplicate days - the import layer detects the clash and reports it
    # instead, which is what the brief's "duplicate removal" requires.
    record_date: Mapped[dt.date] = mapped_column(Date, nullable=False, unique=True, index=True)

    # --- required ---
    total_screen_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- per-category usage (nullable: not every source provides these) ---
    social_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gaming_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    productivity_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- interaction counts ---
    app_launches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notification_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unlock_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- calls ---
    calls_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    call_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- device / context ---
    battery_drain_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Minutes of use inside the late-night window (configurable in settings,
    # default 23:00-06:00). Stored as an absolute count; the RATIO is derived
    # at feature-engineering time so the stored value stays a raw measurement.
    late_night_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    # --- provenance ---
    source: Mapped[DataSource] = mapped_column(
        SAEnum(DataSource, native_enum=False, length=16),
        nullable=False,
        default=DataSource.MANUAL,
        index=True,
    )
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Database-level sanity limits. These duplicate the Pydantic validation on
    # purpose: Pydantic guards the API, these guard the database against any
    # other writer (a script, a manual SQL insert, a future importer).
    __table_args__ = (
        CheckConstraint(
            "total_screen_minutes >= 0 AND total_screen_minutes <= 1440",
            name="ck_total_screen_minutes_range",
        ),
        CheckConstraint(
            "late_night_minutes IS NULL OR (late_night_minutes >= 0 AND late_night_minutes <= 1440)",
            name="ck_late_night_minutes_range",
        ),
        CheckConstraint(
            "sleep_hours IS NULL OR (sleep_hours >= 0 AND sleep_hours <= 24)",
            name="ck_sleep_hours_range",
        ),
        CheckConstraint(
            "battery_drain_percent IS NULL OR "
            "(battery_drain_percent >= 0 AND battery_drain_percent <= 100)",
            name="ck_battery_drain_range",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<UsageRecord {self.record_date} "
            f"screen={self.total_screen_minutes}m source={self.source.value}>"
        )


class ImportBatch(Base):
    """An audit record of one CSV import.

    Exists so the Usage Data screen can show a real validation report - how
    many rows were accepted, rejected or were duplicates, and why - rather
    than silently swallowing bad input.
    """

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    rows_in_file: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # JSON-encoded detail: per-row errors, detected columns, unmapped columns.
    # Stored as Text so SQLite needs no JSON extension.
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ImportBatch {self.filename} "
            f"accepted={self.rows_accepted}/{self.rows_in_file}>"
        )
