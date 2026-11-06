"""INFERENCE for the recommendation engine.

Generates suggestions from TWO clearly separated evidence sources:

  1. POPULATION comparison (KNN). The user is compared with the low-risk
     subset of their nearest neighbours. Only features measured as
     risk-relevant in this dataset may produce these, and the claim is
     always associational.

  2. PERSONAL baseline. A behaviour that is unusual FOR THIS USER, from the
     Phase 6 deviation detector. This can cover features the population data
     says nothing about - notably late-night usage, which the population
     dataset does not even contain.

Keeping the two apart matters. "People like you who are lower-risk use social
media less" and "your late-night use has been unusual for you lately" are
different kinds of statement with different support, and collapsing them into
one undifferentiated list of tips would misrepresent both.

Every recommendation carries `evidence_basis`, the numbers behind it, and a
`claim_type` so nothing reads as a promise of outcome.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings
from app.ml.classification.inference import InsufficientHistory, MissingFeatures
from app.ml.recommendations.knn_engine import ARTIFACT_NAME

#: Minimum low-risk neighbours before a population comparison is reported.
#: A comparison against three people is not worth presenting.
MIN_LOW_RISK_NEIGHBOURS = 10

#: Minimum relative gap (as a fraction of the user's own value) before a
#: difference is worth mentioning. Avoids advising on noise.
MIN_RELATIVE_GAP = 0.15

#: Minimum ABSOLUTE gap, in each feature's own units, that counts as
#: practically meaningful.
#:
#: WHY BOTH THRESHOLDS EXIST: a relative threshold alone penalises heavy
#: users. A measured example from this project - a user averaging 6.67 h/day
#: against a low-risk peer median of 5.70 h/day has a gap of 0.97 h, which is
#: nearly an hour of every day, yet only 14.6% of their total. A pure 15%
#: relative rule silently discarded it. An hour a day matters regardless of
#: what fraction of the user's total it represents, so a gap now qualifies if
#: EITHER threshold is met.
MIN_ABSOLUTE_GAP: dict[str, float] = {
    "daily_screen_time_hours": 0.5,   # 30 minutes a day
    "social_media_hours": 0.5,
    "gaming_hours": 0.5,
    "notifications_per_day": 30.0,
    "app_opens_per_day": 25.0,
    "sleep_hours": 0.5,
}

#: Plain-language names and suggested actions per feature.
FEATURE_COPY: dict[str, dict[str, str]] = {
    "daily_screen_time_hours": {
        "label": "total screen time",
        "unit": "hours a day",
        "action": "Try setting a daily limit, or picking one part of the day to stay off the phone.",
    },
    "social_media_hours": {
        "label": "social media time",
        "unit": "hours a day",
        "action": "Consider removing the one app you open most often from your home screen.",
    },
    "gaming_hours": {
        "label": "gaming time",
        "unit": "hours a day",
        "action": "Try deciding the session length before you start, rather than during.",
    },
    "notifications_per_day": {
        "label": "notifications",
        "unit": "a day",
        "action": "Turn off notifications for apps that are not people messaging you.",
    },
    "app_opens_per_day": {
        "label": "app opens",
        "unit": "a day",
        "action": "A short delay before unlocking - moving apps off the first screen - breaks the habit loop.",
    },
    "sleep_hours": {
        "label": "sleep",
        "unit": "hours a night",
        "action": "Charging the phone outside the bedroom is the change most people find easiest.",
    },
}

#: Copy for personal-baseline recommendations, keyed by baseline feature.
PERSONAL_COPY: dict[str, dict[str, str]] = {
    "late_night_ratio": {
        "label": "late-night use",
        "action": "A wind-down alarm an hour before bed helps more than willpower.",
    },
    "notification_count": {
        "label": "notifications",
        "action": "Mute the noisiest apps for a few days and see if you miss anything.",
    },
    "total_screen_minutes": {
        "label": "screen time",
        "action": "Look at which app grew - usually one accounts for most of a jump.",
    },
    "social_ratio": {
        "label": "share of time on social media",
        "action": "Try replacing the first scroll of the day with something else.",
    },
    "app_launches": {
        "label": "app opens",
        "action": "Frequent checking often means notifications; reviewing them helps.",
    },
    "sleep_hours": {
        "label": "sleep",
        "action": "Protecting a consistent bedtime does more than a longer lie-in.",
    },
}


class ModelNotTrained(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"model '{name}' has not been trained. Run: "
            "python scripts/train_recommender.py"
        )


@dataclass
class Recommendation:
    """One suggestion, with its evidence attached."""

    id: str
    title: str
    detail: str
    action: str
    #: "population_comparison" or "personal_baseline".
    evidence_basis: str
    #: "associational" or "personal_deviation" - never "causal".
    claim_type: str
    priority: int
    #: The numbers behind it, so the user can check the reasoning.
    evidence: dict[str, Any] = field(default_factory=dict)


def _load_artifact_uncached() -> dict[str, Any]:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    if not path.exists():
        raise ModelNotTrained(ARTIFACT_NAME)
    return joblib.load(path)


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any]:
    return _load_artifact_uncached()


def reset_cache() -> None:
    _load_artifact.cache_clear()


def is_trained() -> bool:
    settings = get_settings()
    return (settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib").exists()


def load_metrics() -> dict | None:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _user_profile(daily: pd.DataFrame, features: list[str]) -> dict[str, float]:
    """Average the user's history into the population feature space."""

    def hours(column: str) -> float:
        if column not in daily.columns:
            return float("nan")
        series = pd.to_numeric(daily[column], errors="coerce").dropna()
        return float(series.mean() / 60.0) if not series.empty else float("nan")

    def count(column: str) -> float:
        if column not in daily.columns:
            return float("nan")
        series = pd.to_numeric(daily[column], errors="coerce").dropna()
        return float(series.mean()) if not series.empty else float("nan")

    mapping = {
        "daily_screen_time_hours": hours("total_screen_minutes"),
        "social_media_hours": hours("social_minutes"),
        "gaming_hours": hours("gaming_minutes"),
        "work_study_hours": hours("productivity_minutes"),
        "sleep_hours": count("sleep_hours"),
        "notifications_per_day": count("notification_count"),
        "app_opens_per_day": count("app_launches"),
    }
    return {f: mapping.get(f, float("nan")) for f in features}


def population_recommendations(
    daily: pd.DataFrame, *, min_days: int = 7
) -> tuple[list[Recommendation], dict]:
    """Compare the user with the low-risk subset of their nearest neighbours."""
    artifact = _load_artifact()
    features: list[str] = artifact["feature_columns"]
    scaler = artifact["scaler"]
    index = artifact["index"]
    reference_values: np.ndarray = artifact["reference_values"]
    reference_labels: np.ndarray = artifact["reference_labels"]
    evidence_map: dict = artifact["evidence"]

    if len(daily) < min_days:
        raise InsufficientHistory(have=len(daily), need=min_days)

    profile = _user_profile(daily, features)

    # A missing MEASUREMENT, reported as such. Raising InsufficientHistory
    # here produced "7 day(s) recorded, 7 needed" - refused while the stated
    # requirement was met - which is the third place in this codebase where
    # the two conditions were conflated.
    missing = [f for f, v in profile.items() if not np.isfinite(v)]
    if missing:
        raise MissingFeatures(features=missing, days=len(daily))

    X_user = scaler.transform(np.array([[profile[f] for f in features]], dtype=float))
    distances, indices = index.kneighbors(X_user)
    neighbour_idx = indices[0]

    neighbour_labels = reference_labels[neighbour_idx]
    label_counts = {
        str(label): int((neighbour_labels == label).sum())
        for label in ("Low", "Medium", "High")
    }

    low_mask = neighbour_labels == "Low"
    n_low = int(low_mask.sum())

    context = {
        "n_neighbours": int(len(neighbour_idx)),
        "neighbour_risk_distribution": label_counts,
        "n_low_risk_neighbours": n_low,
        "mean_distance": round(float(np.mean(distances[0])), 4),
        "comparison_possible": n_low >= MIN_LOW_RISK_NEIGHBOURS,
    }

    if n_low < MIN_LOW_RISK_NEIGHBOURS:
        context["why_no_comparison"] = (
            f"Only {n_low} of your {len(neighbour_idx)} nearest neighbours are "
            f"classified Low risk, which is too few ({MIN_LOW_RISK_NEIGHBOURS} "
            "needed) for a meaningful comparison. Reporting one anyway would be "
            "reading too much into a handful of records."
        )
        return [], context

    low_values = reference_values[neighbour_idx][low_mask]

    candidates: list[tuple[float, Recommendation]] = []

    for i, feature in enumerate(features):
        info = evidence_map.get(feature, {})
        if not info.get("population_grounded"):
            # The dataset does not support a risk-related claim here.
            continue

        direction = info.get("direction")
        user_value = profile[feature]
        peer_median = float(np.median(low_values[:, i]))

        if direction == "lower":
            gap = user_value - peer_median
        elif direction == "higher":
            gap = peer_median - user_value
        else:
            continue

        if gap <= 0:
            # The user is already at or better than the comparison group.
            continue

        denominator = abs(user_value) if user_value else 1.0
        relative = gap / denominator
        absolute_floor = MIN_ABSOLUTE_GAP.get(feature)

        meets_relative = relative >= MIN_RELATIVE_GAP
        meets_absolute = absolute_floor is not None and gap >= absolute_floor
        if not (meets_relative or meets_absolute):
            continue

        copy = FEATURE_COPY.get(feature, {"label": feature, "unit": "", "action": ""})
        comparator = "less" if direction == "lower" else "more"

        detail = (
            f"Your {copy['label']} averages {user_value:.1f} {copy['unit']}. "
            f"Among the {n_low} people in your neighbourhood who are classified "
            f"Low risk, the typical figure is {peer_median:.1f} - about "
            f"{abs(gap):.1f} {comparator}."
        )

        candidates.append(
            (
                relative,
                Recommendation(
                    id=f"pop_{feature}",
                    title=(
                        f"Your {copy['label']} is higher than similar lower-risk people"
                        if direction == "lower"
                        else f"Your {copy['label']} is lower than similar lower-risk people"
                    ),
                    detail=detail,
                    action=copy["action"],
                    evidence_basis="population_comparison",
                    claim_type="associational",
                    priority=0,  # assigned after ranking
                    evidence={
                        "feature": feature,
                        "your_value": round(user_value, 2),
                        "low_risk_peer_median": round(peer_median, 2),
                        "gap": round(float(gap), 2),
                        "relative_gap": round(float(relative), 3),
                        "qualified_by": (
                            "relative and absolute"
                            if meets_relative and meets_absolute
                            else ("relative gap" if meets_relative else "absolute gap")
                        ),
                        "n_low_risk_neighbours": n_low,
                        "mutual_information_with_risk": info.get("mutual_information"),
                        "note": (
                            "This is a comparison, not a prediction. The dataset "
                            "is cross-sectional, so it cannot say what would "
                            "happen if you changed this."
                        ),
                    },
                ),
            )
        )

    candidates.sort(key=lambda t: -t[0])
    recommendations = []
    for rank, (_, rec) in enumerate(candidates, start=1):
        rec.priority = rank
        recommendations.append(rec)

    return recommendations, context


def personal_recommendations(
    assessments: list, *, recent_days: int = 14, min_occurrences: int = 2
) -> list[Recommendation]:
    """Build recommendations from repeated deviations in the user's own baseline.

    Takes DayAssessment objects from the Phase 6 detector. A feature must
    deviate on at least `min_occurrences` of the recent days before it is
    raised - a single unusual day is an event, not a pattern worth advising on.
    """
    recent = sorted(assessments, key=lambda a: a.date, reverse=True)[:recent_days]

    tally: dict[str, list] = {}
    for assessment in recent:
        for deviation in assessment.contributing():
            tally.setdefault(deviation.feature, []).append(deviation)

    candidates: list[tuple[float, Recommendation]] = []

    for feature, deviations in tally.items():
        if len(deviations) < min_occurrences:
            continue

        copy = PERSONAL_COPY.get(feature, {"label": feature, "action": ""})
        strongest = max(deviations, key=lambda d: abs(d.robust_z))
        mean_z = float(np.mean([abs(d.robust_z) for d in deviations]))

        detail = (
            f"On {len(deviations)} of your last {len(recent)} days, your "
            f"{copy['label']} was {strongest.direction} your usual level. "
            f"The most extreme was {strongest.value:.1f} against your typical "
            f"{strongest.baseline:.1f}."
        )

        candidates.append(
            (
                mean_z,
                Recommendation(
                    id=f"personal_{feature}",
                    title=f"Your {copy['label']} has been unusual for you",
                    detail=detail,
                    action=copy["action"],
                    evidence_basis="personal_baseline",
                    claim_type="personal_deviation",
                    priority=0,
                    evidence={
                        "feature": feature,
                        "days_deviant": len(deviations),
                        "days_examined": len(recent),
                        "mean_abs_robust_z": round(mean_z, 2),
                        "most_extreme_value": round(strongest.value, 2),
                        "your_usual": round(strongest.baseline, 2),
                        "note": (
                            "Measured against your own history, not against "
                            "anyone else. This says the behaviour changed for "
                            "you; it does not claim a risk level."
                        ),
                    },
                ),
            )
        )

    candidates.sort(key=lambda t: -t[0])
    out = []
    for rank, (_, rec) in enumerate(candidates, start=1):
        rec.priority = rank
        out.append(rec)
    return out


def recommend(
    daily: pd.DataFrame,
    assessments: list | None = None,
    *,
    min_days: int = 7,
) -> dict:
    """Produce the combined recommendation set with full provenance."""
    population, context = population_recommendations(daily, min_days=min_days)
    personal = personal_recommendations(assessments) if assessments else []

    metrics = load_metrics() or {}

    # Interleave by priority so neither source dominates the top of the list,
    # while keeping each source's own ranking intact.
    combined: list[Recommendation] = []
    for i in range(max(len(population), len(personal))):
        if i < len(population):
            combined.append(population[i])
        if i < len(personal):
            combined.append(personal[i])

    for rank, rec in enumerate(combined, start=1):
        rec.priority = rank

    return {
        "recommendations": [asdict(r) for r in combined],
        "n_recommendations": len(combined),
        "sources": {
            "population_comparison": len(population),
            "personal_baseline": len(personal),
        },
        "neighbourhood": context,
        "claim_policy": metrics.get("claim_policy"),
        "population_grounded_features": metrics.get("population_grounded_features"),
        "personal_only_features": metrics.get("personal_only_features"),
        "caveats": [
            (
                "Population comparisons describe people with similar usage who "
                "are classified lower-risk. They are comparisons, not "
                "predictions: this dataset observed each person once, so it "
                "cannot say what happens after someone changes their habits."
            ),
            (
                "Some behaviours - notification volume and gaming among them - "
                "show no measurable relationship with the risk label in this "
                "dataset. Advice about those comes only from your own baseline, "
                "and makes no risk claim."
            ),
        ],
    }
