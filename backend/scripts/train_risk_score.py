"""Fit and VALIDATE the PCA risk score, then demonstrate the Bayesian layer.

Run from the backend/ directory:

    .venv/Scripts/python.exe scripts/train_risk_score.py

The validation section is the point of this script. PCA finds directions of
variance, which is not the same thing as risk. This checks whether the
constructed score actually orders the dataset's own Low / Medium / High
labels, and prints the answer either way.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from app.ml.preprocessing.cleaning import clean_population_frame  # noqa: E402
from app.ml.preprocessing.dataset_loaders import (  # noqa: E402
    PRIMARY_REQUIRED,
    DatasetError,
    load_primary_dataset,
)
from app.ml.risk_scoring import bayesian, inference, pca_score  # noqa: E402

LINE = "=" * 78


def banner(text: str) -> None:
    print(f"\n{LINE}\n{text}\n{LINE}")


def run_pca() -> tuple[int, dict]:
    banner("PCA RISK SCORE - construction and validation")

    try:
        df, _ = load_primary_dataset()
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1, {}

    cleaned, _ = clean_population_frame(df, required_columns=PRIMARY_REQUIRED)
    report = pca_score.train(cleaned)

    print(f"rows      : {report['n_samples']}")
    print(f"features  : {report['features']}")

    print("\nexplained variance by component:")
    for i, (ratio, cumulative) in enumerate(
        zip(report["explained_variance_ratio"], report["cumulative_explained_variance"])
    ):
        keep = "  <- retained" if i < report["n_components_retained"] else ""
        print(f"   PC{i + 1}  {ratio:.4f}   cumulative {cumulative:.4f}{keep}")

    print(f"\ncomponents retained: {report['n_components_retained']} "
          f"(target {0.80:.0%} cumulative variance)")
    print(f"orientation: {report['orientation']}")

    print("\nretained components and their dominant features:")
    for comp in report["components"]:
        print(f"\n   PC{comp['index'] + 1}  variance {comp['explained_variance_ratio']:.4f}  "
              f"weight {comp['weight']:.4f}")
        print(f"      dominant: {', '.join(comp['dominant_features'])}")
        top = sorted(comp["loadings"].items(), key=lambda kv: -abs(kv[1]))[:4]
        for feature, loading in top:
            bar = "#" * int(abs(loading) * 30)
            print(f"      {feature:<26} {loading:+.4f} {bar}")

    v = report["validation"]
    banner("DOES THE SCORE ACTUALLY MEASURE RISK?")
    if "spearman_rho" not in v:
        print(f"could not validate: {v.get('reason')}")
    else:
        print(f"Spearman rho vs risk ordinal : {v['spearman_rho']:+.4f}  (p = {v['spearman_p']:.3g})")
        print(f"mean score per class         : {v['mean_score_per_class']}")
        print(f"monotonic Low < Medium < High: {v['monotonic_low_medium_high']}")
        print(f"Low-to-High separation       : {v['low_to_high_separation']} score points")
        print(f"\nVERDICT: {'VALIDATED' if v['validated'] else 'NOT VALIDATED'}")
        if not v["validated"]:
            print("   The constructed score does NOT order the known risk classes "
                  "well enough to be called a risk score. This is reported rather "
                  "than hidden.")

    k = report["kernel_pca_comparison"]
    if k.get("attempted") and "spearman_rho_pc1" in k:
        print(f"\nKernel PCA (rbf) PC1 rho vs risk ordinal: {k['spearman_rho_pc1']:+.4f}")
        print(f"   {k['limitation']}")
    print(f"\n{report['adoption_reason']}")

    return 0, report


def run_bayesian_demo() -> int:
    banner("BAYESIAN CONFIDENCE LAYER")

    prior = bayesian.describe_prior()
    print(f"prior: {prior['distribution']} ({prior['name']})")
    print(f"   prior mean {prior['prior_mean']:.3f}, worth "
          f"{prior['equivalent_prior_observations']} prior observation(s)")
    print(f"   {prior['rationale']}")

    print("\nHow the interval responds to more data (same 20% observed rate):")
    print(f"\n{'days':>6} {'elevated':>9} {'n_eff':>8} {'rate':>8} "
          f"{'95% credible interval':>26} {'width':>8}")
    print("-" * 70)
    for n in (5, 10, 30, 90, 365):
        k = max(1, round(0.2 * n))
        est = bayesian.estimate_rate(successes=k, trials=n, autocorrelation=0.52)
        flag = "" if est.is_reliable else "  (too weak)"
        print(f"{n:>6} {k:>9} {est.effective_trials:>8.1f} {est.mean:>7.1%} "
              f"   [{est.lower:>6.1%}, {est.upper:>6.1%}]        "
              f"{est.width:>6.1%}{flag}")

    print("\nThe interval narrows as days accumulate, which is the behaviour the")
    print("brief requires: with little data the app must admit it does not know.")

    print("\nEffect of the autocorrelation correction at n = 365, k = 73:")
    naive = bayesian.estimate_rate(successes=73, trials=365, autocorrelation=None)
    corrected = bayesian.estimate_rate(successes=73, trials=365, autocorrelation=0.52)
    print(f"   assuming independence : [{naive.lower:.1%}, {naive.upper:.1%}]  "
          f"width {naive.width:.1%}  (n_eff {naive.effective_trials})")
    print(f"   corrected for rho=0.52: [{corrected.lower:.1%}, {corrected.upper:.1%}]  "
          f"width {corrected.width:.1%}  (n_eff {corrected.effective_trials})")
    print(f"   the corrected interval is {corrected.width / naive.width:.2f}x wider - "
          "ignoring autocorrelation would overstate certainty")

    # Now run it on the real stored history.
    banner("APPLIED TO THE STORED HISTORY")

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
                "sleep_hours": r.sleep_hours,
            }
            for r in records
        ]
    )

    inference.reset_cache()
    try:
        result = inference.score_with_confidence(daily)
    except inference.InsufficientHistory as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    print(f"current score : {result['current_score']} / 100  ({result['band']} risk)")
    print(f"days scored   : {result['days_scored']}")
    print(f"elevated days : {result['elevated_days']} "
          f"(score >= {result['elevated_threshold']})")

    b = result["bayesian"]
    print(f"\nunderlying elevated-day rate: {b['estimated_rate']:.1%}")
    print(f"95% credible interval       : "
          f"[{b['credible_interval'][0]:.1%}, {b['credible_interval'][1]:.1%}]")
    print(f"posterior                   : {b['posterior']}")
    print(f"observed                    : {b['observed']}  "
          f"-> {b['effective_trials']} effective (rho = {b['autocorrelation']})")
    print(f"reliable                    : {b['is_reliable']}")
    print(f"\n{b['explanation']}")

    print("\ntop contributions to the current score (standardised value x net loading):")
    ranked = sorted(result["contributions"].items(), key=lambda kv: -abs(kv[1]))
    for feature, value in ranked[:5]:
        print(f"   {feature:<26} {value:+.4f}")

    return 0


def main() -> int:
    status, _ = run_pca()
    if status:
        return status
    return run_bayesian_demo()


if __name__ == "__main__":
    raise SystemExit(main())
