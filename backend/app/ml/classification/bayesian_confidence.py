"""Bayesian confidence in the risk classification.

The classifier answers "which class does your average day fall in?" with a
single probability vector. That says nothing about how STABLE the answer is:
a 53% Medium could come from weeks that are all borderline, or from weeks
that swing between Low and High. This module measures that.

METHOD
------
1. Split the finished history into non-overlapping 7-day windows, newest
   first. A week is the smallest unit comparable with what the classifier was
   trained on (a person's typical day), and it averages out weekday/weekend
   swings. Windows with fewer than MIN_DAYS_PER_WEEK records are skipped.
2. Classify each week with the deployed classifier and keep its full
   probability vector - "soft" evidence, so a 51/49 week counts as roughly
   half a week for each class instead of a whole week for one.
3. Treat theta = (share of weeks that are Low, Medium, High) as unknown with a
   uniform Dirichlet(1, 1, 1) prior. Soft counts are conjugate, so the
   posterior is Dirichlet(1 + sum of probabilities per class).
4. Report the posterior mean share per class with a 90% credible interval
   (each marginal is a Beta), and by Monte Carlo:
     - P(each class is your most common class)
     - P(Medium + High weeks are the majority)  -> "elevated risk"

LIMITS, stated in the response
------------------------------
* Adjacent weeks are not fully independent, so the intervals are somewhat
  narrower than they should be. Counting weeks rather than days already
  reduces this a lot (daily usage is strongly autocorrelated; weekly
  averages much less so).
* The classifier itself is ~60% accurate against a 38% baseline. This
  posterior expresses how consistent ITS answers are across your weeks, not
  extra accuracy on top of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from app.ml.classification import inference as clf

DAYS_PER_WEEK = 7
MIN_DAYS_PER_WEEK = 5
DEFAULT_MAX_WEEKS = 12
MIN_WEEKS = 2
PRIOR = 1.0
CREDIBLE_MASS = 0.90
MC_DRAWS = 20_000


class InsufficientWeeks(Exception):
    def __init__(self, have: int, need: int, missing: list[str] | None = None) -> None:
        self.have = have
        self.need = need
        #: Measurements whose absence made otherwise-complete weeks unusable.
        self.missing = missing or []
        super().__init__(f"{have} usable week(s), {need} needed")


@dataclass
class WeekResult:
    start: str
    end: str
    days: int
    predicted: str
    probabilities: dict[str, float]
    synthetic_days: int = 0


@dataclass
class RiskConfidence:
    classes: list[str]
    weeks: list[WeekResult]
    posterior_alpha: dict[str, float]
    share_mean: dict[str, float]
    share_interval: dict[str, tuple[float, float]]
    prob_most_common: dict[str, float]
    prob_elevated_majority: float
    most_likely_class: str
    verdict: str
    plain_language: str
    caveats: list[str] = field(default_factory=list)


def weekly_windows(
    daily: pd.DataFrame, *, max_weeks: int = DEFAULT_MAX_WEEKS
) -> list[pd.DataFrame]:
    """Consecutive 7-calendar-day windows ending at the newest record, newest first."""
    if daily.empty:
        return []
    df = daily.copy()
    df["record_date"] = pd.to_datetime(df["record_date"])
    end = df["record_date"].max()
    windows: list[pd.DataFrame] = []
    for i in range(max_weeks):
        w_end = end - pd.Timedelta(days=DAYS_PER_WEEK * i)
        w_start = w_end - pd.Timedelta(days=DAYS_PER_WEEK - 1)
        if w_end < df["record_date"].min():
            break
        rows = df[(df["record_date"] >= w_start) & (df["record_date"] <= w_end)]
        if len(rows) >= MIN_DAYS_PER_WEEK:
            windows.append(rows)
    return windows


def _verdict(p_top: float) -> str:
    if p_top >= 0.9:
        return "very sure"
    if p_top >= 0.75:
        return "fairly sure"
    if p_top >= 0.55:
        return "leaning"
    return "uncertain"


def estimate(
    daily: pd.DataFrame,
    *,
    max_weeks: int = DEFAULT_MAX_WEEKS,
    seed: int = 0,
) -> RiskConfidence:
    windows = weekly_windows(daily, max_weeks=max_weeks)

    weeks: list[WeekResult] = []
    skipped_missing: set[str] = set()
    for w in windows:
        try:
            pred = clf.predict_from_personal_history(
                w.drop(columns=["source"], errors="ignore"), min_days=MIN_DAYS_PER_WEEK
            )
        except clf.MissingFeatures as exc:
            # e.g. a week with no sleep estimate - skipped, never guessed.
            skipped_missing.update(exc.features)
            continue
        except clf.InsufficientHistory:
            continue
        synthetic = int((w["source"] == "synthetic").sum()) if "source" in w.columns else 0
        weeks.append(
            WeekResult(
                start=w["record_date"].min().date().isoformat(),
                end=w["record_date"].max().date().isoformat(),
                days=len(w),
                predicted=pred.risk_class,
                probabilities={k: round(v, 4) for k, v in pred.probabilities.items()},
                synthetic_days=synthetic,
            )
        )

    if len(weeks) < MIN_WEEKS:
        raise InsufficientWeeks(have=len(weeks), need=MIN_WEEKS, missing=sorted(skipped_missing))

    classes = list(weeks[0].probabilities.keys())
    soft = np.array([[wk.probabilities.get(c, 0.0) for c in classes] for wk in weeks]).sum(axis=0)
    alpha = PRIOR + soft
    a0 = float(alpha.sum())

    tail = (1 - CREDIBLE_MASS) / 2
    share_mean = {c: float(alpha[i] / a0) for i, c in enumerate(classes)}
    share_interval = {
        c: (
            float(stats.beta.ppf(tail, alpha[i], a0 - alpha[i])),
            float(stats.beta.ppf(1 - tail, alpha[i], a0 - alpha[i])),
        )
        for i, c in enumerate(classes)
    }

    rng = np.random.default_rng(seed)
    draws = rng.dirichlet(alpha, size=MC_DRAWS)
    winners = draws.argmax(axis=1)
    prob_most_common = {c: float((winners == i).mean()) for i, c in enumerate(classes)}
    elevated_idx = [i for i, c in enumerate(classes) if c in ("Medium", "High")]
    prob_elevated = float((draws[:, elevated_idx].sum(axis=1) > 0.5).mean())

    top = max(prob_most_common, key=prob_most_common.get)
    verdict = _verdict(prob_most_common[top])
    n = len(weeks)
    plain = (
        f"Across your last {n} weeks, {top} is the most likely description of a typical week "
        f"({prob_most_common[top]:.0%} posterior probability) - {verdict}. "
        f"There is a {prob_elevated:.0%} probability that most of your weeks are Medium or High risk."
    )

    caveats = [
        "Each week is classified separately, then combined with a uniform Dirichlet prior. "
        "With few weeks the prior still matters and intervals are wide.",
        "Neighbouring weeks are not fully independent, so the true uncertainty is somewhat "
        "larger than shown.",
        "This measures how consistent the classifier's answers are across your weeks. It does "
        "not make the classifier itself more accurate (about 60% on held-out people).",
    ]
    synthetic_weeks = sum(1 for w in weeks if w.synthetic_days > 0)
    if synthetic_weeks:
        caveats.insert(
            0,
            f"{synthetic_weeks} of the {n} weeks include demo (synthetic) days, so this is "
            "partly about generated data rather than your real usage.",
        )

    return RiskConfidence(
        classes=classes,
        weeks=weeks,
        posterior_alpha={c: round(float(alpha[i]), 4) for i, c in enumerate(classes)},
        share_mean=share_mean,
        share_interval=share_interval,
        prob_most_common=prob_most_common,
        prob_elevated_majority=prob_elevated,
        most_likely_class=top,
        verdict=verdict,
        plain_language=plain,
        caveats=caveats,
    )
