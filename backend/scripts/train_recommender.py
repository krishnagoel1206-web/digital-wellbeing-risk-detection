"""Fit the KNN recommender and demonstrate it on the stored history.

Run from the backend/ directory:

    .venv/Scripts/python.exe scripts/train_recommender.py

The interesting output is the EVIDENCE table: which features the dataset can
actually support a risk-related recommendation for, and which cannot.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.ml.anomaly_detection import personal_baseline as pb  # noqa: E402
from app.ml.preprocessing.cleaning import clean_population_frame  # noqa: E402
from app.ml.preprocessing.dataset_loaders import (  # noqa: E402
    PRIMARY_REQUIRED,
    DatasetError,
    load_primary_dataset,
)
from app.ml.preprocessing.feature_engineering import build_personal_features  # noqa: E402
from app.ml.recommendations import inference, knn_engine  # noqa: E402

LINE = "=" * 78


def banner(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def run_training() -> int:
    banner("KNN RECOMMENDER - fitting and evidence assessment")

    try:
        df, _ = load_primary_dataset()
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cleaned, _ = clean_population_frame(df, required_columns=PRIMARY_REQUIRED)
    report = knn_engine.train(cleaned)

    print(f"rows indexed : {report['n_samples']}")
    print(f"neighbours   : {report['n_neighbours']}")

    print(f"\n{'feature':<26} {'mutual info':>12} {'grounded?':>10}")
    print("-" * 52)
    for feature, info in sorted(
        report["mutual_information"].items(), key=lambda kv: -kv[1]
    ):
        grounded = feature in report["population_grounded_features"]
        mark = "YES" if grounded else "no"
        print(f"{feature:<26} {info:>12.5f} {mark:>10}")

    print(f"\npopulation-grounded : {report['population_grounded_features']}")
    print(f"personal-only       : {report['personal_only_features']}")

    print("\nwhy each feature falls where it does:")
    for feature, note in report["evidence"].items():
        print(f"   {feature}:")
        print(f"      {note}")

    print(f"\nALLOWED CLAIMS   : {report['claim_policy']['allowed']}")
    print(f"FORBIDDEN CLAIMS : {report['claim_policy']['forbidden']}")

    return 0


def run_demo() -> int:
    banner("RECOMMENDATIONS FOR THE STORED HISTORY")

    from app.database.session import SessionLocal
    from app.services import usage_service

    db = SessionLocal()
    try:
        records = usage_service.list_records(db, limit=2000)
    finally:
        db.close()

    if not records:
        print("No usage records; seed a history first.", file=sys.stderr)
        return 1

    daily = pd.DataFrame(
        [
            {
                "record_date": r.record_date,
                "total_screen_minutes": r.total_screen_minutes,
                "social_minutes": r.social_minutes,
                "gaming_minutes": r.gaming_minutes,
                "productivity_minutes": r.productivity_minutes,
                "app_launches": r.app_launches,
                "notification_count": r.notification_count,
                "late_night_minutes": r.late_night_minutes,
                "sleep_hours": r.sleep_hours,
            }
            for r in records
        ]
    )

    features, _ = build_personal_features(daily)
    try:
        assessments = pb.assess_history(features)
    except pb.InsufficientHistory:
        assessments = []
        print("(not enough history for personal-baseline recommendations)")

    inference.reset_cache()
    result = inference.recommend(daily, assessments)

    n = result["neighbourhood"]
    print(f"nearest neighbours      : {n['n_neighbours']}")
    print(f"their risk distribution : {n['neighbour_risk_distribution']}")
    print(f"low-risk neighbours     : {n['n_low_risk_neighbours']}")
    print(f"comparison possible     : {n['comparison_possible']}")
    if not n["comparison_possible"]:
        print(f"   {n.get('why_no_comparison')}")

    print(f"\nrecommendations: {result['n_recommendations']} "
          f"(population {result['sources']['population_comparison']}, "
          f"personal {result['sources']['personal_baseline']})")

    for rec in result["recommendations"]:
        print(f"\n  [{rec['priority']}] {rec['title']}")
        print(f"      basis : {rec['evidence_basis']}  ({rec['claim_type']})")
        print(f"      {rec['detail']}")
        print(f"      action: {rec['action']}")
        ev = rec["evidence"]
        numbers = {
            k: v for k, v in ev.items() if k not in {"note", "feature"}
        }
        print(f"      evidence: {numbers}")

    print("\ncaveats:")
    for caveat in result["caveats"]:
        print(f"   - {caveat}")

    return 0


def main() -> int:
    status = run_training()
    if status:
        return status
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
