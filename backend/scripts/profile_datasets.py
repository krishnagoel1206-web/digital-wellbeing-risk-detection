"""Exploratory profiling of the raw datasets.

Run from the backend/ directory:

    .venv/Scripts/python.exe -I scripts/profile_datasets.py

WHY THIS SCRIPT EXISTS
----------------------
The brief says never to assume a field exists, and not to fabricate labels or
claim accuracy. This script is how those rules are honoured in practice: it
reports what the data actually contains before any model is written, and it
runs an explicit test for whether the classification target is a deterministic
function of the features (the "circularity" problem).

If the target IS recoverable from a couple of columns, any classifier will hit
near-100% accuracy and that number means nothing. Knowing this before training
is what lets the project report it honestly rather than brag about it.

This script only READS data. It writes nothing and trains no production model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.feature_selection import mutual_info_classif
from sklearn.model_selection import cross_val_score
from sklearn.tree import DecisionTreeClassifier, export_text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
RAW = BACKEND_ROOT / "data" / "raw"

PRIMARY = RAW / "Smartphone_Usage_And_Addiction_Analysis_7500_Rows.csv"
STUDENTS = RAW / "Students Social Media Addiction.csv"

RANDOM_STATE = 42
LINE = "=" * 78


def rule(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def basic_profile(df: pd.DataFrame, name: str) -> None:
    """Shape, dtypes, missing values, duplicates - the unglamorous essentials."""
    rule(f"{name}: STRUCTURE")
    print(f"rows: {len(df)}   columns: {df.shape[1]}")

    print("\nper-column: dtype / missing / unique")
    print(f"{'column':<32} {'dtype':<10} {'missing':>8} {'miss%':>7} {'unique':>8}")
    for col in df.columns:
        missing = int(df[col].isna().sum())
        pct = 100.0 * missing / len(df) if len(df) else 0.0
        print(
            f"{col:<32} {str(df[col].dtype):<10} {missing:>8} {pct:>6.2f}% "
            f"{df[col].nunique(dropna=True):>8}"
        )

    total_missing = int(df.isna().sum().sum())
    print(f"\ntotal missing cells: {total_missing}")

    dupe_full = int(df.duplicated().sum())
    print(f"fully duplicated rows: {dupe_full}")


def numeric_summary(df: pd.DataFrame, name: str) -> None:
    """Descriptive stats plus an IQR-based outlier count per numeric column."""
    rule(f"{name}: NUMERIC SUMMARY + OUTLIERS (1.5 x IQR)")

    numeric = df.select_dtypes(include=[np.number])
    if numeric.empty:
        print("(no numeric columns)")
        return

    print(
        f"{'column':<28} {'min':>8} {'q1':>8} {'median':>8} {'q3':>8} "
        f"{'max':>8} {'mean':>8} {'std':>8} {'outliers':>9}"
    )
    for col in numeric.columns:
        s = numeric[col].dropna()
        if s.empty:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        n_out = int(((s < lo) | (s > hi)).sum())
        print(
            f"{col:<28} {s.min():>8.2f} {q1:>8.2f} {s.median():>8.2f} {q3:>8.2f} "
            f"{s.max():>8.2f} {s.mean():>8.2f} {s.std():>8.2f} {n_out:>9}"
        )


def categorical_summary(df: pd.DataFrame, name: str, max_levels: int = 12) -> None:
    rule(f"{name}: CATEGORICAL VALUES")
    cats = df.select_dtypes(exclude=[np.number])
    if cats.empty:
        print("(no categorical columns)")
        return

    for col in cats.columns:
        n_unique = df[col].nunique(dropna=True)
        # Skip identifier-like columns: listing 7500 ids is noise.
        if n_unique > max_levels:
            print(f"\n{col}: {n_unique} distinct values (identifier-like, not listed)")
            continue
        print(f"\n{col}:")
        counts = df[col].value_counts(dropna=False)
        for value, count in counts.items():
            print(f"   {str(value):<22} {count:>6}  ({100.0 * count / len(df):>5.2f}%)")


def circularity_test(
    df: pd.DataFrame,
    target: str,
    feature_cols: list[str],
    name: str,
) -> None:
    """Test whether `target` is recoverable from the features by a trivial rule.

    Method, and why each piece is there:

    1. A DummyClassifier gives the majority-class baseline. Any accuracy must
       be judged against this, not against zero.
    2. Decision trees of depth 1, 2, 3 and unlimited. A depth-1 tree is a
       single threshold on a single feature. If that alone scores very highly,
       the label is essentially a hand-written rule and a "97% accurate
       classifier" would be measuring nothing but that rule.
    3. Mutual information ranks how much each feature individually tells us
       about the target.
    4. The depth-2 tree is printed so the actual thresholds are visible.

    All scores are 5-fold cross-validated, so they are out-of-sample.
    """
    rule(f"{name}: CIRCULARITY TEST for target '{target}'")

    work = df[feature_cols + [target]].dropna()
    X = work[feature_cols].to_numpy(dtype=float)
    y = work[target].to_numpy()

    print(f"usable rows: {len(work)}   features: {len(feature_cols)}")
    print(f"classes: {sorted(pd.unique(y).tolist())}")

    majority = cross_val_score(
        DummyClassifier(strategy="most_frequent"), X, y, cv=5, scoring="accuracy"
    ).mean()
    print(f"\nmajority-class baseline accuracy : {majority:.4f}")

    print("\ndecision-tree accuracy by depth (5-fold CV):")
    for depth in (1, 2, 3, None):
        clf = DecisionTreeClassifier(max_depth=depth, random_state=RANDOM_STATE)
        score = cross_val_score(clf, X, y, cv=5, scoring="accuracy").mean()
        label = "unlimited" if depth is None else f"depth {depth}"
        lift = score - majority
        print(f"   {label:<12} accuracy {score:.4f}   (+{lift:.4f} over baseline)")

    print("\nmutual information with the target (higher = more informative):")
    mi = mutual_info_classif(X, y, random_state=RANDOM_STATE)
    for featname, value in sorted(zip(feature_cols, mi), key=lambda t: -t[1]):
        print(f"   {featname:<28} {value:.4f}")

    print("\nactual depth-2 tree (shows the thresholds being used):")
    shallow = DecisionTreeClassifier(max_depth=2, random_state=RANDOM_STATE).fit(X, y)
    print(export_text(shallow, feature_names=feature_cols, show_weights=False))

    print("INTERPRETATION GUIDE")
    print("  depth-1 accuracy near 1.00      -> label is a single-feature threshold (fully circular)")
    print("  unlimited accuracy near 1.00    -> label is a deterministic rule over the features")
    print("  accuracy close to the baseline  -> features barely predict the label")
    print("  something in between            -> a genuine, learnable relationship")


def class_means(df: pd.DataFrame, target: str, feature_cols: list[str], name: str) -> None:
    """Per-class feature means, to see which features actually separate classes."""
    rule(f"{name}: FEATURE MEANS PER '{target}' CLASS")
    grouped = df.groupby(target)[feature_cols].mean().round(2)
    print(grouped.to_string())


def main() -> int:
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)

    print(f"pandas {pd.__version__} | numpy {np.__version__}")

    missing = [p for p in (PRIMARY, STUDENTS) if not p.exists()]
    if missing:
        for path in missing:
            print(f"MISSING: {path}", file=sys.stderr)
        print("\nPlace the dataset CSVs in backend/data/raw/ - see README.md", file=sys.stderr)
        return 1

    # ---------------- PRIMARY DATASET ----------------
    primary = pd.read_csv(PRIMARY)
    basic_profile(primary, "PRIMARY (7500-row smartphone usage)")
    numeric_summary(primary, "PRIMARY")
    categorical_summary(primary, "PRIMARY")

    # Only behavioural columns are offered as features. Identifiers are
    # excluded because they carry no behavioural meaning, and `addicted_label`
    # is excluded because it was shown to be a perfect function of
    # `addiction_level` - including it would be straightforward leakage.
    primary_features = [
        "daily_screen_time_hours",
        "social_media_hours",
        "gaming_hours",
        "work_study_hours",
        "sleep_hours",
        "notifications_per_day",
        "app_opens_per_day",
        "weekend_screen_time",
        "age",
    ]
    available = [c for c in primary_features if c in primary.columns]
    if len(available) != len(primary_features):
        print(f"\nWARNING: expected features absent: {set(primary_features) - set(available)}")

    circularity_test(primary, "addiction_level", available, "PRIMARY")
    class_means(primary, "addiction_level", available, "PRIMARY")

    # Does the duplicate target add anything beyond addiction_level?
    rule("PRIMARY: RELATIONSHIP BETWEEN THE TWO TARGETS")
    print(pd.crosstab(primary["addiction_level"], primary["addicted_label"]).to_string())
    print(
        "\nA clean block-diagonal pattern means addicted_label is fully "
        "determined by addiction_level, so the two are redundant."
    )

    # ---------------- STUDENTS DATASET ----------------
    students = pd.read_csv(STUDENTS)
    basic_profile(students, "STUDENTS (705-row self-reported)")
    numeric_summary(students, "STUDENTS")
    categorical_summary(students, "STUDENTS")

    student_features = [
        "Age",
        "Avg_Daily_Usage_Hours",
        "Sleep_Hours_Per_Night",
        "Mental_Health_Score",
        "Conflicts_Over_Social_Media",
    ]
    s_available = [c for c in student_features if c in students.columns]

    # Addicted_Score is 1-10; bin into the app's three risk classes so the
    # same circularity test applies. Cut points are documented, not arbitrary:
    # low 1-4, medium 5-7, high 8-10.
    if "Addicted_Score" in students.columns:
        students = students.assign(
            risk_class=pd.cut(
                students["Addicted_Score"],
                bins=[0, 4, 7, 10],
                labels=["Low", "Medium", "High"],
            )
        )
        rule("STUDENTS: Addicted_Score binned into three risk classes")
        print("cut points: Low = 1-4, Medium = 5-7, High = 8-10")
        print(students["risk_class"].value_counts().to_string())

        circularity_test(students, "risk_class", s_available, "STUDENTS")
        class_means(students, "risk_class", s_available, "STUDENTS")

    rule("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
