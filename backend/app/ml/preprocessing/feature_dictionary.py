"""THE FEATURE DICTIONARY.

A single authoritative description of every feature the system uses: what it
means, its unit, where it comes from, its valid range, which ML modules
consume it, and any caveat discovered while profiling the real data.

It is written as code rather than as a static document for two reasons:
  1. The API can serve it, so the app can explain any number it displays in
     plain language without duplicating the wording.
  2. It cannot silently drift away from the implementation the way a separate
     markdown file would.

PROVENANCE OF THE CAVEATS
------------------------
Every "caveat" string below records something measured from the actual files
by scripts/profile_datasets.py - not an assumption. Where a feature is
useless for a particular purpose, that is stated rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FeatureOrigin(str, Enum):
    """Where a feature's value comes from."""

    #: Stored directly on a UsageRecord, as entered or imported.
    RAW = "raw"
    #: Computed from raw fields at feature-engineering time.
    DERIVED = "derived"
    #: A column of a population dataset, used only for training.
    DATASET = "dataset"


class MLModule(str, Enum):
    """Which part of the pipeline consumes a feature."""

    REGRESSION = "regression"
    CLASSIFICATION = "classification"
    CLUSTERING = "clustering"
    ANOMALY = "anomaly_detection"
    RISK_SCORE = "risk_scoring"
    RECOMMENDATION = "recommendations"


@dataclass(frozen=True)
class FeatureSpec:
    """Documentation for one feature."""

    key: str
    #: Short, non-technical name shown in the app.
    label: str
    #: Plain-language meaning, written for a normal user.
    description: str
    unit: str
    origin: FeatureOrigin
    #: Inclusive bounds, or None where unbounded.
    valid_min: float | None = None
    valid_max: float | None = None
    used_by: tuple[MLModule, ...] = ()
    #: How this value is computed, when derived.
    formula: str | None = None
    #: Anything measured about the real data that a reader must know.
    caveat: str | None = None


# ---------------------------------------------------------------------------
# 1. RAW PERSONAL FIELDS - one row per day, stored in SQLite.
# ---------------------------------------------------------------------------

PERSONAL_RAW: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="total_screen_minutes",
        label="Screen time",
        description="Total minutes the screen was on and being used during the day.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(
            MLModule.REGRESSION,
            MLModule.CLASSIFICATION,
            MLModule.CLUSTERING,
            MLModule.ANOMALY,
            MLModule.RISK_SCORE,
        ),
    ),
    FeatureSpec(
        key="social_minutes",
        label="Social media",
        description="Minutes spent in social media apps.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
    ),
    FeatureSpec(
        key="gaming_minutes",
        label="Games",
        description="Minutes spent in games.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="productivity_minutes",
        label="Work and study",
        description="Minutes spent in work, study or productivity apps.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="app_launches",
        label="App opens",
        description="How many times an app was opened during the day.",
        unit="count",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=10_000,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
    ),
    FeatureSpec(
        key="notification_count",
        label="Notifications",
        description="How many notifications arrived during the day.",
        unit="count",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=10_000,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
        caveat=(
            "In the population dataset this column has ZERO mutual information "
            "with the risk label (measured 0.0000), so it cannot help the "
            "classifier. It is still genuinely useful for describing behaviour, "
            "so it is kept for clustering, anomaly detection and the risk score."
        ),
    ),
    FeatureSpec(
        key="unlock_count",
        label="Phone pickups",
        description="How many times the phone was unlocked.",
        unit="count",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=10_000,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
        caveat="Not present in either population dataset; available only from manual entry.",
    ),
    FeatureSpec(
        key="calls_count",
        label="Calls",
        description="Number of phone calls made or received.",
        unit="count",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=500,
        used_by=(MLModule.CLUSTERING,),
        caveat="Not present in either population dataset; available only from manual entry.",
    ),
    FeatureSpec(
        key="call_duration_minutes",
        label="Time on calls",
        description="Total minutes spent on calls.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(MLModule.CLUSTERING,),
        caveat="Not present in either population dataset; available only from manual entry.",
    ),
    FeatureSpec(
        key="battery_drain_percent",
        label="Battery used",
        description="Percentage of battery consumed over the day.",
        unit="percent",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=100,
        used_by=(MLModule.CLUSTERING,),
        caveat="Not present in either population dataset; available only from manual entry.",
    ),
    FeatureSpec(
        key="late_night_minutes",
        label="Late-night use",
        description="Minutes of phone use between 11pm and 6am.",
        unit="minutes",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=1440,
        used_by=(MLModule.ANOMALY, MLModule.RISK_SCORE, MLModule.RECOMMENDATION),
        caveat=(
            "Neither population dataset records a late-night figure. It is "
            "therefore a personal-only feature: used for the individual's "
            "baseline and risk score, but it cannot be learned from the "
            "population data."
        ),
    ),
    FeatureSpec(
        key="sleep_hours",
        label="Sleep",
        description="Hours slept that night.",
        unit="hours",
        origin=FeatureOrigin.RAW,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
        caveat=(
            "Measured mutual information with the population risk label is "
            "0.0039 - effectively none. Retained as a behavioural descriptor, "
            "not as a predictor."
        ),
    ),
)


# ---------------------------------------------------------------------------
# 2. DERIVED FEATURES - computed from the raw fields above.
# ---------------------------------------------------------------------------

PERSONAL_DERIVED: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="total_screen_hours",
        label="Screen time (hours)",
        description="Screen time expressed in hours, for display and for "
        "comparison with the population datasets which use hours.",
        unit="hours",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=24,
        formula="total_screen_minutes / 60",
        used_by=(MLModule.REGRESSION, MLModule.CLASSIFICATION),
    ),
    FeatureSpec(
        key="social_ratio",
        label="Share on social media",
        description="What proportion of screen time went to social media.",
        unit="ratio 0-1",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=1,
        formula="social_minutes / total_screen_minutes, clipped to [0, 1]",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
        caveat=(
            "CLIPPING IS REQUIRED, not cosmetic. In the population dataset "
            "60.7% of rows have social+gaming+work exceeding total screen "
            "time (component mean 8.53h vs total mean 7.50h), because each "
            "category was sampled independently. Unclipped ratios would "
            "exceed 1.0 and silently corrupt every downstream distance "
            "calculation. Rows needing clipping are counted and reported."
        ),
    ),
    FeatureSpec(
        key="gaming_ratio",
        label="Share on games",
        description="What proportion of screen time went to games.",
        unit="ratio 0-1",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=1,
        formula="gaming_minutes / total_screen_minutes, clipped to [0, 1]",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="productivity_ratio",
        label="Share on work and study",
        description="What proportion of screen time went to productive apps.",
        unit="ratio 0-1",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=1,
        formula="productivity_minutes / total_screen_minutes, clipped to [0, 1]",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="late_night_ratio",
        label="Share late at night",
        description="What proportion of your screen time happened between "
        "11pm and 6am.",
        unit="ratio 0-1",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=1,
        formula="late_night_minutes / total_screen_minutes, clipped to [0, 1]",
        used_by=(MLModule.ANOMALY, MLModule.RISK_SCORE, MLModule.RECOMMENDATION),
    ),
    FeatureSpec(
        key="notifications_per_hour",
        label="Notifications per hour",
        description="How often notifications arrived, relative to how long "
        "the phone was actually in use.",
        unit="count per hour",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        formula="notification_count / total_screen_hours (None when screen time is 0)",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
        caveat="Undefined on a zero-screen-time day; returned as missing rather than 0.",
    ),
    FeatureSpec(
        key="launches_per_hour",
        label="App opens per hour",
        description="How frequently apps were opened while using the phone. "
        "A high value suggests restless checking.",
        unit="count per hour",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        formula="app_launches / total_screen_hours (None when screen time is 0)",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY, MLModule.RISK_SCORE),
    ),
    FeatureSpec(
        key="avg_session_minutes",
        label="Average session length",
        description="Roughly how long each time you picked up the phone lasted.",
        unit="minutes",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        formula="total_screen_minutes / app_launches (None when launches is 0)",
        used_by=(MLModule.CLUSTERING, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="is_weekend",
        label="Weekend",
        description="Whether the day was a Saturday or Sunday. Usage normally "
        "differs at weekends, so the baseline accounts for it.",
        unit="0 or 1",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=1,
        formula="record_date.weekday() >= 5",
        used_by=(MLModule.REGRESSION, MLModule.ANOMALY),
    ),
    FeatureSpec(
        key="day_of_week",
        label="Day of week",
        description="Monday = 0 through Sunday = 6.",
        unit="0-6",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        valid_max=6,
        formula="record_date.weekday()",
        used_by=(MLModule.REGRESSION,),
    ),
    FeatureSpec(
        key="screen_time_7d_mean",
        label="7-day average",
        description="Average screen time over the previous seven days.",
        unit="minutes",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        formula="rolling mean of total_screen_minutes over the 7 PRECEDING days",
        used_by=(MLModule.REGRESSION, MLModule.ANOMALY),
        caveat=(
            "Uses only PRECEDING days and excludes the current day. Including "
            "the current day would leak the target into its own predictor."
        ),
    ),
    FeatureSpec(
        key="screen_time_7d_std",
        label="7-day variability",
        description="How much your screen time has been varying recently.",
        unit="minutes",
        origin=FeatureOrigin.DERIVED,
        valid_min=0,
        formula="rolling standard deviation over the 7 PRECEDING days",
        used_by=(MLModule.ANOMALY,),
        caveat="Needs at least 2 preceding days; missing before that.",
    ),
)


# ---------------------------------------------------------------------------
# 3. POPULATION DATASET COLUMNS - training data only.
# ---------------------------------------------------------------------------

POPULATION_FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="daily_screen_time_hours",
        label="Daily screen time",
        description="Hours of screen time per day (population dataset).",
        unit="hours",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLASSIFICATION, MLModule.REGRESSION, MLModule.RECOMMENDATION),
        caveat="Observed range exactly 3.00-12.00 with zero IQR outliers - a sign "
        "the data is synthetic. Strongest single predictor (mutual information 0.2565).",
    ),
    FeatureSpec(
        key="social_media_hours",
        label="Social media hours",
        description="Hours per day in social media apps (population dataset).",
        unit="hours",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLASSIFICATION, MLModule.RECOMMENDATION),
        caveat="Mutual information with the label 0.1680 - the third most useful feature.",
    ),
    FeatureSpec(
        key="weekend_screen_time",
        label="Weekend screen time",
        description="Hours of screen time on weekend days (population dataset).",
        unit="hours",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLASSIFICATION,),
        caveat=(
            "Correlates 0.964 with daily_screen_time_hours (ratio mean 1.268, "
            "sd 0.163), i.e. it is essentially the same column rescaled. This "
            "severe multicollinearity is precisely why Ridge and Lasso are "
            "expected to beat plain Linear Regression here."
        ),
    ),
    FeatureSpec(
        key="gaming_hours",
        label="Gaming hours",
        description="Hours per day spent gaming (population dataset).",
        unit="hours",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLUSTERING,),
        caveat="Mutual information with the label measured 0.0000 - carries no "
        "information about risk in this dataset. Excluded from the classifier.",
    ),
    FeatureSpec(
        key="work_study_hours",
        label="Work/study hours",
        description="Hours per day in work or study apps (population dataset).",
        unit="hours",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        valid_max=24,
        used_by=(MLModule.CLUSTERING,),
    ),
    FeatureSpec(
        key="notifications_per_day",
        label="Notifications per day",
        description="Notifications received per day (population dataset).",
        unit="count",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        used_by=(MLModule.CLUSTERING,),
        caveat="Mutual information 0.0000 - no relationship to the label here.",
    ),
    FeatureSpec(
        key="app_opens_per_day",
        label="App opens per day",
        description="App launches per day (population dataset).",
        unit="count",
        origin=FeatureOrigin.DATASET,
        valid_min=0,
        used_by=(MLModule.CLUSTERING,),
        caveat="Mutual information 0.0035 - negligible.",
    ),
)


# ---------------------------------------------------------------------------
# 4. TARGETS
# ---------------------------------------------------------------------------

TARGETS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        key="risk_class",
        label="Risk category",
        description="Low, Medium or High behavioural risk.",
        unit="category",
        origin=FeatureOrigin.DERIVED,
        used_by=(MLModule.CLASSIFICATION,),
        formula=(
            "primary dataset: addiction_level mapped None/Mild->Low, "
            "Moderate->Medium, Severe->High. "
            "students dataset: Addicted_Score binned 1-4 Low, 5-7 Medium, 8-10 High."
        ),
        caveat=(
            "This label is NOT fabricated - it derives from a label present in "
            "the source data. Measured achievable accuracy on the primary "
            "dataset is about 0.62 (majority baseline 0.38), so it is genuinely "
            "learnable but far from deterministic. On the students dataset the "
            "same style of label is ~93% recoverable from two survey items, so "
            "results there must be reported with that caveat."
        ),
    ),
)


ALL_FEATURES: tuple[FeatureSpec, ...] = (
    PERSONAL_RAW + PERSONAL_DERIVED + POPULATION_FEATURES + TARGETS
)

#: Lookup by key.
BY_KEY: dict[str, FeatureSpec] = {spec.key: spec for spec in ALL_FEATURES}


def features_for(module: MLModule) -> list[FeatureSpec]:
    """Every feature declared as used by a given ML module."""
    return [spec for spec in ALL_FEATURES if module in spec.used_by]


def classifier_features() -> list[str]:
    """Population columns used by the classifier.

    Deliberately EXCLUDES the zero-information columns identified by
    profiling (gaming_hours, notifications_per_day, app_opens_per_day, age).
    Feeding a model pure noise does not make it more rigorous - it adds
    variance and dilutes the features that matter.
    """
    return [
        "daily_screen_time_hours",
        "social_media_hours",
        "weekend_screen_time",
        "work_study_hours",
        "sleep_hours",
    ]


def to_dict(spec: FeatureSpec) -> dict:
    """Serialise one spec for the API."""
    return {
        "key": spec.key,
        "label": spec.label,
        "description": spec.description,
        "unit": spec.unit,
        "origin": spec.origin.value,
        "valid_min": spec.valid_min,
        "valid_max": spec.valid_max,
        "used_by": [m.value for m in spec.used_by],
        "formula": spec.formula,
        "caveat": spec.caveat,
    }
