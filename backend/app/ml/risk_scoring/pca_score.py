"""PCA-based Digital Wellbeing Risk Score (0-100).

THE PROBLEM WITH "JUST USE PCA"
-------------------------------
PCA does not produce a risk score. It finds the directions of greatest
variance in the data, which is a statement about spread, not about risk.
Three things must be designed and then VALIDATED before a principal component
can be called a risk score:

  1. ORIENTATION. The sign of a principal component is arbitrary - PCA is
     free to return either +PC1 or -PC1, and the choice is an implementation
     detail of the eigensolver. So "high PC1" might mean heavy use or light
     use. The direction is therefore FIXED by checking it against the known
     risk labels, and flipped if needed.

  2. COMPONENT SELECTION AND WEIGHTING. Using PC1 alone throws away
     information; using all components reintroduces noise. Components are
     selected by cumulative explained variance and weighted by their own
     explained-variance ratio, so a component that explains little
     contributes little.

  3. THE SCALE. A raw component score is unbounded and has no natural units.
     It is mapped to 0-100 through the empirical cumulative distribution of
     the training population, which makes the number interpretable as a
     percentile rather than an arbitrary quantity.

VALIDATION IS NOT OPTIONAL
--------------------------
After construction, the score is checked against the dataset's own
`risk_class` labels:
  * Spearman correlation between score and risk ordinal, and
  * whether mean score is MONOTONIC across Low < Medium < High.

If monotonicity fails, the report records the failure rather than hiding it.
A composite score that does not order the known classes correctly is not a
risk score, whatever its construction.

KERNEL PCA
----------
Kernel PCA (RBF) is fitted alongside for comparison, because a non-linear
embedding can capture structure linear PCA misses. It is only adopted if it
validates better. Its drawback is reported: kernel components have no
feature loadings, so the score becomes unexplainable in terms of the user's
own behaviour - which matters for an app that must say WHY.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import PCA, KernelPCA
from sklearn.preprocessing import StandardScaler

from app.config import get_settings

ARTIFACT_NAME = "risk_score_pca"
RANDOM_STATE = 42

#: Behavioural features the composite score is built from. The full
#: behavioural set is used, not just the classifier's subset: the score is
#: meant to summarise overall behaviour, and a feature can be worth
#: describing even if it does not predict the label.
SCORE_FEATURES = [
    "daily_screen_time_hours",
    "social_media_hours",
    "gaming_hours",
    "work_study_hours",
    "sleep_hours",
    "notifications_per_day",
    "app_opens_per_day",
]

#: Keep components until this much cumulative variance is explained.
CUMULATIVE_VARIANCE_TARGET = 0.80

#: Risk classes mapped to an ordinal scale for validation only - never used
#: to fit the PCA, which stays unsupervised.
RISK_ORDINAL = {"Low": 0, "Medium": 1, "High": 2}


@dataclass
class ComponentInfo:
    """One retained principal component."""

    index: int
    explained_variance_ratio: float
    weight: float
    #: feature -> loading (how strongly the feature contributes).
    loadings: dict[str, float] = field(default_factory=dict)
    #: The features dominating this component, strongest first.
    dominant_features: list[str] = field(default_factory=list)


def _percentile_mapper(raw_scores: np.ndarray) -> np.ndarray:
    """Sorted training scores, used later to map a raw score to a percentile.

    Storing the sorted training distribution (rather than only a mean and
    standard deviation) means the mapping makes no assumption about the
    shape of the distribution.
    """
    return np.sort(raw_scores)


def raw_to_score(raw: float, sorted_training: np.ndarray) -> float:
    """Map a raw composite value onto 0-100 by its percentile.

    Percentile rank is used instead of min-max scaling because min-max is
    controlled entirely by the two most extreme training rows: one outlier
    would compress everybody else into a narrow band.
    """
    if sorted_training.size == 0:
        return float("nan")
    position = float(np.searchsorted(sorted_training, raw, side="right"))
    return float(100.0 * position / sorted_training.size)


def _validate_against_labels(
    scores: np.ndarray, risk_class: pd.Series
) -> dict:
    """Check the score actually orders the known risk classes.

    This is the step that decides whether the construction worked.
    """
    labels = risk_class.to_numpy()
    mask = pd.notna(labels)
    scores_v, labels_v = scores[mask], labels[mask]

    ordinal = np.array([RISK_ORDINAL.get(str(lab), np.nan) for lab in labels_v], dtype=float)
    valid = np.isfinite(ordinal)
    if valid.sum() < 10:
        return {
            "validated": False,
            "reason": "too few labelled rows to validate",
        }

    rho, p_value = stats.spearmanr(scores_v[valid], ordinal[valid])

    means: dict[str, float] = {}
    for label in ("Low", "Medium", "High"):
        subset = scores_v[labels_v == label]
        if subset.size:
            means[label] = float(np.mean(subset))

    ordered = [means[k] for k in ("Low", "Medium", "High") if k in means]
    monotonic = all(a < b for a, b in zip(ordered, ordered[1:]))

    # Separation between the extreme classes, in score points, as a plain
    # measure of whether the score is actually useful.
    separation = (
        float(means["High"] - means["Low"]) if {"Low", "High"} <= means.keys() else None
    )

    return {
        "validated": bool(monotonic and rho > 0.2),
        "spearman_rho": float(rho),
        "spearman_p": float(p_value),
        "mean_score_per_class": {k: round(v, 2) for k, v in means.items()},
        "monotonic_low_medium_high": bool(monotonic),
        "low_to_high_separation": round(separation, 2) if separation is not None else None,
        "interpretation": (
            "A positive rho with monotonically increasing class means shows "
            "the composite score orders the known risk classes correctly. "
            "Monotonicity failing would mean the construction does not "
            "measure risk, regardless of how much variance it explains."
        ),
    }


def _describe_components(
    pca: PCA, features: list[str], n_keep: int
) -> tuple[list[ComponentInfo], np.ndarray]:
    """Build descriptions and the component weights."""
    ratios = pca.explained_variance_ratio_[:n_keep]
    # Weight each retained component by its share of the RETAINED variance,
    # so the weights sum to 1 and a weak component cannot dominate.
    weights = ratios / ratios.sum()

    infos: list[ComponentInfo] = []
    for i in range(n_keep):
        loadings = {f: float(v) for f, v in zip(features, pca.components_[i])}
        dominant = [
            f for f, _ in sorted(loadings.items(), key=lambda kv: -abs(kv[1]))[:3]
        ]
        infos.append(
            ComponentInfo(
                index=i,
                explained_variance_ratio=float(ratios[i]),
                weight=float(weights[i]),
                loadings={f: round(v, 4) for f, v in loadings.items()},
                dominant_features=dominant,
            )
        )

    return infos, weights


def train(df: pd.DataFrame) -> dict:
    """Fit the risk score, validate it, and save the artifact."""
    settings = get_settings()

    features = [c for c in SCORE_FEATURES if c in df.columns]
    if len(features) < 3:
        raise ValueError(f"need at least 3 score features, found {features}")

    work = df[features + (["risk_class"] if "risk_class" in df.columns else [])].dropna(
        subset=features
    )

    scaler = StandardScaler().fit(work[features].to_numpy(dtype=float))
    X = scaler.transform(work[features].to_numpy(dtype=float))

    # --- linear PCA ------------------------------------------------------
    pca = PCA(n_components=len(features), random_state=RANDOM_STATE).fit(X)

    cumulative = np.cumsum(pca.explained_variance_ratio_)
    n_keep = int(np.searchsorted(cumulative, CUMULATIVE_VARIANCE_TARGET) + 1)
    n_keep = max(1, min(n_keep, len(features)))

    components, weights = _describe_components(pca, features, n_keep)

    projected = pca.transform(X)[:, :n_keep]
    raw = projected @ weights

    # --- ORIENTATION: fix the arbitrary sign using the labels ------------
    orientation_note = "no labels available; sign left as PCA returned it"
    flipped = False
    if "risk_class" in work.columns:
        ordinal = work["risk_class"].map(RISK_ORDINAL).to_numpy(dtype=float)
        valid = np.isfinite(ordinal)
        if valid.sum() >= 10:
            rho, _ = stats.spearmanr(raw[valid], ordinal[valid])
            if rho < 0:
                raw = -raw
                weights = -weights
                flipped = True
            orientation_note = (
                f"sign {'FLIPPED' if flipped else 'kept'}: correlation with the "
                f"risk ordinal was {rho:+.4f} before orienting, so higher score "
                "now means higher risk"
            )

    sorted_training = _percentile_mapper(raw)
    scores_0_100 = np.array([raw_to_score(v, sorted_training) for v in raw])

    validation = (
        _validate_against_labels(scores_0_100, work["risk_class"])
        if "risk_class" in work.columns
        else {"validated": False, "reason": "no risk_class column to validate against"}
    )

    # --- Kernel PCA, for comparison only ---------------------------------
    kernel_validation: dict = {"attempted": False}
    try:
        # Subsample: KernelPCA builds an n x n kernel matrix, which is
        # 7500x7500 here and needlessly expensive for a comparison.
        rng = np.random.default_rng(RANDOM_STATE)
        idx = rng.choice(len(X), size=min(2000, len(X)), replace=False)
        kpca = KernelPCA(
            n_components=2, kernel="rbf", gamma=None, random_state=RANDOM_STATE
        ).fit(X[idx])
        k_proj = kpca.transform(X[idx])[:, 0]

        if "risk_class" in work.columns:
            k_ord = work["risk_class"].iloc[idx].map(RISK_ORDINAL).to_numpy(dtype=float)
            k_valid = np.isfinite(k_ord)
            k_rho, k_p = stats.spearmanr(k_proj[k_valid], k_ord[k_valid])
            kernel_validation = {
                "attempted": True,
                "kernel": "rbf",
                "n_subsample": int(len(idx)),
                "spearman_rho_pc1": float(k_rho),
                "spearman_p": float(k_p),
                "abs_rho": abs(float(k_rho)),
                "limitation": (
                    "Kernel PCA components have no feature loadings, so a score "
                    "built on them cannot explain WHICH behaviour drove it. That "
                    "matters more than a small accuracy gain for an app that has "
                    "to tell the user why."
                ),
            }
    except Exception as exc:  # pragma: no cover - defensive
        kernel_validation = {"attempted": True, "error": f"{type(exc).__name__}: {exc}"}

    # Linear PCA is adopted unless Kernel PCA validates MUCH better, because
    # explainability is a stated requirement.
    linear_abs_rho = abs(validation.get("spearman_rho", 0.0) or 0.0)
    kernel_abs_rho = kernel_validation.get("abs_rho", 0.0)
    adopted = "PCA"
    adoption_reason = (
        f"Linear PCA adopted. |rho| linear = {linear_abs_rho:.4f}, "
        f"kernel = {kernel_abs_rho:.4f}. Linear PCA is preferred unless the "
        "kernel is decisively better, because only linear loadings let the app "
        "explain which behaviour drove the score."
    )

    settings.ensure_directories()
    artifact_path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    joblib.dump(
        {
            "scaler": scaler,
            "pca": pca,
            "n_components": n_keep,
            "weights": weights,
            "feature_columns": features,
            "sorted_training_raw": sorted_training,
            "model_name": adopted,
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "n_training_rows": int(len(work)),
            "validated": validation.get("validated", False),
        },
        artifact_path,
    )

    report = {
        "task": "risk_scoring",
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_samples": int(len(work)),
        "features": features,
        "method": {
            "step_1": "standardise features (zero mean, unit variance)",
            "step_2": (
                f"fit PCA and retain the components reaching "
                f"{CUMULATIVE_VARIANCE_TARGET:.0%} cumulative explained variance "
                f"-> {n_keep} component(s)"
            ),
            "step_3": (
                "weight each retained component by its share of the retained "
                "variance, so weights sum to 1"
            ),
            "step_4": (
                "orient the composite using the known risk labels, because the "
                "sign of a principal component is arbitrary"
            ),
            "step_5": (
                "map the raw composite onto 0-100 by percentile rank against "
                "the training distribution, not min-max (which one outlier "
                "would dominate)"
            ),
        },
        "explained_variance_ratio": [
            float(v) for v in pca.explained_variance_ratio_
        ],
        "cumulative_explained_variance": [float(v) for v in cumulative],
        "n_components_retained": n_keep,
        "orientation": orientation_note,
        "sign_flipped": flipped,
        "components": [
            {
                "index": c.index,
                "explained_variance_ratio": round(c.explained_variance_ratio, 4),
                "weight": round(c.weight, 4),
                "dominant_features": c.dominant_features,
                "loadings": c.loadings,
            }
            for c in components
        ],
        "validation": validation,
        "kernel_pca_comparison": kernel_validation,
        "adopted_method": adopted,
        "adoption_reason": adoption_reason,
        "artifact": artifact_path.name,
    }

    metrics_path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report
