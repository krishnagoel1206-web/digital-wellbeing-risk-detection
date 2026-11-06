"""Data cleaning: validation, duplicates, missing values and outlier analysis.

WHY SCALING IS NOT IN THIS MODULE
--------------------------------
Feature scaling is a required part of the pipeline, but doing it here would
be a data-leakage bug. A StandardScaler fitted on the whole dataset has
already seen the test fold's mean and standard deviation, so any accuracy
measured afterwards is optimistically biased.

Scaling therefore lives INSIDE the scikit-learn Pipeline used for training,
where cross-validation refits it on each training fold only. This module
handles the operations that are legitimately global - removing duplicate
rows, reporting missing values, and flagging impossible values - and stops
there.

EVERYTHING IS REPORTED, NOTHING IS SILENT
-----------------------------------------
Each function returns a report alongside the cleaned frame. The brief
requires the app to show a real validation report, and silent cleaning is
how datasets quietly become wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.ml.preprocessing.feature_dictionary import BY_KEY


@dataclass
class CleaningReport:
    """A complete account of what cleaning did."""

    rows_in: int
    rows_out: int
    duplicates_removed: int = 0
    #: column -> number of missing values found.
    missing_before: dict[str, int] = field(default_factory=dict)
    #: column -> how many values were imputed, and with what strategy.
    imputed: dict[str, str] = field(default_factory=dict)
    #: column -> count of values outside the documented valid range.
    out_of_range: dict[str, int] = field(default_factory=dict)
    #: column -> count of statistical outliers (1.5 x IQR).
    outliers: dict[str, int] = field(default_factory=dict)
    #: Rows dropped for being unusable, with the reason.
    rows_rejected: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.rows_in} rows in -> {self.rows_out} out "
            f"({self.duplicates_removed} duplicates removed, "
            f"{len(self.rows_rejected)} rejected)"
        )


def remove_duplicates(
    df: pd.DataFrame, subset: list[str] | None = None
) -> tuple[pd.DataFrame, int]:
    """Drop duplicate rows, keeping the first occurrence.

    `subset` lets callers define duplication by key columns (for personal
    records, two rows for the same date are duplicates even if the figures
    differ) rather than requiring every column to match.
    """
    before = len(df)
    cleaned = df.drop_duplicates(subset=subset, keep="first")
    return cleaned, before - len(cleaned)


def find_out_of_range(df: pd.DataFrame) -> dict[str, int]:
    """Count values violating the documented bounds in the feature dictionary.

    Only columns that appear in the dictionary are checked - an unknown column
    has no documented range, and inventing one would be guesswork.
    """
    violations: dict[str, int] = {}
    for col in df.columns:
        spec = BY_KEY.get(col)
        if spec is None:
            continue
        if spec.valid_min is None and spec.valid_max is None:
            continue

        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if series.empty:
            continue

        mask = pd.Series(False, index=series.index)
        if spec.valid_min is not None:
            mask |= series < spec.valid_min
        if spec.valid_max is not None:
            mask |= series > spec.valid_max

        count = int(mask.sum())
        if count:
            violations[col] = count
    return violations


def find_outliers_iqr(df: pd.DataFrame, factor: float = 1.5) -> dict[str, int]:
    """Count statistical outliers per numeric column using the IQR rule.

    This is ANALYSIS, not removal. Outliers are reported so they can be
    inspected; they are not deleted, because in behavioural data an extreme
    day is often the most interesting observation rather than an error. The
    anomaly-detection module exists precisely to study such days.
    """
    counts: dict[str, int] = {}
    for col in df.select_dtypes(include=[np.number]).columns:
        series = df[col].dropna()
        if len(series) < 4:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - factor * iqr, q3 + factor * iqr
        count = int(((series < lo) | (series > hi)).sum())
        if count:
            counts[col] = count
    return counts


def impute_missing(
    df: pd.DataFrame,
    strategy: str = "median",
    columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Fill missing numeric values, recording what was done to each column.

    Median is the default rather than mean because it is unaffected by the
    extreme values that behavioural data routinely contains.

    IMPORTANT: this is for DESCRIPTIVE use (charts, summaries). For model
    training, imputation must happen inside the Pipeline so each fold is
    imputed from its own training data only. Imputing globally here and then
    cross-validating would leak information between folds.
    """
    if strategy not in {"median", "mean", "zero"}:
        raise ValueError(f"unsupported imputation strategy: {strategy}")

    target_cols = columns or list(df.select_dtypes(include=[np.number]).columns)
    out = df.copy()
    applied: dict[str, str] = {}

    for col in target_cols:
        if col not in out.columns:
            continue
        n_missing = int(out[col].isna().sum())
        if n_missing == 0:
            continue

        if strategy == "zero":
            fill_value: float = 0.0
        elif strategy == "mean":
            fill_value = float(out[col].mean())
        else:
            fill_value = float(out[col].median())

        out[col] = out[col].fillna(fill_value)
        applied[col] = f"{n_missing} value(s) filled with {strategy} ({fill_value:.2f})"

    return out, applied


def clean_population_frame(
    df: pd.DataFrame,
    *,
    required_columns: list[str],
    impute: bool = False,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Clean a population training frame and report everything that happened.

    Steps, in order:
      1. Record missing values per column.
      2. Drop exact duplicate rows.
      3. Reject rows missing a REQUIRED column - these cannot be used for
         supervised training because the target or a core feature is absent.
      4. Report out-of-range values against the feature dictionary.
      5. Report IQR outliers (analysis only - nothing is deleted).
      6. Optionally impute remaining missing values, for descriptive use.
    """
    report = CleaningReport(rows_in=len(df), rows_out=len(df))
    report.missing_before = {c: int(df[c].isna().sum()) for c in df.columns}

    work, n_dupes = remove_duplicates(df)
    report.duplicates_removed = n_dupes
    if n_dupes:
        report.notes.append(f"removed {n_dupes} exact duplicate row(s)")

    present_required = [c for c in required_columns if c in work.columns]
    missing_required = set(required_columns) - set(present_required)
    if missing_required:
        report.notes.append(
            f"required column(s) absent from this file: {sorted(missing_required)}"
        )

    if present_required:
        incomplete = work[present_required].isna().any(axis=1)
        n_incomplete = int(incomplete.sum())
        if n_incomplete:
            for idx in work.index[incomplete][:50]:
                absent = [c for c in present_required if pd.isna(work.at[idx, c])]
                report.rows_rejected.append(
                    {"row": int(idx), "reason": f"missing required: {absent}"}
                )
            work = work[~incomplete]
            report.notes.append(
                f"rejected {n_incomplete} row(s) missing a required column"
            )

    report.out_of_range = find_out_of_range(work)
    if report.out_of_range:
        report.notes.append(f"values outside documented range: {report.out_of_range}")

    report.outliers = find_outliers_iqr(work)
    if report.outliers:
        report.notes.append(
            f"IQR outliers detected (reported, NOT removed): {report.outliers}"
        )
    else:
        report.notes.append("no IQR outliers detected in any numeric column")

    if impute:
        work, applied = impute_missing(work)
        report.imputed = applied
        if applied:
            report.notes.append(
                "imputed for DESCRIPTIVE use only; model training imputes "
                "inside the Pipeline to avoid leakage"
            )

    report.rows_out = len(work)
    return work, report
