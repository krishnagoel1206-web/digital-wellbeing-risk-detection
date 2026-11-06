"""Train all models and write artifacts.

Run from the backend/ directory:

    .venv/Scripts/python.exe scripts/train_models.py
    .venv/Scripts/python.exe scripts/train_models.py --only classification
    .venv/Scripts/python.exe scripts/train_models.py --only regression

THIS IS THE ONLY PLACE MODELS ARE FITTED.
The API never trains. It loads the .joblib artifacts this script produces and
calls predict(). That separation is what makes it possible to state honestly
whether a model exists: if the artifact is absent, /api/health reports it as
untrained and the app hides the corresponding section rather than inventing
a number.

Artifacts are written to app/ml/artifacts/:
    <name>.joblib          the fitted pipeline plus its metadata
    <name>_metrics.json    every score measured at training time
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.database.models import DataSource  # noqa: E402
from app.ml.classification import train_risk  # noqa: E402
from app.ml.preprocessing.cleaning import clean_population_frame  # noqa: E402
from app.ml.preprocessing.dataset_loaders import (  # noqa: E402
    PRIMARY_REQUIRED,
    DatasetError,
    load_primary_dataset,
)
from app.ml.regression import train_forecast  # noqa: E402

LINE = "=" * 78


def banner(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def run_classification() -> int:
    banner("CLASSIFICATION - risk_class from the population dataset")

    try:
        df, load_report = load_primary_dataset()
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Place the dataset in backend/data/raw/ - see README.md", file=sys.stderr)
        return 1

    print(f"loaded {load_report.rows} rows from {Path(load_report.path).name}")
    for note in load_report.notes:
        print(f"  - {note}")

    cleaned, clean_report = clean_population_frame(
        df, required_columns=PRIMARY_REQUIRED + ["risk_class"]
    )
    print(f"\ncleaning: {clean_report.summary()}")
    for note in clean_report.notes:
        print(f"  - {note}")

    print("\ntraining seven classifiers...")
    report = train_risk.train(cleaned)

    print(f"\nclass distribution : {report['class_distribution']}")
    print(f"majority baseline  : {report['majority_baseline']:.4f}")
    print(f"train / test split : {report['n_train']} / {report['n_test']}")
    print(f"features used      : {report['feature_columns']}")

    print(f"\n{'model':<22} {'CV acc':>8} {'CV F1':>8} {'test acc':>9} {'test F1':>8} {'ROC-AUC':>8}")
    print("-" * 70)
    for entry in sorted(report["models"], key=lambda m: -m["cv_f1_macro"]):
        test = entry["test"]
        auc = test.get("roc_auc_ovr")
        auc_text = f"{auc:.4f}" if auc is not None else "n/a"
        print(
            f"{entry['name']:<22} {entry['cv_accuracy']:>8.4f} {entry['cv_f1_macro']:>8.4f} "
            f"{test['accuracy']:>9.4f} {test['f1_macro']:>8.4f} {auc_text:>8}"
        )

    print(f"\nselected: {report['best_model']}")
    print(f"rule    : {report['selection_rule']}")

    best = next(m for m in report["models"] if m["name"] == report["best_model"])
    print(f"\nconfusion matrix on the held-out test set (rows = true):")
    labels = best["test"]["labels"]
    print("          " + "".join(f"{lab:>9}" for lab in labels))
    for lab, row in zip(labels, best["test"]["confusion"]):
        print(f"{lab:>9} " + "".join(f"{v:>9}" for v in row))

    if best["feature_importance"]:
        print("\nfeature importance (selected model):")
        for feat, value in sorted(best["feature_importance"].items(), key=lambda t: -t[1]):
            print(f"   {feat:<28} {value:.4f}")

    return 0


#: Which provenances --sources maps to. Mirrors the API's AnalysisScope so
#: "trained on device data" means the same thing in both places.
SOURCE_SCOPES = {
    "device": [DataSource.DEVICE],
    "real": [DataSource.DEVICE, DataSource.MANUAL, DataSource.CSV],
    "all": None,
}


def run_regression(scope: str = "all") -> int:
    banner("REGRESSION - next-day screen-time forecast from your own history")

    daily = train_forecast.load_daily_from_db(sources=SOURCE_SCOPES[scope])
    print(f"scope              : {scope}")
    print(f"loaded {len(daily)} daily record(s) from the database")

    if "source" in daily.columns and not daily.empty:
        counts = daily["source"].value_counts().to_dict()
        print("by source          : " + ", ".join(f"{k}={v}" for k, v in counts.items()))

    if daily.empty:
        print(
            "\nNo usage records found. Seed demonstration data first:\n"
            "   .venv/Scripts/python.exe scripts/seed_synthetic_history.py --days 90",
            file=sys.stderr,
        )
        return 1

    try:
        report = train_forecast.train(daily)
    except train_forecast.NotEnoughHistory as exc:
        print(f"\nNot enough history: {exc}", file=sys.stderr)
        print(
            "The app reports insufficient history rather than guessing - this is "
            "the intended behaviour, not a failure.",
            file=sys.stderr,
        )
        return 1

    td = report.get("training_data", {})
    print("")
    print(f"fitted on          : {td.get('fitted_on')}")
    print(f"   {td.get('note')}")

    print("")
    print(f"usable rows        : {report['n_samples']} "
          f"({report['rows_dropped_warmup']} dropped as lag warm-up)")
    print(f"cv strategy        : {report['cv_strategy']}")
    print(f"naive baseline RMSE: {report['naive_persistence_rmse']:.2f} minutes")

    if report["sample_size_warning"]:
        print(f"\nWARNING: {report['sample_size_warning']}")

    print(f"\n{'model':<20} {'RMSE':>9} {'MAE':>9} {'R2':>9} {'RMSE %mean':>11} {'beats naive':>12}")
    print("-" * 74)
    for entry in sorted(report["models"], key=lambda m: m["rmse"]):
        beats = entry["beats_naive"]
        beats_text = "yes" if beats else ("no" if beats is False else "n/a")
        print(
            f"{entry['name']:<20} {entry['rmse']:>9.2f} {entry['mae']:>9.2f} "
            f"{entry['r2']:>9.4f} {entry['rmse_pct_of_mean']:>10.1f}% {beats_text:>12}"
        )

    print(f"\nselected: {report['best_model']}")

    best = next(m for m in report["models"] if m["name"] == report["best_model"])
    print("\ncoefficients (on standardised features, so they are comparable):")
    for feat, value in sorted(best["coefficients"].items(), key=lambda t: -abs(t[1])):
        print(f"   {feat:<16} {value:>10.3f}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources",
        choices=sorted(SOURCE_SCOPES),
        default="all",
        help=(
            "Which data provenances to TRAIN the forecaster on. 'device' uses "
            "only days measured on the phone; 'all' includes generated "
            "demonstration days, which demonstrates the pipeline but does not "
            "produce a personal forecast."
        ),
    )
    parser.add_argument(
        "--only",
        choices=["regression", "classification"],
        help="Train just one task instead of all.",
    )
    args = parser.parse_args()

    settings = get_settings()
    settings.ensure_directories()

    status = 0
    if args.only in (None, "classification"):
        status |= run_classification()
    if args.only in (None, "regression"):
        status |= run_regression(args.sources)

    banner("ARTIFACTS ON DISK")
    for path in sorted(settings.artifacts_dir.iterdir()):
        if path.suffix in {".joblib", ".json"}:
            print(f"   {path.name:<44} {path.stat().st_size:>9,} bytes")

    return status


if __name__ == "__main__":
    raise SystemExit(main())
