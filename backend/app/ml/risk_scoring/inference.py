"""INFERENCE for the risk score and its Bayesian confidence interval.

Loads the PCA artifact, scores the user, and attaches a posterior over their
underlying elevated-risk-day rate. Never fits the PCA.

WHY THE SCORE IS COMPUTED PER DAY AND THEN SUMMARISED
-----------------------------------------------------
Scoring the user's averaged "typical day" gives one number but no history and
no uncertainty. Scoring EVERY day gives a score history (which the brief asks
for), a current value, and the counts the Bayesian layer needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings
from app.ml.classification.inference import MissingFeatures
from app.ml.risk_scoring import bayesian
from app.ml.risk_scoring.pca_score import ARTIFACT_NAME, raw_to_score

#: Score at or above which a day counts as "elevated" for the Bayesian layer.
#: 70 corresponds to the 70th percentile of the training population by
#: construction, since the score IS a percentile rank.
ELEVATED_SCORE_THRESHOLD = 70.0

#: Score bands for the Low / Medium / High label shown in the app. These are
#: percentile cut-points, documented rather than arbitrary: the bottom half of
#: the population is Low, the next 20 points Medium, the top 30 High.
SCORE_BANDS = ((50.0, "Low"), (70.0, "Medium"), (100.1, "High"))


class ModelNotTrained(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"model '{name}' has not been trained. Run: "
            "python scripts/train_risk_score.py"
        )


class InsufficientHistory(Exception):
    def __init__(self, have: int, need: int) -> None:
        self.have = have
        self.need = need
        super().__init__(f"need at least {need} days to score, have {have}")


@dataclass
class ScoredDay:
    date: str
    score: float
    band: str


@dataclass
class RiskScoreResult:
    current_score: float
    band: str
    #: Per-feature contribution to today's score, in standard deviations.
    contributions: dict[str, float] = field(default_factory=dict)
    history: list[ScoredDay] = field(default_factory=list)
    days_scored: int = 0


def band_for(score: float) -> str:
    """Map a 0-100 score onto its documented band."""
    for threshold, label in SCORE_BANDS:
        if score < threshold:
            return label
    return "High"


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any]:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    if not path.exists():
        raise ModelNotTrained(ARTIFACT_NAME)
    return joblib.load(path)


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


def _personal_to_population_daily(daily: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Convert each of the user's days into the population feature space.

    The PCA was fitted on hours-per-day columns, so stored minutes are
    converted. `weekend_screen_time` is excluded from SCORE_FEATURES, so no
    weekend-specific handling is needed here.
    """
    out = pd.DataFrame(index=daily.index)

    def hours(column: str) -> pd.Series:
        if column not in daily.columns:
            return pd.Series(np.nan, index=daily.index)
        return pd.to_numeric(daily[column], errors="coerce") / 60.0

    def count(column: str) -> pd.Series:
        if column not in daily.columns:
            return pd.Series(np.nan, index=daily.index)
        return pd.to_numeric(daily[column], errors="coerce")

    mapping = {
        "daily_screen_time_hours": hours("total_screen_minutes"),
        "social_media_hours": hours("social_minutes"),
        "gaming_hours": hours("gaming_minutes"),
        "work_study_hours": hours("productivity_minutes"),
        "sleep_hours": count("sleep_hours"),
        "notifications_per_day": count("notification_count"),
        "app_opens_per_day": count("app_launches"),
    }

    for feature in features:
        out[feature] = mapping.get(feature, pd.Series(np.nan, index=daily.index))

    return out


def score_history(daily: pd.DataFrame, *, min_days: int = 7) -> RiskScoreResult:
    """Score every day of the user's history."""
    artifact = _load_artifact()
    features: list[str] = artifact["feature_columns"]
    scaler = artifact["scaler"]
    pca = artifact["pca"]
    n_components = artifact["n_components"]
    weights = artifact["weights"]
    sorted_training = artifact["sorted_training_raw"]

    if daily.empty:
        raise InsufficientHistory(have=0, need=min_days)

    work = daily.copy()
    work["record_date"] = pd.to_datetime(work["record_date"])
    work = work.sort_values("record_date").reset_index(drop=True)

    mapped = _personal_to_population_daily(work, features)
    usable = mapped.dropna()
    if len(usable) < min_days:
        # Distinguish "too few days" from "a measurement is missing".
        #
        # Both used to surface as the former. The first version of this check
        # only caught a column missing on EVERY day, which stopped firing as
        # soon as notification access was granted and one day carried a count:
        # 8 days stored, 7 needed, and the message still read "0 usable, 7
        # needed" with no hint that notifications were the cause.
        #
        # So the question asked here is the useful one - which measurements are
        # responsible for discarding rows, and on how many days - rather than
        # the narrow one of whether a column is empty throughout.
        if len(work) >= min_days:
            blocked = {
                column: int(mapped[column].isna().sum())
                for column in features
                if mapped[column].isna().any()
            }
            if blocked:
                raise MissingFeatures(
                    features=sorted(blocked, key=lambda c: -blocked[c]),
                    days=len(work),
                    blocked_rows=blocked,
                )
        raise InsufficientHistory(have=len(usable), need=min_days)

    X = scaler.transform(usable.to_numpy(dtype=float))
    projected = pca.transform(X)[:, :n_components]
    raw = projected @ weights

    scores = np.array([raw_to_score(v, sorted_training) for v in raw])

    dates = work.loc[usable.index, "record_date"]
    history = [
        ScoredDay(date=d.date().isoformat(), score=round(float(s), 1), band=band_for(float(s)))
        for d, s in zip(dates, scores)
    ]

    # Per-feature contribution for the most recent scored day. Computed as
    # the standardised feature value times its net loading across the
    # retained components, which is the feature's actual pull on the score.
    net_loading = pca.components_[:n_components].T @ weights
    last_scaled = X[-1]
    contributions = {
        f: round(float(last_scaled[i] * net_loading[i]), 4)
        for i, f in enumerate(features)
    }

    return RiskScoreResult(
        current_score=round(float(scores[-1]), 1),
        band=band_for(float(scores[-1])),
        contributions=contributions,
        history=history,
        days_scored=len(history),
    )


def score_with_confidence(daily: pd.DataFrame, *, min_days: int = 7) -> dict:
    """Score the history and attach a Bayesian posterior over the rate.

    The posterior is over the user's underlying rate of elevated-risk days,
    corrected for the autocorrelation between consecutive days.
    """
    result = score_history(daily, min_days=min_days)

    scores = np.array([d.score for d in result.history], dtype=float)
    elevated = int((scores >= ELEVATED_SCORE_THRESHOLD).sum())

    # Measure autocorrelation from the SCORE series itself, which is what the
    # Bernoulli trials are derived from - rather than reusing a figure
    # measured on a different variable.
    rho = bayesian.lag1_autocorrelation(scores)

    posterior = bayesian.estimate_rate(
        successes=elevated,
        trials=len(scores),
        autocorrelation=rho,
    )

    metrics = load_metrics() or {}
    validation = metrics.get("validation", {})

    return {
        "current_score": result.current_score,
        "band": result.band,
        "days_scored": result.days_scored,
        "score_history": [
            {"date": d.date, "score": d.score, "band": d.band} for d in result.history
        ],
        "contributions": result.contributions,
        "elevated_days": elevated,
        "elevated_threshold": ELEVATED_SCORE_THRESHOLD,
        "bayesian": {
            "estimated_rate": round(posterior.mean, 4),
            "credible_interval": [round(posterior.lower, 4), round(posterior.upper, 4)],
            "credible_mass": posterior.credible_mass,
            "interval_width": round(posterior.width, 4),
            "observed": f"{posterior.observed_successes}/{posterior.observed_trials}",
            "effective_trials": posterior.effective_trials,
            "autocorrelation": round(rho, 4) if rho is not None else None,
            "is_reliable": posterior.is_reliable,
            "explanation": posterior.explanation,
            "notes": posterior.notes,
            "prior": bayesian.describe_prior(),
            "posterior": f"Beta({posterior.posterior_alpha}, {posterior.posterior_beta})",
            "next_day": bayesian.posterior_predictive_next_day(posterior),
        },
        "score_method": {
            "scale": (
                "0-100 percentile rank against the training population: a score "
                "of 70 means this day's behaviour is more pronounced than 70% of "
                "the reference population."
            ),
            "bands": "Low below 50, Medium 50-69, High 70 and above",
            "components_retained": metrics.get("n_components_retained"),
            "validated_against_labels": validation.get("validated"),
            "spearman_rho": validation.get("spearman_rho"),
            "mean_score_per_class": validation.get("mean_score_per_class"),
        },
    }
