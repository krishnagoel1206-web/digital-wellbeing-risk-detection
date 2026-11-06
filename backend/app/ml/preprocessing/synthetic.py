"""Synthetic daily-history generator.

WHY THIS IS NECESSARY, NOT A SHORTCUT
-------------------------------------
Profiling proved both population datasets are cross-sectional: 7,500 distinct
user_ids across 7,500 rows, and 705 distinct students. Neither contains a
single user observed over multiple days.

But two of the required modules need exactly that:
  * forecasting the next days' screen time (needs a time series), and
  * personal-baseline anomaly detection (needs "this user's normal").

So this generator produces a plausible daily history for one person. Every
row it creates is tagged `source=SYNTHETIC` in the database, and the API
summary exposes `contains_synthetic_data`, so the app can always tell the
user which numbers rest on demonstration data.

GROUND TRUTH IS THE POINT
-------------------------
The generator deliberately INJECTS anomalies on known dates and returns a
manifest of them. That turns Phase 6 from a demo into a measurable test: we
can ask the detector how many injected anomalies it found and how many days
it flagged that were in fact normal. Without ground truth, an anomaly
detector can only be eyeballed.

Everything is driven by a seeded RNG, so a given seed reproduces exactly the
same history - the brief requires reproducible results.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass(frozen=True)
class BehaviourProfile:
    """The 'true' behaviour this synthetic person has.

    Values are daily means in minutes unless stated. Weekday/weekend effects
    are multiplicative so weekends scale the whole day realistically.
    """

    name: str
    screen_minutes_mean: float
    screen_minutes_sd: float
    social_share: float
    gaming_share: float
    productivity_share: float
    late_night_share: float
    notifications_mean: float
    launches_mean: float
    sleep_hours_mean: float
    weekend_multiplier: float = 1.25
    #: Day-to-day persistence of screen time, as an AR(1) coefficient.
    #:
    #: WHY THIS EXISTS: the first version of this generator drew every day
    #: independently. That is unrealistic - habits carry over, so a heavy day
    #: tends to follow a heavy day - and it also made next-day forecasting
    #: impossible in principle: with i.i.d. days, lag features contain no
    #: information, and the regression module could only ever learn the
    #: weekend effect. Training confirmed exactly that (all lag coefficients
    #: near zero, no model beating naive persistence).
    #:
    #: 0.55 gives noticeable but not overwhelming momentum.
    autocorrelation: float = 0.55
    #: Gentle multiplicative drift across the whole period, so there is also
    #: a slow trend to detect (e.g. 0.15 = usage rises ~15% end to end).
    trend_strength: float = 0.15


#: A few documented personas. Names describe behaviour, not judgement.
PROFILES: dict[str, BehaviourProfile] = {
    "balanced": BehaviourProfile(
        name="balanced",
        screen_minutes_mean=270,
        screen_minutes_sd=45,
        social_share=0.28,
        gaming_share=0.12,
        productivity_share=0.35,
        late_night_share=0.05,
        notifications_mean=120,
        launches_mean=85,
        sleep_hours_mean=7.4,
    ),
    "social_heavy": BehaviourProfile(
        name="social_heavy",
        screen_minutes_mean=430,
        screen_minutes_sd=70,
        social_share=0.55,
        gaming_share=0.08,
        productivity_share=0.15,
        late_night_share=0.14,
        notifications_mean=320,
        launches_mean=180,
        sleep_hours_mean=6.2,
    ),
    "late_night_scroller": BehaviourProfile(
        name="late_night_scroller",
        screen_minutes_mean=380,
        screen_minutes_sd=60,
        social_share=0.45,
        gaming_share=0.10,
        productivity_share=0.18,
        late_night_share=0.30,
        notifications_mean=240,
        launches_mean=150,
        sleep_hours_mean=5.6,
    ),
}


@dataclass
class InjectedAnomaly:
    """One deliberately abnormal day, recorded so detection can be scored."""

    date: str
    kind: str
    description: str
    #: Which features were pushed away from the person's normal.
    features_affected: list[str] = field(default_factory=list)


@dataclass
class GenerationResult:
    """The generated rows plus the ground truth about them."""

    records: list[dict]
    profile: dict
    anomalies: list[dict]
    seed: int
    notes: list[str] = field(default_factory=list)


def _clip_int(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def generate_history(
    *,
    days: int = 60,
    profile_name: str = "balanced",
    end_date: dt.date | None = None,
    seed: int = 42,
    inject_anomalies: bool = True,
) -> GenerationResult:
    """Generate `days` of daily usage ending at `end_date` (default: yesterday).

    Returns dicts shaped for UsageRecordCreate, so they can be posted straight
    to the API or handed to the service layer.
    """
    if days < 1:
        raise ValueError("days must be >= 1")
    if profile_name not in PROFILES:
        raise ValueError(f"unknown profile {profile_name!r}; choose from {sorted(PROFILES)}")

    profile = PROFILES[profile_name]
    rng = np.random.default_rng(seed)

    # Default to yesterday: the API rejects future dates, and today's usage is
    # not finished yet so recording it would be misleading.
    end = end_date or (dt.date.today() - dt.timedelta(days=1))
    start = end - dt.timedelta(days=days - 1)

    # Choose anomaly days from the second half only, so there is enough
    # preceding history for a baseline to exist when they occur.
    anomaly_dates: dict[dt.date, str] = {}
    if inject_anomalies and days >= 20:
        earliest = days // 2
        candidates = [start + dt.timedelta(days=int(i)) for i in range(earliest, days)]
        kinds = ["late_night_spike", "notification_flood", "social_binge"]
        chosen = rng.choice(len(candidates), size=min(3, len(candidates)), replace=False)
        for kind, idx in zip(kinds, sorted(chosen.tolist())):
            anomaly_dates[candidates[idx]] = kind

    records: list[dict] = []
    injected: list[InjectedAnomaly] = []

    # AR(1) state: the carried-over deviation from this person's usual level.
    # Scaled so the resulting series has roughly the profile's stated standard
    # deviation: for an AR(1) process, sd(series) = sd(innovation)/sqrt(1-rho^2),
    # so the innovation is shrunk by sqrt(1-rho^2) to compensate.
    rho = float(np.clip(profile.autocorrelation, 0.0, 0.95))
    innovation_sd = profile.screen_minutes_sd * float(np.sqrt(1.0 - rho**2))
    deviation = rng.normal(0.0, profile.screen_minutes_sd)

    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        is_weekend = day.weekday() >= 5
        multiplier = profile.weekend_multiplier if is_weekend else 1.0

        # Slow drift across the period, so a trend exists to be detected.
        progress = offset / max(1, days - 1)
        trend = 1.0 + profile.trend_strength * (progress - 0.5)

        # Carry yesterday's deviation forward, then add fresh noise.
        deviation = rho * deviation + rng.normal(0.0, innovation_sd)
        screen = profile.screen_minutes_mean * multiplier * trend + deviation
        social_share = profile.social_share
        late_share = profile.late_night_share
        notifications = rng.normal(profile.notifications_mean, profile.notifications_mean * 0.18)
        launches = rng.normal(profile.launches_mean, profile.launches_mean * 0.20)
        sleep = rng.normal(profile.sleep_hours_mean, 0.6)

        kind = anomaly_dates.get(day)
        if kind == "late_night_spike":
            # Same total screen time, but far more of it after 11pm. This is
            # the case a simple screen-time threshold would completely miss,
            # which is the whole argument for multivariate baselines.
            late_share = min(0.75, profile.late_night_share * 3.5 + 0.2)
            sleep = max(2.5, sleep - 2.5)
            injected.append(
                InjectedAnomaly(
                    date=day.isoformat(),
                    kind=kind,
                    description=(
                        "Late-night share tripled with total screen time unchanged, "
                        "and sleep reduced."
                    ),
                    features_affected=["late_night_ratio", "sleep_hours"],
                )
            )
        elif kind == "notification_flood":
            notifications *= 3.2
            launches *= 1.9
            injected.append(
                InjectedAnomaly(
                    date=day.isoformat(),
                    kind=kind,
                    description="Notification volume and app opens far above normal.",
                    features_affected=["notification_count", "app_launches"],
                )
            )
        elif kind == "social_binge":
            screen *= 1.75
            social_share = min(0.85, profile.social_share * 1.7)
            injected.append(
                InjectedAnomaly(
                    date=day.isoformat(),
                    kind=kind,
                    description="Total screen time and the social media share both well above normal.",
                    features_affected=["total_screen_minutes", "social_ratio"],
                )
            )

        total = _clip_int(screen, 15, 1200)

        # Shares are drawn with noise then normalised so the three categories
        # can never exceed the total - the exact defect found in the
        # population dataset, avoided here by construction.
        raw_shares = np.array(
            [
                max(0.01, social_share * rng.normal(1.0, 0.12)),
                max(0.0, profile.gaming_share * rng.normal(1.0, 0.25)),
                max(0.01, profile.productivity_share * rng.normal(1.0, 0.18)),
            ]
        )
        share_sum = raw_shares.sum()
        # Leave 8-20% of the day as "other" apps, so the categories sum to
        # less than the total, as they must in reality.
        usable = rng.uniform(0.80, 0.92)
        shares = raw_shares / share_sum * usable

        social = _clip_int(total * shares[0], 0, total)
        gaming = _clip_int(total * shares[1], 0, total)
        productivity = _clip_int(total * shares[2], 0, total)

        # Guard against rounding pushing the sum over the total.
        overflow = social + gaming + productivity - total
        if overflow > 0:
            productivity = max(0, productivity - overflow)

        late_night = _clip_int(total * min(0.95, max(0.0, late_share * rng.normal(1.0, 0.2))), 0, total)

        records.append(
            {
                "record_date": day.isoformat(),
                "total_screen_minutes": total,
                "social_minutes": social,
                "gaming_minutes": gaming,
                "productivity_minutes": productivity,
                "app_launches": _clip_int(launches, 1, 2000),
                "notification_count": _clip_int(notifications, 0, 5000),
                "unlock_count": _clip_int(launches * rng.normal(1.35, 0.1), 1, 3000),
                "late_night_minutes": late_night,
                "sleep_hours": round(float(max(0.5, min(14.0, sleep))), 1),
                "source": "synthetic",
                "note": f"generated: {profile.name}" + (f" / {kind}" if kind else ""),
            }
        )

    return GenerationResult(
        records=records,
        profile=asdict(profile),
        anomalies=[asdict(a) for a in injected],
        seed=seed,
        notes=[
            f"{days} day(s) generated for profile '{profile.name}' with seed {seed}",
            f"date range {start.isoformat()} to {end.isoformat()}",
            f"{len(injected)} anomaly day(s) injected as ground truth",
            f"screen time follows an AR(1) process (rho={rho:.2f}) with a "
            f"{profile.trend_strength:+.0%} drift across the period, so "
            "day-to-day momentum and a slow trend both exist - an earlier "
            "version drew days independently, which made next-day forecasting "
            "impossible in principle",
            "category minutes are normalised to sum BELOW the daily total, so "
            "these rows satisfy the API's validation rules",
            "ALL rows are tagged source=synthetic - results derived from them "
            "must not be presented as validated real-world performance",
        ],
    )
