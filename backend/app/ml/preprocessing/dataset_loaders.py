"""Loaders for the raw population datasets.

WHY LOADERS EXIST INSTEAD OF CALLING pd.read_csv DIRECTLY
---------------------------------------------------------
Reading these files with default pandas settings corrupts them. Specifically:

    The primary dataset's `addiction_level` column contains the literal
    string "None" for 819 rows (10.92%) - this is the LOWEST-RISK category,
    a real class label. pandas' default `na_values` list includes "None",
    so `pd.read_csv(path)` converts all 819 into NaN.

The consequences if that goes unnoticed:
  * `df.dropna()` silently deletes the entire lowest-risk class.
  * The target appears to have 3 classes when it really has 4.
  * Every model is then trained on a sample biased toward higher risk, and
    reports a class distribution that does not match the source file.

This was caught by profiling the raw file (see scripts/profile_datasets.py)
and comparing pandas' output against a direct byte-level count of the column.
Loading therefore goes through these functions, which disable the default
NA strings and declare explicitly what counts as missing.

Every loader also VERIFIES the columns it expects, rather than assuming them.
A renamed or reordered column raises a clear error instead of producing a
quietly wrong model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.config import get_settings

# Only a genuinely empty cell is missing. Note this list deliberately does
# NOT contain "None", because in these files "None" is a real category.
EXPLICIT_NA_VALUES = ["", " ", "NaN", "nan", "NULL", "null", "#N/A"]

PRIMARY_FILENAME = "Smartphone_Usage_And_Addiction_Analysis_7500_Rows.csv"
STUDENTS_FILENAME = "Students Social Media Addiction.csv"


class DatasetError(Exception):
    """Raised when a dataset file is missing or has an unexpected shape."""


@dataclass
class LoadReport:
    """What happened during a load. Surfaced to the API so the UI can show it."""

    path: str
    rows: int
    columns: list[str]
    missing_per_column: dict[str, int]
    duplicate_rows: int
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Column contracts - what each file MUST provide.
# ---------------------------------------------------------------------------

PRIMARY_REQUIRED = [
    "user_id",
    "age",
    "gender",
    "daily_screen_time_hours",
    "social_media_hours",
    "gaming_hours",
    "work_study_hours",
    "sleep_hours",
    "notifications_per_day",
    "app_opens_per_day",
    "weekend_screen_time",
    "stress_level",
    "addiction_level",
]

STUDENTS_REQUIRED = [
    "Student_ID",
    "Age",
    "Gender",
    "Avg_Daily_Usage_Hours",
    "Sleep_Hours_Per_Night",
    "Mental_Health_Score",
    "Conflicts_Over_Social_Media",
    "Addicted_Score",
]

# The four levels present in the primary dataset, in severity order.
PRIMARY_LEVELS = ["None", "Mild", "Moderate", "Severe"]

# Documented mapping from the dataset's four levels onto the app's three risk
# classes. "None" and "Mild" are merged because the app communicates three
# categories; merging the two least severe keeps the classes reasonably
# balanced (Low 2192 / Medium 2874 / High 2434).
PRIMARY_LEVEL_TO_RISK = {
    "None": "Low",
    "Mild": "Low",
    "Moderate": "Medium",
    "Severe": "High",
}


def _require_columns(df: pd.DataFrame, required: list[str], path: Path) -> None:
    """Fail loudly if the file does not provide the columns we rely on."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DatasetError(
            f"{path.name} is missing required column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )


def _build_report(df: pd.DataFrame, path: Path, notes: list[str]) -> LoadReport:
    return LoadReport(
        path=str(path),
        rows=len(df),
        columns=list(df.columns),
        missing_per_column={c: int(df[c].isna().sum()) for c in df.columns},
        duplicate_rows=int(df.duplicated().sum()),
        notes=notes,
    )


def load_primary_dataset(path: Path | None = None) -> tuple[pd.DataFrame, LoadReport]:
    """Load the 7,500-row smartphone usage dataset.

    Returns the DataFrame plus a LoadReport describing what was read.

    Adds one derived column, `risk_class`, holding the app's three-way label
    produced by the documented mapping above. The original `addiction_level`
    is preserved so the mapping stays auditable.

    `addicted_label` is dropped. Profiling showed it is a perfect function of
    `addiction_level` (block-diagonal crosstab, zero exceptions), so keeping it
    alongside the target would be data leakage.
    """
    settings = get_settings()
    path = path or (settings.raw_data_dir / PRIMARY_FILENAME)
    if not path.exists():
        raise DatasetError(f"dataset not found: {path}")

    notes: list[str] = []

    # The critical part: keep_default_na=False stops "None" being read as NaN.
    df = pd.read_csv(
        path,
        keep_default_na=False,
        na_values=EXPLICIT_NA_VALUES,
        dtype={"addiction_level": "string"},
    )

    _require_columns(df, PRIMARY_REQUIRED, path)

    # Verify the fix actually worked, rather than trusting that it did.
    n_none = int((df["addiction_level"] == "None").sum())
    n_missing = int(df["addiction_level"].isna().sum())
    notes.append(
        f'addiction_level: {n_none} rows have the literal class "None"; '
        f"{n_missing} are genuinely missing"
    )
    if n_none == 0 and n_missing > 0:
        notes.append(
            'WARNING: no "None" strings but missing values present - the NA '
            "handling may have regressed"
        )

    unexpected = sorted(set(df["addiction_level"].dropna().unique()) - set(PRIMARY_LEVELS))
    if unexpected:
        raise DatasetError(
            f"addiction_level contains unexpected level(s) {unexpected}; "
            f"expected only {PRIMARY_LEVELS}"
        )

    # Derived three-way risk class.
    df["risk_class"] = df["addiction_level"].map(PRIMARY_LEVEL_TO_RISK).astype("string")
    notes.append(
        "added risk_class via documented mapping "
        "None/Mild->Low, Moderate->Medium, Severe->High"
    )

    if "addicted_label" in df.columns:
        df = df.drop(columns=["addicted_label"])
        notes.append(
            "dropped addicted_label: it is a perfect function of addiction_level "
            "(verified block-diagonal), so retaining it would leak the target"
        )

    return df, _build_report(df, path, notes)


def load_students_dataset(path: Path | None = None) -> tuple[pd.DataFrame, LoadReport]:
    """Load the 705-row self-reported student dataset.

    Adds `risk_class` by binning `Addicted_Score` into three classes using
    documented cut points. NOTE the profiling finding: this label is roughly
    93% recoverable from `Conflicts_Over_Social_Media` and
    `Mental_Health_Score` alone, so results on this dataset must be reported
    with that caveat rather than presented as independent validation.
    """
    settings = get_settings()
    path = path or (settings.raw_data_dir / STUDENTS_FILENAME)
    if not path.exists():
        raise DatasetError(f"dataset not found: {path}")

    notes: list[str] = []

    df = pd.read_csv(path, keep_default_na=False, na_values=EXPLICIT_NA_VALUES)
    _require_columns(df, STUDENTS_REQUIRED, path)

    observed_min = int(df["Addicted_Score"].min())
    observed_max = int(df["Addicted_Score"].max())
    notes.append(
        f"Addicted_Score observed range {observed_min}-{observed_max} "
        "(documented scale is 1-10)"
    )

    # Cut points chosen on the documented 1-10 scale, not on observed
    # quantiles, so the mapping does not shift if the sample changes.
    df["risk_class"] = pd.cut(
        df["Addicted_Score"],
        bins=[0, 4, 7, 10],
        labels=["Low", "Medium", "High"],
        include_lowest=True,
    ).astype("string")
    notes.append("added risk_class by binning Addicted_Score: 1-4 Low, 5-7 Medium, 8-10 High")

    counts = df["risk_class"].value_counts().to_dict()
    notes.append(f"risk_class distribution: {counts}")
    notes.append(
        "CAVEAT: profiling showed this label is ~93% recoverable from "
        "Conflicts_Over_Social_Media and Mental_Health_Score alone"
    )

    return df, _build_report(df, path, notes)
