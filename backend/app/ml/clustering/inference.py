"""INFERENCE for persona assignment.

Loads the clustering artifact and assigns the user to their nearest persona.
Never fits.

AN IMPORTANT HONESTY CONSTRAINT
-------------------------------
Training measured the cluster structure in this dataset as WEAK (silhouette
0.107, and silhouette varying by only 0.011 across k = 2..6). K-Means will
always hand back a cluster, so an assignment is always available - but that
does not make it meaningful.

Every response from this module therefore carries the structure verdict
alongside the persona, so the app can present a weak assignment as "closest
match" rather than as a discovered behavioural type. Presenting an arbitrary
partition as a real persona would be exactly the kind of overclaiming the
brief forbids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.config import get_settings
from app.ml.classification.inference import (
    InsufficientHistory,
    MissingFeatures,
    features_from_personal_history,
)
from app.ml.clustering.personas import ARTIFACT_NAME


class ModelNotTrained(Exception):
    """Raised when clustering has not been trained yet."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"model '{name}' has not been trained. Run: "
            "python scripts/train_clusters_and_score_anomalies.py"
        )


@dataclass
class PersonaAssignment:
    """The user's nearest persona, with an honest caveat about reliability."""

    cluster_id: int
    name: str
    traits: list[str]
    #: Share of the training population in this cluster.
    population_share: float
    #: How many days of the user's history were averaged.
    days_averaged: int
    #: "strong", "moderate", "weak", "none" or "unmeasurable".
    structure_verdict: str
    structure_detail: str
    #: Distance to the assigned centroid, and to the next nearest, so the app
    #: can show whether the assignment was close-run.
    distance_to_assigned: float
    distance_to_next: float | None
    inputs: dict[str, float] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _load_artifact() -> dict[str, Any]:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    if not path.exists():
        raise ModelNotTrained(ARTIFACT_NAME)
    return joblib.load(path)


def reset_cache() -> None:
    _load_artifact.cache_clear()


def is_trained() -> bool:
    settings = get_settings()
    return (settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib").exists()


def load_metrics() -> dict | None:
    settings = get_settings()
    path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def assign_persona(daily: pd.DataFrame, *, min_days: int = 7) -> PersonaAssignment:
    """Assign the user to their nearest persona.

    The user's daily records are averaged into a typical day and converted to
    the population feature space, exactly as for the classifier, so the two
    modules describe the same person in the same terms.
    """
    artifact = _load_artifact()
    pipeline = artifact["pipeline"]
    features: list[str] = artifact["feature_columns"]
    personas: list[dict] = artifact["personas"]

    # Reuse the classifier's documented personal -> population mapping, then
    # fill in the clustering-only columns from the same history.
    mapped, days = features_from_personal_history(daily, min_days=min_days)

    df = daily.copy()

    def mean_hours(column: str) -> float:
        if column not in df.columns:
            return float("nan")
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        return float(series.mean() / 60.0) if not series.empty else float("nan")

    def mean_count(column: str) -> float:
        if column not in df.columns:
            return float("nan")
        series = pd.to_numeric(df[column], errors="coerce").dropna()
        return float(series.mean()) if not series.empty else float("nan")

    values: dict[str, float] = {
        "daily_screen_time_hours": mapped["daily_screen_time_hours"],
        "social_media_hours": mapped["social_media_hours"],
        "work_study_hours": mapped["work_study_hours"],
        "sleep_hours": mapped["sleep_hours"],
        "gaming_hours": mean_hours("gaming_minutes"),
        "notifications_per_day": mean_count("notification_count"),
        "app_opens_per_day": mean_count("app_launches"),
    }

    missing = [f for f in features if not np.isfinite(values.get(f, float("nan")))]
    if missing:
        # The days exist; specific measurements do not. Say which.
        raise MissingFeatures(features=missing, days=days)

    X = np.array([[values[f] for f in features]], dtype=float)

    cluster_id = int(pipeline.predict(X)[0])

    # Distances to every centroid, so a close-run assignment is visible.
    scaler = pipeline.named_steps["scaler"]
    kmeans = pipeline.named_steps["model"]
    scaled = scaler.transform(X)
    distances = np.linalg.norm(kmeans.cluster_centers_ - scaled, axis=1)
    ordered = np.sort(distances)

    profile = next(
        (p for p in personas if p["cluster_id"] == cluster_id),
        {"name": f"Cluster {cluster_id}", "traits": [], "share": float("nan")},
    )

    metrics = load_metrics() or {}
    structure = metrics.get("overall_structure", {})

    return PersonaAssignment(
        cluster_id=cluster_id,
        name=profile["name"],
        traits=profile.get("traits", []),
        population_share=float(profile.get("share", float("nan"))),
        days_averaged=days,
        structure_verdict=structure.get("verdict", "unmeasurable"),
        structure_detail=structure.get("detail", ""),
        distance_to_assigned=round(float(ordered[0]), 4),
        distance_to_next=round(float(ordered[1]), 4) if len(ordered) > 1 else None,
        inputs={k: round(v, 3) for k, v in values.items() if k in features},
    )
