"""Feature engineering - turning raw rows into model inputs.

Two separate paths, because the two data shapes mean different things:

  build_personal_features()    one row per DAY for a single user. Produces the
                               time-aware features (rolling windows, weekday)
                               that the forecasting and personal-baseline
                               modules need.

  build_population_features()  one row per PERSON, cross-sectional. No time
                               ordering exists, so no rolling features are
                               computed - inventing them would be fiction.

LEAKAGE RULES ENFORCED HERE
---------------------------
Rolling features use only PRECEDING days. `screen_time_7d_mean` for a given
date is the mean of the seven days BEFORE it, never including the day itself.
Including the current day would put the target inside its own predictor and
produce an impressively wrong model.

Scaling is not done here - see cleaning.py for why it belongs in the Pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class EngineeringReport:
    """What feature engineering produced, and what it had to correct."""

    rows: int
    features_created: list[str] = field(default_factory=list)
    #: column -> how many values had to be clipped into a valid range.
    clipped: dict[str, int] = field(default_factory=dict)
    #: column -> how many values are undefined (e.g. divide by zero).
    undefined: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _safe_ratio(
    numerator: pd.Series,
    denominator: pd.Series,
    report: EngineeringReport,
    name: str,
) -> pd.Series:
    """numerator / denominator, clipped to [0, 1], with both problems counted.

    Two separate failure modes are handled distinctly, because conflating them
    hides information:

    * Denominator is zero or missing -> the ratio is genuinely UNDEFINED.
      Returned as NaN, never as 0. A zero would assert "0% of screen time was
      social media" on a day with no screen time at all, which is a claim the
      data does not support.

    * Ratio exceeds 1.0 -> the source row is internally inconsistent. This is
      not hypothetical: 60.7% of the population dataset's rows have category
      hours summing above total screen time. The value is clipped to 1.0 and
      counted, so the extent of the problem is visible rather than buried.
    """
    denom = denominator.replace(0, np.nan).astype("float64")
    raw = numerator.astype("float64") / denom

    n_undefined = int(raw.isna().sum() - numerator.isna().sum().item()) if len(raw) else 0
    if n_undefined > 0:
        report.undefined[name] = n_undefined

    n_over = int((raw > 1.0).sum())
    if n_over:
        report.clipped[name] = n_over

    return raw.clip(lower=0.0, upper=1.0)


def build_personal_features(df: pd.DataFrame) -> tuple[pd.DataFrame, EngineeringReport]:
    """Derive features from this user's daily records.

    Expects the columns of UsageRecord, including `record_date` and
    `total_screen_minutes`. Rows are sorted by date first, because every
    rolling feature depends on correct ordering.
    """
    if df.empty:
        return df.copy(), EngineeringReport(rows=0, notes=["no records to process"])

    if "record_date" not in df.columns:
        raise ValueError("build_personal_features requires a 'record_date' column")

    out = df.copy()
    out["record_date"] = pd.to_datetime(out["record_date"])
    out = out.sort_values("record_date").reset_index(drop=True)

    report = EngineeringReport(rows=len(out))
    total = out["total_screen_minutes"].astype("float64")

    # --- unit conversion -------------------------------------------------
    out["total_screen_hours"] = total / 60.0
    report.features_created.append("total_screen_hours")

    # --- composition ratios ----------------------------------------------
    for src, name in (
        ("social_minutes", "social_ratio"),
        ("gaming_minutes", "gaming_ratio"),
        ("productivity_minutes", "productivity_ratio"),
        ("late_night_minutes", "late_night_ratio"),
    ):
        if src in out.columns:
            out[name] = _safe_ratio(out[src], total, report, name)
            report.features_created.append(name)

    # --- intensity -------------------------------------------------------
    hours = out["total_screen_hours"].replace(0, np.nan)

    if "notification_count" in out.columns:
        out["notifications_per_hour"] = out["notification_count"].astype("float64") / hours
        report.features_created.append("notifications_per_hour")

    if "app_launches" in out.columns:
        launches = out["app_launches"].astype("float64")
        out["launches_per_hour"] = launches / hours
        out["avg_session_minutes"] = total / launches.replace(0, np.nan)
        report.features_created.extend(["launches_per_hour", "avg_session_minutes"])

    # --- calendar --------------------------------------------------------
    out["day_of_week"] = out["record_date"].dt.day_of_week
    out["is_weekend"] = (out["day_of_week"] >= 5).astype(int)
    report.features_created.extend(["day_of_week", "is_weekend"])

    # --- rolling windows: PRECEDING days only ----------------------------
    # shift(1) is the leakage guard. Without it, the current day's value is
    # inside its own 7-day mean.
    preceding = total.shift(1)
    out["screen_time_7d_mean"] = preceding.rolling(window=7, min_periods=1).mean()
    out["screen_time_7d_std"] = preceding.rolling(window=7, min_periods=2).std()
    report.features_created.extend(["screen_time_7d_mean", "screen_time_7d_std"])
    report.notes.append(
        "rolling features use shift(1) so they describe only PRECEDING days - "
        "without this the target would leak into its own predictor"
    )

    n_no_history = int(out["screen_time_7d_mean"].isna().sum())
    if n_no_history:
        report.undefined["screen_time_7d_mean"] = n_no_history
        report.notes.append(
            f"{n_no_history} row(s) have no preceding day, so rolling features "
            "are missing there (expected for the earliest records)"
        )

    return out, report


def build_population_features(
    df: pd.DataFrame,
    *,
    hours_columns: tuple[str, ...] = (
        "social_media_hours",
        "gaming_hours",
        "work_study_hours",
    ),
    total_column: str = "daily_screen_time_hours",
) -> tuple[pd.DataFrame, EngineeringReport]:
    """Derive composition ratios for the cross-sectional population dataset.

    No rolling or calendar features are produced: this dataset holds one row
    per person with no dates, so there is no time axis to roll along. The
    profiling confirmed 7,500 distinct user_ids across 7,500 rows.
    """
    if df.empty:
        return df.copy(), EngineeringReport(rows=0, notes=["no rows to process"])

    out = df.copy()
    report = EngineeringReport(rows=len(out))

    if total_column not in out.columns:
        raise ValueError(f"expected total column '{total_column}' is absent")

    total = out[total_column].astype("float64")

    for col in hours_columns:
        if col not in out.columns:
            report.notes.append(f"column '{col}' absent - ratio not computed")
            continue
        name = col.replace("_hours", "_ratio")
        out[name] = _safe_ratio(out[col], total, report, name)
        report.features_created.append(name)

    # Make the dataset's internal inconsistency explicit and measurable
    # rather than leaving it implicit in the clipping counts.
    present = [c for c in hours_columns if c in out.columns]
    if present:
        component_sum = out[present].astype("float64").sum(axis=1)
        out["component_hours_sum"] = component_sum
        out["components_exceed_total"] = (component_sum > total).astype(int)
        report.features_created.extend(["component_hours_sum", "components_exceed_total"])

        n_exceed = int(out["components_exceed_total"].sum())
        pct = 100.0 * n_exceed / len(out)
        report.notes.append(
            f"{n_exceed} of {len(out)} rows ({pct:.1f}%) have category hours "
            f"summing above {total_column} - the categories were sampled "
            "independently in this synthetic dataset, so ratios are clipped"
        )

    return out, report
