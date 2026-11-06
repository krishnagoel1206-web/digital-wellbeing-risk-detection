"""TRAINING for the KNN recommendation engine.

WHAT THIS ENGINE MAY AND MAY NOT CLAIM
--------------------------------------
The dataset is cross-sectional: 7,500 different people observed once each.
Nobody was followed over time, and nobody changed their behaviour and was
re-measured. So the data supports ASSOCIATIONAL statements only:

  ALLOWED  "People whose usage resembles yours and who are classified
            lower-risk typically spend 1.2 fewer hours on social media."
  FORBIDDEN "Reducing social media by 1.2 hours will lower your risk."

The second is a causal claim about an intervention, and nothing in this data
tests an intervention. The brief forbids inventing peer outcomes, so every
generated recommendation is phrased as a comparison and labelled with its
evidence basis.

EVIDENCE BASIS IS MEASURED, NOT ASSUMED
---------------------------------------
Mutual information between each feature and the risk label is computed HERE,
at training time, and stored in the artifact. A feature may only support a
population-grounded recommendation if it actually carries information about
risk in this dataset.

Phase 4 already measured that `notifications_per_day`, `gaming_hours` and
`app_opens_per_day` have mutual information of essentially zero with the
risk label. Telling a user to "reduce notifications to lower your risk" on
the strength of this dataset would therefore be unsupported - the dataset
says notification volume and risk are unrelated. Such features can still
generate PERSONAL-baseline advice ("this is unusual for you"), which is a
different and weaker claim, and is labelled as such.

WHY THE COMPARISON GROUP IS LOW-RISK NEIGHBOURS
-----------------------------------------------
Comparing the user with all neighbours would only describe people like them.
The useful comparison is with the subset of similar people who are
nonetheless classified lower-risk, because that is where a behavioural
difference is informative. The size of that subset is reported, since a
comparison against three people is not worth much.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_classif
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from app.config import get_settings

ARTIFACT_NAME = "knn_recommender"
RANDOM_STATE = 42

#: Neighbourhood size. 75 is large enough that the low-risk subset is usually
#: a usable sample, while still being local in a 7,500-row dataset.
N_NEIGHBOURS = 75

#: Mutual information above which a feature is treated as genuinely
#: risk-relevant in this dataset, and may therefore support a
#: population-grounded recommendation.
MI_RELEVANCE_THRESHOLD = 0.02

#: Features compared between the user and their neighbours.
COMPARISON_FEATURES = [
    "daily_screen_time_hours",
    "social_media_hours",
    "gaming_hours",
    "work_study_hours",
    "sleep_hours",
    "notifications_per_day",
    "app_opens_per_day",
]

#: Which direction counts as an improvement for each feature, and whether a
#: user can plausibly act on it directly.
#: direction: "lower" means a lower value is associated with lower risk.
FEATURE_ACTIONABILITY: dict[str, dict] = {
    "daily_screen_time_hours": {"direction": "lower", "actionable": True},
    "social_media_hours": {"direction": "lower", "actionable": True},
    "gaming_hours": {"direction": "lower", "actionable": True},
    "notifications_per_day": {"direction": "lower", "actionable": True},
    "app_opens_per_day": {"direction": "lower", "actionable": True},
    # Longer sleep is better, and it is partly a consequence of the others
    # rather than a direct lever, which the wording reflects.
    "sleep_hours": {"direction": "higher", "actionable": True},
    # More work/study time is not advice this app should give.
    "work_study_hours": {"direction": "none", "actionable": False},
}


@dataclass
class FeatureEvidence:
    """What the dataset actually supports for one feature."""

    feature: str
    mutual_information: float
    population_grounded: bool
    direction: str
    actionable: bool
    note: str = ""


@dataclass
class TrainingReport:
    n_samples: int
    features: list[str]
    evidence: list[FeatureEvidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def train(df: pd.DataFrame) -> dict:
    """Fit the neighbour index and measure what the data can support."""
    settings = get_settings()

    features = [c for c in COMPARISON_FEATURES if c in df.columns]
    if len(features) < 3:
        raise ValueError(f"need at least 3 comparison features, found {features}")
    if "risk_class" not in df.columns:
        raise ValueError("risk_class is required to identify low-risk neighbours")

    work = df[features + ["risk_class"]].dropna()

    scaler = StandardScaler().fit(work[features].to_numpy(dtype=float))
    X = scaler.transform(work[features].to_numpy(dtype=float))
    y = work["risk_class"].to_numpy(dtype=object)

    # Measure, here and now, which features carry risk information.
    mi = mutual_info_classif(X, y, random_state=RANDOM_STATE)
    mi_by_feature = {f: float(v) for f, v in zip(features, mi)}

    evidence: list[FeatureEvidence] = []
    for feature in features:
        info = mi_by_feature[feature]
        meta = FEATURE_ACTIONABILITY.get(
            feature, {"direction": "none", "actionable": False}
        )
        grounded = bool(info >= MI_RELEVANCE_THRESHOLD and meta["actionable"])

        if not meta["actionable"]:
            note = "not something this app should advise changing"
        elif grounded:
            note = (
                f"mutual information {info:.4f} with the risk label, so a "
                "population comparison on this feature is supported"
            )
        else:
            note = (
                f"mutual information only {info:.4f} with the risk label in "
                "this dataset, so no population-grounded claim can be made; "
                "personal-baseline advice only"
            )

        evidence.append(
            FeatureEvidence(
                feature=feature,
                mutual_information=round(info, 5),
                population_grounded=grounded,
                direction=meta["direction"],
                actionable=bool(meta["actionable"]),
                note=note,
            )
        )

    n_neighbours = min(N_NEIGHBOURS, len(X) - 1)
    index = NearestNeighbors(n_neighbors=n_neighbours, algorithm="auto").fit(X)

    settings.ensure_directories()
    artifact_path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    joblib.dump(
        {
            "scaler": scaler,
            "index": index,
            "feature_columns": features,
            # Stored so the recommender can read neighbour values and labels
            # without reloading the CSV.
            "reference_values": work[features].to_numpy(dtype=float),
            "reference_labels": y,
            "mutual_information": mi_by_feature,
            "evidence": {
                e.feature: {
                    "mutual_information": e.mutual_information,
                    "population_grounded": e.population_grounded,
                    "direction": e.direction,
                    "actionable": e.actionable,
                    "note": e.note,
                }
                for e in evidence
            },
            "n_neighbours": n_neighbours,
            "model_name": "KNeighbors",
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "n_training_rows": int(len(work)),
        },
        artifact_path,
    )

    grounded_features = [e.feature for e in evidence if e.population_grounded]
    ungrounded = [
        e.feature for e in evidence if e.actionable and not e.population_grounded
    ]

    report = {
        "task": "recommendations",
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_samples": int(len(work)),
        "features": features,
        "n_neighbours": n_neighbours,
        "mutual_information": {k: round(v, 5) for k, v in mi_by_feature.items()},
        "population_grounded_features": grounded_features,
        "personal_only_features": ungrounded,
        "evidence": {e.feature: e.note for e in evidence},
        "claim_policy": {
            "allowed": (
                "Associational comparisons: 'people with similar usage who are "
                "classified lower-risk typically have X lower by Y'."
            ),
            "forbidden": (
                "Causal or outcome claims: 'reducing X will lower your risk', or "
                "any statement about what happened to people after they changed "
                "behaviour. This dataset is cross-sectional - nobody was followed "
                "over time, so it contains no outcomes."
            ),
        },
        "comparison_group": (
            "The low-risk subset of the user's nearest neighbours. Comparing "
            "against all neighbours would only describe people like the user; "
            "the informative contrast is with similar people who are "
            "nonetheless classified lower-risk."
        ),
        "artifact": artifact_path.name,
    }

    metrics_path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report
