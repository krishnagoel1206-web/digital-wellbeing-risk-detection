"""Bayesian confidence layer for risk estimates.

WHAT IS BEING ESTIMATED, PRECISELY
----------------------------------
Not "how confident is the classifier". That number already exists and is just
a softmax output. The quantity estimated here is different and genuinely
uncertain:

    theta = this person's underlying rate of elevated-risk days

We observe k elevated days out of n recorded days. theta is unknown, and with
few days it is very poorly determined. A Bayesian treatment gives a full
posterior distribution over theta rather than a single point, so the app can
say "somewhere between 8% and 34%" instead of inventing a precise "19%".

THE MODEL
---------
Beta-Binomial conjugate pair:

    prior:      theta ~ Beta(a0, b0)
    likelihood: k ~ Binomial(n, theta)
    posterior:  theta ~ Beta(a0 + k, b0 + n - k)

The default prior is Jeffreys, Beta(0.5, 0.5). It is chosen because it is the
non-informative reference prior for a binomial proportion and, unlike
Beta(1,1), it does not pull the estimate toward 0.5 - which matters here
because elevated days are genuinely rare, so a uniform prior would overstate
the rate when n is small.

THE ASSUMPTION THAT IS VIOLATED, AND THE CORRECTION
---------------------------------------------------
The Binomial likelihood assumes the n days are INDEPENDENT. They are not.
Phase 5 measured the lag-1 autocorrelation of this user's screen time at
0.522: a heavy day is followed by a heavy day. Correlated observations carry
less information than independent ones, so treating 365 correlated days as
365 independent trials produces a credible interval that is too NARROW -
overstating certainty, which is the exact failure the brief warns against.

The standard correction is an effective sample size. For an AR(1) process,

    n_eff = n * (1 - rho) / (1 + rho)

At rho = 0.52 that is roughly 0.32n, so 365 days behave like about 115
independent observations. Both the naive and the corrected intervals are
reported, with the correction used as the headline figure, so the effect of
the assumption is visible rather than buried.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

#: Jeffreys prior for a binomial proportion.
DEFAULT_ALPHA = 0.5
DEFAULT_BETA = 0.5

#: Credible interval mass.
DEFAULT_CREDIBLE_MASS = 0.95

#: Below this effective sample size the estimate is too weak to present as a
#: finding, and the app is told to say so.
MIN_EFFECTIVE_N = 10.0


@dataclass
class PosteriorEstimate:
    """A posterior over a rate, with everything needed to report it honestly."""

    #: Posterior mean - the headline probability.
    mean: float
    #: Posterior mode, where defined.
    mode: float | None
    #: Equal-tailed credible interval.
    lower: float
    upper: float
    credible_mass: float
    #: Width of the interval; the plain measure of how much is unknown.
    width: float

    observed_successes: int
    observed_trials: int
    effective_trials: float
    autocorrelation: float | None

    prior_alpha: float
    prior_beta: float
    posterior_alpha: float
    posterior_beta: float

    #: True when there is too little effective data to report confidently.
    is_reliable: bool
    explanation: str = ""
    notes: list[str] = field(default_factory=list)


def effective_sample_size(n: int, rho: float | None) -> tuple[float, str]:
    """Shrink n to account for autocorrelation between consecutive days.

    Returns (n_eff, explanation). With rho None or <= 0 the observations are
    treated as independent and n is returned unchanged.
    """
    if rho is None or not np.isfinite(rho) or rho <= 0:
        return float(n), "observations treated as independent (no positive autocorrelation)"

    # Clamp below 1 to keep the factor finite.
    rho_c = float(min(rho, 0.95))
    factor = (1.0 - rho_c) / (1.0 + rho_c)
    n_eff = max(1.0, float(n) * factor)

    return n_eff, (
        f"n reduced from {n} to {n_eff:.1f} effective observations because "
        f"consecutive days correlate (rho = {rho_c:.3f}); correlated days "
        "carry less information than independent ones, so ignoring this would "
        "make the interval falsely narrow"
    )


def lag1_autocorrelation(series: np.ndarray) -> float | None:
    """Lag-1 autocorrelation of a series, or None when it cannot be computed."""
    clean = np.asarray(series, dtype=float)
    clean = clean[np.isfinite(clean)]
    if clean.size < 10:
        return None

    first, second = clean[:-1], clean[1:]
    if np.std(first) == 0 or np.std(second) == 0:
        return None

    rho = float(np.corrcoef(first, second)[0, 1])
    return rho if np.isfinite(rho) else None


def estimate_rate(
    *,
    successes: int,
    trials: int,
    autocorrelation: float | None = None,
    prior_alpha: float = DEFAULT_ALPHA,
    prior_beta: float = DEFAULT_BETA,
    credible_mass: float = DEFAULT_CREDIBLE_MASS,
) -> PosteriorEstimate:
    """Posterior over an underlying rate, given observed counts.

    `autocorrelation` triggers the effective-sample-size correction. The
    observed proportion k/n is preserved while n shrinks, so the posterior
    mean barely moves but the interval widens appropriately.
    """
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError(f"invalid counts: {successes} successes out of {trials} trials")

    notes: list[str] = []

    n_eff, ess_note = effective_sample_size(trials, autocorrelation)
    notes.append(ess_note)

    # Keep the observed proportion, scale the counts down to n_eff.
    proportion = (successes / trials) if trials > 0 else 0.0
    k_eff = proportion * n_eff

    post_alpha = prior_alpha + k_eff
    post_beta = prior_beta + (n_eff - k_eff)

    posterior = stats.beta(post_alpha, post_beta)

    tail = (1.0 - credible_mass) / 2.0
    lower = float(posterior.ppf(tail))
    upper = float(posterior.ppf(1.0 - tail))
    mean = float(posterior.mean())

    # The Beta mode exists only when both parameters exceed 1.
    mode: float | None = None
    if post_alpha > 1 and post_beta > 1:
        mode = float((post_alpha - 1) / (post_alpha + post_beta - 2))

    is_reliable = n_eff >= MIN_EFFECTIVE_N
    if not is_reliable:
        notes.append(
            f"effective sample size {n_eff:.1f} is below {MIN_EFFECTIVE_N:.0f}; "
            "this estimate is too weak to present as a finding"
        )

    width = upper - lower
    explanation = (
        f"Based on {successes} elevated day(s) out of {trials} recorded, the "
        f"most likely underlying rate is {mean:.1%}. There is a "
        f"{credible_mass:.0%} probability the true rate lies between "
        f"{lower:.1%} and {upper:.1%}."
    )
    if width > 0.30:
        explanation += (
            " That range is wide, which means there is not yet enough data to "
            "pin the rate down - it will narrow as more days are recorded."
        )

    return PosteriorEstimate(
        mean=mean,
        mode=mode,
        lower=lower,
        upper=upper,
        credible_mass=credible_mass,
        width=width,
        observed_successes=int(successes),
        observed_trials=int(trials),
        effective_trials=round(n_eff, 2),
        autocorrelation=autocorrelation,
        prior_alpha=prior_alpha,
        prior_beta=prior_beta,
        posterior_alpha=round(post_alpha, 4),
        posterior_beta=round(post_beta, 4),
        is_reliable=is_reliable,
        explanation=explanation,
        notes=notes,
    )


def posterior_predictive_next_day(estimate: PosteriorEstimate) -> dict:
    """Probability that the NEXT day is an elevated-risk day.

    For a Beta posterior and one Bernoulli trial the posterior predictive
    probability equals the posterior mean. It is exposed separately because
    "the chance tomorrow is elevated" and "your underlying rate" are different
    questions, and conflating them is a common error.
    """
    return {
        "probability": round(estimate.mean, 4),
        "derivation": (
            "For a Beta(a, b) posterior and a single Bernoulli trial, the "
            "posterior predictive probability is a / (a + b), i.e. the "
            "posterior mean."
        ),
        "interval_note": (
            "The credible interval describes the long-run RATE, not tomorrow. "
            "A single day is either elevated or not; only the rate has an "
            "interval."
        ),
    }


def describe_prior(prior_alpha: float = DEFAULT_ALPHA, prior_beta: float = DEFAULT_BETA) -> dict:
    """Document the prior, so it is never an unexplained constant."""
    name = "Jeffreys" if (prior_alpha == 0.5 and prior_beta == 0.5) else "custom"
    return {
        "distribution": f"Beta({prior_alpha}, {prior_beta})",
        "name": name,
        "prior_mean": prior_alpha / (prior_alpha + prior_beta),
        "equivalent_prior_observations": prior_alpha + prior_beta,
        "rationale": (
            "Jeffreys Beta(0.5, 0.5) is the non-informative reference prior for "
            "a binomial proportion. It is preferred over the uniform Beta(1, 1) "
            "here because elevated days are genuinely rare, and a uniform prior "
            "would pull a small-sample estimate toward 50%. It contributes the "
            "equivalent of only one prior observation, so it is quickly "
            "overwhelmed by real data."
        ),
    }
