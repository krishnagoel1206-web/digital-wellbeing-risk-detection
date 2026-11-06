"""Shared metric computation for regression and classification.

Centralised so that every model is scored the same way and the numbers shown
in the app are the numbers that were actually measured at training time.

A NOTE ON WHAT THESE NUMBERS MEAN
---------------------------------
All reported scores come from CROSS-VALIDATION on held-out folds, never from
the data the model was fitted on. A model scored on its own training data
will flatter itself, sometimes dramatically, and the brief explicitly forbids
inventing or overstating evaluation results.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)


@dataclass
class RegressionMetrics:
    """Scores for a continuous prediction."""

    rmse: float
    mae: float
    r2: float
    #: Mean of the target, for context - an RMSE of 50 means something very
    #: different on a target averaging 60 than on one averaging 600.
    target_mean: float
    #: RMSE as a percentage of the target mean, for scale-free comparison.
    rmse_pct_of_mean: float
    n_samples: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClassificationMetrics:
    """Scores for a categorical prediction."""

    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    #: Weighted variants account for class imbalance.
    precision_weighted: float
    recall_weighted: float
    f1_weighted: float
    #: One-vs-rest macro ROC-AUC. None when probabilities are unavailable.
    roc_auc_ovr: float | None
    #: Why ROC-AUC is None, when it is. Never silently blank.
    roc_auc_error: str | None
    #: Row = true class, column = predicted class, ordered by `labels`.
    confusion: list[list[int]]
    labels: list[str]
    #: Accuracy of always predicting the most common class. The honest
    #: yardstick: a model must beat this to have learned anything.
    majority_baseline: float
    n_samples: int
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> RegressionMetrics:
    """Compute regression scores.

    RMSE is computed as sqrt(MSE) explicitly rather than relying on a
    `squared=False` argument, which has been removed in recent scikit-learn.
    """
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)

    mse = float(mean_squared_error(y_true, y_pred))
    rmse = float(np.sqrt(mse))
    target_mean = float(np.mean(y_true))

    return RegressionMetrics(
        rmse=rmse,
        mae=float(mean_absolute_error(y_true, y_pred)),
        r2=float(r2_score(y_true, y_pred)),
        target_mean=target_mean,
        rmse_pct_of_mean=float(100.0 * rmse / target_mean) if target_mean else float("nan"),
        n_samples=int(len(y_true)),
    )


def classification_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    labels: list[str],
    y_proba: np.ndarray | None = None,
) -> ClassificationMetrics:
    """Compute classification scores.

    `zero_division=0` is passed explicitly: if a model never predicts a given
    class, that class's precision is undefined, and scikit-learn would emit a
    warning and substitute 0. Making it explicit documents the choice.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)

    # Majority baseline computed from the TRUE distribution of this sample.
    _, counts = np.unique(y_true, return_counts=True)
    majority = float(counts.max() / counts.sum())

    roc_auc: float | None = None
    roc_auc_error: str | None = None
    if y_proba is not None:
        try:
            # scikit-learn requires `labels` to be in SORTED order here, while
            # `labels` as passed in is in severity order (Low, Medium, High)
            # because that is what reads correctly in a confusion matrix.
            # Passing the severity order raises "Parameter 'labels' must be
            # ordered", so the probability columns are reordered to match the
            # sorted labels instead.
            sorted_labels = sorted(labels)
            reorder = [labels.index(lab) for lab in sorted_labels]
            proba_sorted = np.asarray(y_proba)[:, reorder]

            roc_auc = float(
                roc_auc_score(
                    y_true,
                    proba_sorted,
                    multi_class="ovr",
                    average="macro",
                    labels=sorted_labels,
                )
            )
        except (ValueError, IndexError) as exc:
            # Genuinely unavailable, e.g. a fold missing a class entirely.
            # The reason is RECORDED rather than silently becoming None - a
            # swallowed exception here is exactly how ROC-AUC quietly went
            # missing for every model in the first training run.
            roc_auc = None
            roc_auc_error = f"{type(exc).__name__}: {exc}"

    cm = confusion_matrix(y_true, y_pred, labels=labels)

    per_class: dict[str, dict[str, float]] = {}
    p_each = precision_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    r_each = recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    f_each = f1_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    for idx, label in enumerate(labels):
        per_class[label] = {
            "precision": float(p_each[idx]),
            "recall": float(r_each[idx]),
            "f1": float(f_each[idx]),
            "support": int((y_true == label).sum()),
        }

    return ClassificationMetrics(
        accuracy=float(accuracy_score(y_true, y_pred)),
        precision_macro=float(precision_score(y_true, y_pred, average="macro", zero_division=0)),
        recall_macro=float(recall_score(y_true, y_pred, average="macro", zero_division=0)),
        f1_macro=float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        precision_weighted=float(
            precision_score(y_true, y_pred, average="weighted", zero_division=0)
        ),
        recall_weighted=float(recall_score(y_true, y_pred, average="weighted", zero_division=0)),
        f1_weighted=float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
        roc_auc_ovr=roc_auc,
        roc_auc_error=roc_auc_error,
        confusion=cm.tolist(),
        labels=list(labels),
        majority_baseline=majority,
        n_samples=int(len(y_true)),
        per_class=per_class,
    )
