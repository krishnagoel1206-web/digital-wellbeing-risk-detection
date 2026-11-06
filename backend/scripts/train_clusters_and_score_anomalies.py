"""Train clustering and SCORE the anomaly detector against ground truth.

Run from the backend/ directory:

    .venv/Scripts/python.exe scripts/train_clusters_and_score_anomalies.py

The anomaly half of this script is a genuine evaluation, not a demonstration.
The synthetic generator recorded which days it deliberately made abnormal, in
data/synthetic/anomaly_ground_truth.json. This script asks the detector to
assess every day and then checks how many of those known anomalies it found.

Without that check, an anomaly detector can only be admired, not measured.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.ml.anomaly_detection import personal_baseline as pb  # noqa: E402
from app.ml.clustering import personas  # noqa: E402
from app.ml.preprocessing.cleaning import clean_population_frame  # noqa: E402
from app.ml.preprocessing.dataset_loaders import (  # noqa: E402
    PRIMARY_REQUIRED,
    DatasetError,
    load_primary_dataset,
)
from app.ml.preprocessing.feature_engineering import build_personal_features  # noqa: E402

LINE = "=" * 78


def banner(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def run_clustering() -> int:
    banner("CLUSTERING - personas from the population dataset")

    try:
        df, _ = load_primary_dataset()
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cleaned, _ = clean_population_frame(df, required_columns=PRIMARY_REQUIRED)
    report = personas.train(cleaned)

    print(f"rows clustered : {report['n_samples']}")
    print(f"features       : {report['features']}")
    print(f"\nk selection (silhouette over k = 2..6):")
    for entry in report["k_selection"]["sweep"]:
        marker = "  <- chosen" if entry["k"] == report["k_selection"]["chosen_k"] else ""
        print(f"   k={entry['k']}  silhouette {entry['silhouette']:.4f}"
              f"  inertia {entry['inertia']:>12,.0f}{marker}")

    print(f"\n{'algorithm':<10} {'clusters':>9} {'noise':>7} {'silhouette':>11} "
          f"{'cal-har':>10} {'dav-bould':>10}")
    print("-" * 62)
    for name, res in report["algorithms"].items():
        s = res["scores"]
        sil = f"{s['silhouette']:.4f}" if s["silhouette"] is not None else "n/a"
        ch = f"{s['calinski_harabasz']:.1f}" if s["calinski_harabasz"] is not None else "n/a"
        db = f"{s['davies_bouldin']:.4f}" if s["davies_bouldin"] is not None else "n/a"
        print(f"{name:<10} {res['n_clusters']:>9} {res['noise_share']:>6.1%} "
              f"{sil:>11} {ch:>10} {db:>10}")

    eps = report["dbscan_eps_selection"]
    print(f"\nDBSCAN eps chosen from the data: {eps['eps']:.3f}")
    print(f"   method: {eps['method']}")
    print(f"   kth-NN distance: min {eps['kth_distance_min']:.2f}, "
          f"median {eps['kth_distance_median']:.2f}, max {eps['kth_distance_max']:.2f}")

    print(f"\nKMeans vs BIRCH agreement (adjusted Rand index): "
          f"{report['kmeans_birch_agreement_ari']:.4f}")

    struct = report["overall_structure"]
    print(f"\n*** CLUSTER STRUCTURE: {struct['verdict'].upper()} "
          f"(silhouette {struct.get('silhouette')}) ***")
    print(f"    {struct['detail']}")

    ks = report["k_selection"]
    if not ks["selection_is_meaningful"]:
        print(f"    k selection is NOT meaningful: silhouette varies by only "
              f"{ks['silhouette_spread']} across k=2..6, so the chosen k is arbitrary.")

    print(f"\nPERSONAS DERIVED BY {report['deployed_model']} "
          f"(names come from centroid values, not hand-assigned):")
    for persona in report["algorithms"][report["deployed_model"]]["personas"]:
        print(f"\n  [{persona['cluster_id']}] {persona['name']}")
        print(f"      {persona['size']} people ({persona['share']:.1%})")
        if persona["traits"]:
            print(f"      traits: {', '.join(persona['traits'])}")
        notable = sorted(
            persona["centroid_z"].items(), key=lambda kv: -abs(kv[1])
        )[:3]
        detail = ", ".join(
            f"{k}={persona['centroid_raw'][k]} ({v:+.2f} sd)" for k, v in notable
        )
        print(f"      {detail}")

    print(f"\nNOTE: {report['late_night_note']}")
    return 0


def run_anomaly_scoring() -> int:
    banner("ANOMALY DETECTION - scored against injected ground truth")

    settings = get_settings()

    from app.database.session import SessionLocal
    from app.services import usage_service

    db = SessionLocal()
    try:
        records = usage_service.list_records(db, limit=2000)
    finally:
        db.close()

    if not records:
        print("No usage records. Seed a history first:", file=sys.stderr)
        print("   scripts/seed_synthetic_history.py --days 365 --clear", file=sys.stderr)
        return 1

    raw = pd.DataFrame(
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

    features, _ = build_personal_features(raw)

    try:
        assessments = pb.assess_history(features)
    except pb.InsufficientHistory as exc:
        print(f"\n{exc}", file=sys.stderr)
        print("Refusing to report a baseline on too little data is the "
              "intended behaviour, not a failure.", file=sys.stderr)
        return 1

    flagged = [a for a in assessments if a.is_anomalous]
    print(f"days assessed : {len(assessments)}")
    print(f"days flagged  : {len(flagged)} ({len(flagged) / len(assessments):.1%})")

    by_severity: dict[str, int] = {}
    for a in assessments:
        by_severity[a.severity] = by_severity.get(a.severity, 0) + 1
    print(f"by severity   : {by_severity}")

    combination_only = [a for a in flagged if a.unusual_combination]
    print(f"flagged on COMBINATION alone (no single feature deviant): "
          f"{len(combination_only)}")

    # Score against ground truth.
    manifest_path = settings.synthetic_data_dir / "anomaly_ground_truth.json"
    if not manifest_path.exists():
        print(f"\nNo ground-truth manifest at {manifest_path}; cannot score.")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    injected = manifest.get("anomalies", [])
    injected_dates = [a["date"] for a in injected]

    score = pb.score_against_ground_truth(assessments, injected_dates)

    print(f"\n--- SCORED AGAINST {len(injected_dates)} INJECTED ANOMALIES ---")
    recall_text = f"{score['recall']:.1%}" if score["recall"] is not None else "n/a"
    precision_text = (
        f"{score['precision']:.1%}" if score["precision"] is not None else "n/a"
    )
    print(f"recall    : {recall_text}  ({len(score['detected'])}/{score['n_injected']} found)")
    print(f"precision : {precision_text}")

    lookup = {a.date: a for a in assessments}
    for anomaly in injected:
        date = anomaly["date"]
        found = date in score["detected"]
        mark = "DETECTED" if found else "MISSED"
        print(f"\n  {date}  {anomaly['kind']:<20} [{mark}]")
        print(f"      injected: {anomaly['description']}")
        assessment = lookup.get(date)
        if assessment:
            print(f"      severity: {assessment.severity}  max|z|={assessment.max_abs_z}")
            for dev in assessment.contributing()[:3]:
                print(f"      -> {dev.label}: {dev.value:.1f} vs usual "
                      f"{dev.baseline:.1f} (z={dev.robust_z:+.2f})")
            if not assessment.contributing():
                print(f"      -> no single feature deviant; "
                      f"mahalanobis p={assessment.mahalanobis_p}")

    print(f"\n{score['note']}")

    # Show the most extreme day the detector found that was NOT injected -
    # useful for judging whether extra flags are reasonable.
    extras = [lookup[d] for d in score["extra_flagged"] if d in lookup]
    if extras:
        worst = max(extras, key=lambda a: a.max_abs_z)
        print(f"\nmost extreme non-injected flag: {worst.date} "
              f"(severity {worst.severity}, max|z|={worst.max_abs_z})")
        print(f"   {worst.explanation}")

    return 0


def main() -> int:
    status = run_clustering()
    status |= run_anomaly_scoring()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
