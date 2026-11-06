"""Seed the database with a synthetic daily history.

Run from the backend/ directory:

    .venv/Scripts/python.exe scripts/seed_synthetic_history.py --days 60
    .venv/Scripts/python.exe scripts/seed_synthetic_history.py --profile late_night_scroller
    .venv/Scripts/python.exe scripts/seed_synthetic_history.py --clear

Every row inserted is tagged source=synthetic, and the ground-truth anomaly
manifest is written to data/synthetic/ so Phase 6 can score the anomaly
detector against known answers instead of guessing.

Existing MANUAL records are never touched. --clear removes only synthetic
rows, so a user's real entries survive a reseed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the backend package importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database.models import DataSource  # noqa: E402
from app.database.session import SessionLocal, init_db  # noqa: E402
from app.ml.preprocessing.synthetic import PROFILES, generate_history  # noqa: E402
from app.schemas.usage import UsageRecordCreate  # noqa: E402
from app.services import usage_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=60, help="How many days to generate.")
    parser.add_argument(
        "--profile",
        default="balanced",
        choices=sorted(PROFILES),
        help="Which behaviour profile to simulate.",
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (reproducibility).")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete existing synthetic rows first (manual rows are kept).",
    )
    parser.add_argument(
        "--no-anomalies",
        action="store_true",
        help="Generate a clean history with no injected anomalies.",
    )
    args = parser.parse_args()

    settings = get_settings()
    init_db()

    result = generate_history(
        days=args.days,
        profile_name=args.profile,
        seed=args.seed,
        inject_anomalies=not args.no_anomalies,
    )

    db = SessionLocal()
    try:
        if args.clear:
            removed = usage_service.delete_by_source(db, DataSource.SYNTHETIC)
            print(f"cleared {removed} existing synthetic record(s)")

        inserted = 0
        skipped = 0
        errors: list[str] = []

        for row in result.records:
            try:
                payload = UsageRecordCreate(**row)
            except Exception as exc:
                # A generated row that fails our own validation is a bug in
                # the generator, so it is surfaced rather than swallowed.
                errors.append(f"{row['record_date']}: validation failed - {exc}")
                continue

            try:
                usage_service.create_record(db, payload)
                inserted += 1
            except usage_service.DuplicateDateError:
                skipped += 1

        print(f"\ninserted {inserted} record(s), skipped {skipped} existing date(s)")
        if errors:
            print(f"\n{len(errors)} generated row(s) FAILED validation:")
            for err in errors[:10]:
                print("   ", err)
            print("   (this indicates a generator bug, not a data problem)")

        summary = usage_service.get_summary(db)
        print("\n--- database summary ---")
        print(f"total records        : {summary.total_records}")
        print(f"date range           : {summary.first_date} .. {summary.last_date}")
        print(f"by source            : {summary.records_by_source}")
        print(f"baseline possible    : {summary.has_enough_for_baseline} "
              f"(needs {summary.min_days_for_baseline})")
        print(f"contains synthetic   : {summary.contains_synthetic_data}")
    finally:
        db.close()

    # Persist the ground truth for Phase 6 scoring.
    settings.ensure_directories()
    manifest_path = settings.synthetic_data_dir / "anomaly_ground_truth.json"
    manifest_path.write_text(
        json.dumps(
            {
                "seed": result.seed,
                "profile": result.profile,
                "anomalies": result.anomalies,
                "notes": result.notes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\nground-truth manifest written to {manifest_path}")
    print(f"injected anomalies ({len(result.anomalies)}):")
    for anomaly in result.anomalies:
        print(f"   {anomaly['date']}  {anomaly['kind']:<20} {anomaly['description']}")

    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
