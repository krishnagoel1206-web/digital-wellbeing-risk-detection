"""Personal-baseline deviation detection.

THE REQUIREMENT THIS IMPLEMENTS
-------------------------------
The brief is explicit: do not rely on fixed screen-time thresholds, and do
not flag a naturally heavy user merely for being above the population
average. So nothing here compares the user to anyone else. Every judgement is
made against THIS person's own history.

Concretely: a user who habitually spends 8 hours a day on their phone has a
baseline of 8 hours. An 8-hour day is normal FOR THEM and is not flagged. A
4-hour day might be.

WHY ROBUST STATISTICS, NOT MEAN AND STANDARD DEVIATION
------------------------------------------------------
Mean and standard deviation are themselves distorted by the very anomalies we
are hunting. One 14-hour binge inflates the standard deviation enough to make
itself look ordinary - the outlier masks itself.

So the baseline uses the median and the Median Absolute Deviation (MAD).
Both are resistant to a minority of extreme values. MAD is scaled by 1.4826
so that, for normally distributed data, it estimates the same quantity as the
standard deviation and the resulting scores read like familiar z-scores.

WHY LEAVE-ONE-OUT
-----------------
When judging day t, day t is EXCLUDED from the baseline it is compared
against. Otherwise every day contributes to its own definition of normal,
which shrinks its apparent deviation - a mild form of leakage that makes a
detector look worse than it is on single-day spikes.

WHY MAHALANOBIS DISTANCE AS WELL
--------------------------------
Per-feature scores catch "this one number is unusual". They cannot catch
"these numbers are individually fine but never occur together" - for example
normal total screen time combined with unusually high late-night use. The
brief asks specifically for unusual COMBINATIONS, so a Mahalanobis distance
over the correlated feature space is computed alongside, with a chi-squared
p-value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

# Shared with the other models: a missing measurement is a different
# condition from a missing day, and must not be reported as one.
from app.ml.classification.inference import MissingFeatures

#: Scaling constant making MAD a consistent estimator of sigma for normal data.
MAD_TO_SIGMA = 1.4826

#: Robust z above which a single feature is considered deviant.
FEATURE_Z_THRESHOLD = 3.0

#: Chi-squared p-value below which the multivariate combination is unusual.
MAHALANOBIS_P_THRESHOLD = 0.01

#: Minimum history before any baseline claim is made. Matches the API's
#: configured minimum; below this the module refuses rather than guessing.
MIN_DAYS_FOR_BASELINE = 14

#: Features the baseline is built from. All are personal-only - note that
#: late_night_ratio is included here precisely because the population
#: datasets do not contain it.
BASELINE_FEATURES = [
    "total_screen_minutes",
    "social_ratio",
    "late_night_ratio",
    "notification_count",
    "app_launches",
    "sleep_hours",
]

#: Plain-language names, so explanations avoid feature jargon.
FEATURE_LABELS = {
    "total_screen_minutes": "screen time",
    "social_ratio": "share of time on social media",
    "late_night_ratio": "share of use late at night",
    "notification_count": "notifications",
    "app_launches": "app opens",
    "sleep_hours": "sleep",
}


def format_value(feature: str, value: float) -> str:
    """A feature value the way a person would write it."""
    if feature == "total_screen_minutes":
        m = int(round(value))
        return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"
    if feature.endswith("_ratio"):
        return f"{value * 100:.0f}%"
    if feature == "sleep_hours":
        return f"{value:.1f} h"
    return f"{value:,.0f}"


class InsufficientHistory(Exception):
    """Raised when there are too few days to establish a personal baseline."""

    def __init__(self, have: int, need: int) -> None:
        self.have = have
        self.need = need
        super().__init__(
            f"a reliable personal baseline needs {need} days of history, have {have}"
        )


@dataclass
class FeatureDeviation:
    """How far one feature strayed from this person's normal."""

    feature: str
    label: str
    value: float
    #: This person's typical value (median of all other days).
    baseline: float
    #: Robust spread of their normal range.
    spread: float
    robust_z: float
    direction: str  # "above" or "below"
    is_deviant: bool

    def explain(self) -> str:
        """One sentence a non-technical user can read.

        Phrased without a verb after the label, because labels are a mix of
        singular and plural ("sleep", "notifications") and any single verb
        would be ungrammatical for half of them.
        """
        if not self.is_deviant:
            return f"{self.label.capitalize()}: normal for you."
        return (
            f"{self.label.capitalize()} {self.direction} your usual level: "
            f"{format_value(self.feature, self.value)} versus your typical "
            f"{format_value(self.feature, self.baseline)}."
        )


@dataclass
class DayAssessment:
    """The verdict for one day."""

    date: str
    is_anomalous: bool
    #: "none", "mild", "moderate" or "severe".
    severity: str
    #: Largest absolute robust z across features.
    max_abs_z: float
    mahalanobis_distance: float | None
    mahalanobis_p: float | None
    #: True when the day is unusual as a COMBINATION rather than on any
    #: single feature - the case a per-feature threshold would miss.
    unusual_combination: bool
    deviations: list[FeatureDeviation] = field(default_factory=list)
    explanation: str = ""

    def contributing(self) -> list[FeatureDeviation]:
        """Deviant features, strongest first."""
        return sorted(
            (d for d in self.deviations if d.is_deviant),
            key=lambda d: -abs(d.robust_z),
        )


def _robust_center_spread(values: np.ndarray) -> tuple[float, float]:
    """Median and MAD-based spread of a 1-D array.

    When MAD is zero (a perfectly constant feature) the spread falls back to
    a small positive number derived from the data's own scale, so the z-score
    stays finite instead of becoming infinite or NaN.
    """
    clean = values[np.isfinite(values)]
    if clean.size == 0:
        return float("nan"), float("nan")

    median = float(np.median(clean))
    mad = float(np.median(np.abs(clean - median)))
    spread = mad * MAD_TO_SIGMA

    if spread <= 0:
        # Fall back to a conservative fraction of the level, never zero.
        spread = max(abs(median) * 0.05, 1e-6)

    return median, spread


def _severity(max_abs_z: float, unusual_combination: bool) -> str:
    """Map the evidence onto a four-level severity label."""
    if max_abs_z >= 6.0:
        return "severe"
    if max_abs_z >= 4.5:
        return "moderate"
    if max_abs_z >= FEATURE_Z_THRESHOLD or unusual_combination:
        return "mild"
    return "none"


def assess_history(
    features: pd.DataFrame,
    *,
    min_days: int = MIN_DAYS_FOR_BASELINE,
    feature_columns: list[str] | None = None,
) -> list[DayAssessment]:
    """Assess every day in the history against this person's own baseline.

    `features` must contain `record_date` plus the baseline feature columns
    (produced by feature_engineering.build_personal_features).

    Each day is scored LEAVE-ONE-OUT: the baseline it is compared against is
    computed from every other day.
    """
    if features.empty:
        raise InsufficientHistory(have=0, need=min_days)

    df = features.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    df = df.sort_values("record_date").reset_index(drop=True)

    if len(df) < min_days:
        raise InsufficientHistory(have=len(df), need=min_days)

    wanted = list(feature_columns or BASELINE_FEATURES)
    available = [
        c for c in wanted if c in df.columns and df[c].notna().sum() >= min_days
    ]
    if not available:
        # The day count is sufficient - that was checked above. What is
        # missing is any FEATURE with enough non-null days to build a baseline
        # from, so the message must name the measurements rather than repeat a
        # day count the user has already met.
        raise MissingFeatures(
            features=wanted,
            days=len(df),
            blocked_rows={
                c: int(len(df) - (df[c].notna().sum() if c in df.columns else 0))
                for c in wanted
            },
        )

    matrix = df[available].to_numpy(dtype=float)

    # Robust covariance for the multivariate check. Features are first put on
    # a common robust scale so that one large-magnitude feature (notification
    # counts in the hundreds) cannot dominate the distance purely by units.
    centers = np.empty(len(available))
    spreads = np.empty(len(available))
    for j, col in enumerate(available):
        centers[j], spreads[j] = _robust_center_spread(matrix[:, j])

    scaled = (matrix - centers) / spreads
    finite_rows = np.isfinite(scaled).all(axis=1)

    inv_cov: np.ndarray | None = None
    if finite_rows.sum() > len(available) + 1:
        cov = np.cov(scaled[finite_rows], rowvar=False)
        # Ridge the covariance so it stays invertible when features are
        # near-collinear; without this the inverse can explode.
        cov = cov + np.eye(len(available)) * 1e-6
        try:
            inv_cov = np.linalg.inv(cov)
        except np.linalg.LinAlgError:
            inv_cov = None

    assessments: list[DayAssessment] = []

    for i in range(len(df)):
        deviations: list[FeatureDeviation] = []
        max_abs_z = 0.0

        for j, col in enumerate(available):
            value = matrix[i, j]
            if not np.isfinite(value):
                continue

            # LEAVE-ONE-OUT: exclude this day from its own baseline.
            others = np.delete(matrix[:, j], i)
            baseline, spread = _robust_center_spread(others)
            if not np.isfinite(baseline) or not np.isfinite(spread):
                continue

            z = float((value - baseline) / spread)
            is_deviant = abs(z) >= FEATURE_Z_THRESHOLD
            max_abs_z = max(max_abs_z, abs(z))

            deviations.append(
                FeatureDeviation(
                    feature=col,
                    label=FEATURE_LABELS.get(col, col),
                    value=float(value),
                    baseline=baseline,
                    spread=spread,
                    robust_z=round(z, 3),
                    direction="above" if z > 0 else "below",
                    is_deviant=is_deviant,
                )
            )

        # Multivariate distance over the scaled features.
        distance: float | None = None
        p_value: float | None = None
        if inv_cov is not None and np.isfinite(scaled[i]).all():
            delta = scaled[i] - np.median(scaled[finite_rows], axis=0)
            d2 = float(delta @ inv_cov @ delta)
            distance = float(np.sqrt(max(d2, 0.0)))
            # Mahalanobis squared is approximately chi-squared with d degrees
            # of freedom when the data is multivariate normal. That assumption
            # is imperfect here, so the p-value is treated as a ranking signal
            # rather than an exact probability.
            p_value = float(stats.chi2.sf(d2, df=len(available)))

        unusual_combination = bool(
            p_value is not None
            and p_value < MAHALANOBIS_P_THRESHOLD
            and max_abs_z < FEATURE_Z_THRESHOLD
        )

        severity = _severity(max_abs_z, unusual_combination)
        is_anomalous = severity != "none"

        contributing = sorted(
            (d for d in deviations if d.is_deviant), key=lambda d: -abs(d.robust_z)
        )

        if not is_anomalous:
            explanation = "This day looks normal for you."
        elif unusual_combination and not contributing:
            explanation = (
                "No single measure was unusual, but this combination of "
                "behaviours is rare for you."
            )
        else:
            parts = [d.explain() for d in contributing[:3]]
            explanation = " ".join(parts)

        assessments.append(
            DayAssessment(
                date=df.at[i, "record_date"].date().isoformat(),
                is_anomalous=is_anomalous,
                severity=severity,
                max_abs_z=round(max_abs_z, 3),
                mahalanobis_distance=round(distance, 3) if distance is not None else None,
                mahalanobis_p=p_value,
                unusual_combination=unusual_combination,
                deviations=deviations,
                explanation=explanation,
            )
        )

    return assessments


def score_against_ground_truth(
    assessments: list[DayAssessment],
    anomaly_dates: list[str],
) -> dict:
    """Score the detector against known anomaly dates.

    This is what turns the module from a demonstration into a measurement.
    Without ground truth, an anomaly detector can only be eyeballed.

    Note precision is expected to be well below 1.0 and that is not
    necessarily a failure: the synthetic history is random, so some days are
    genuinely extreme by chance and flagging them is correct behaviour, not a
    false positive in any meaningful sense.
    """
    truth = set(anomaly_dates)
    flagged = {a.date for a in assessments if a.is_anomalous}

    true_positives = sorted(truth & flagged)
    false_negatives = sorted(truth - flagged)
    false_positives = sorted(flagged - truth)

    recall = len(true_positives) / len(truth) if truth else None
    precision = len(true_positives) / len(flagged) if flagged else None

    return {
        "n_days_assessed": len(assessments),
        "n_injected": len(truth),
        "n_flagged": len(flagged),
        "detected": true_positives,
        "missed": false_negatives,
        "extra_flagged": false_positives,
        "recall": recall,
        "precision": precision,
        "note": (
            "Recall is the key figure: it measures whether deliberately "
            "injected anomalies were found. Precision is secondary because "
            "the generated history is random, so some unflagged-in-truth days "
            "really are extreme and flagging them is correct."
        ),
    }
