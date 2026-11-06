"""Behavioural clustering: K-Means, DBSCAN and BIRCH.

WHAT IS CLUSTERED
-----------------
The 7,500-person population dataset, one row per person. Clusters therefore
describe TYPES OF PEOPLE, which is what a "persona" means.

HOW PERSONA NAMES ARE PRODUCED
------------------------------
Names are DERIVED from each cluster's actual centroid, never hand-assigned.
The procedure is:

  1. Standardise the features, so "high" means high relative to the
     population rather than relative to the unit of measurement.
  2. For each cluster, find which features sit furthest from the population
     average (in standard deviations).
  3. Build the name from those features using a fixed, documented vocabulary.

A consequence worth stating plainly: the brief's example persona
"late-night scroller" CANNOT be produced from this dataset, because the data
contains no late-night column at all. Inventing that persona here would mean
labelling a cluster with a behaviour the data never measured. The late-night
dimension is available only in the user's own records, which the anomaly
module uses.

MODEL COMPARISON
----------------
The three algorithms answer slightly different questions, so they are not
scored on a single "accuracy":

  K-Means  partitions everyone into exactly k groups. k is chosen by
           silhouette score, not assumed.
  DBSCAN   finds dense regions and labels the rest as noise, so it can say
           "this person belongs to no group" - which K-Means cannot.
  BIRCH    builds a hierarchy, which scales well and often agrees with
           K-Means; a disagreement is informative.

Silhouette, Calinski-Harabasz and Davies-Bouldin are reported for each, plus
the noise fraction for DBSCAN.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, Birch, KMeans
from sklearn.metrics import (
    calinski_harabasz_score,
    davies_bouldin_score,
    silhouette_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import get_settings

RANDOM_STATE = 42
ARTIFACT_NAME = "clustering_personas"

#: Features used for clustering. Unlike the classifier, the low-information
#: columns are KEPT here: they carry no signal about the risk label but they
#: do describe behaviour, which is exactly what clustering is for.
CLUSTER_FEATURES = [
    "daily_screen_time_hours",
    "social_media_hours",
    "gaming_hours",
    "work_study_hours",
    "sleep_hours",
    "notifications_per_day",
    "app_opens_per_day",
]

#: How many standard deviations from the population mean counts as a defining
#: trait of a cluster. Below this the cluster is described as "balanced".
TRAIT_THRESHOLD = 0.45

#: Documented vocabulary: feature -> (phrase when high, phrase when low).
TRAIT_VOCABULARY: dict[str, tuple[str, str]] = {
    "daily_screen_time_hours": ("heavy overall use", "light overall use"),
    "social_media_hours": ("social-media focused", "little social media"),
    "gaming_hours": ("gaming focused", "little gaming"),
    "work_study_hours": ("work and study focused", "little work or study use"),
    "sleep_hours": ("long sleep", "short sleep"),
    "notifications_per_day": ("notification heavy", "few notifications"),
    "app_opens_per_day": ("frequent checking", "infrequent checking"),
}

K_RANGE = range(2, 7)

#: Silhouette bands used to judge whether clusters mean anything.
#: These are the conventional interpretation thresholds.
SILHOUETTE_STRONG = 0.50
SILHOUETTE_MODERATE = 0.25
SILHOUETTE_WEAK = 0.10


def choose_dbscan_eps(X: np.ndarray, min_samples: int, percentile: float = 90.0) -> tuple[float, dict]:
    """Choose DBSCAN's eps from the data instead of guessing it.

    Standard heuristic: look at each point's distance to its `min_samples`-th
    nearest neighbour, sort those distances, and take eps near the "knee".
    A fixed guess fails badly as dimensionality changes - an eps of 0.9 that
    works in 2-D labels every point as noise in 7-D, because distances grow
    with dimension.

    Returns (eps, diagnostics).
    """
    from sklearn.neighbors import NearestNeighbors

    k = min(min_samples, max(2, len(X) - 1))
    neighbours = NearestNeighbors(n_neighbors=k).fit(X)
    distances, _ = neighbours.kneighbors(X)
    kth = np.sort(distances[:, -1])

    eps = float(np.percentile(kth, percentile))

    return eps, {
        "method": f"{percentile:.0f}th percentile of distance to the {k}th nearest neighbour",
        "k": int(k),
        "eps": eps,
        "kth_distance_min": float(kth.min()),
        "kth_distance_median": float(np.median(kth)),
        "kth_distance_max": float(kth.max()),
    }


def assess_structure(silhouette: float | None) -> dict:
    """State plainly whether the clustering found real structure.

    This exists because K-Means ALWAYS returns k clusters, whether or not the
    data contains any. Reporting cluster centroids without saying how well
    separated they are would imply structure that may not exist.
    """
    if silhouette is None:
        return {
            "verdict": "unmeasurable",
            "detail": "Too few clusters with members to compute a silhouette score.",
        }

    if silhouette >= SILHOUETTE_STRONG:
        verdict, detail = "strong", "Clusters are well separated and clearly distinct."
    elif silhouette >= SILHOUETTE_MODERATE:
        verdict, detail = (
            "moderate",
            "Clusters are discernible but overlap substantially.",
        )
    elif silhouette >= SILHOUETTE_WEAK:
        verdict, detail = (
            "weak",
            "Barely any separation. The algorithm has partitioned a largely "
            "continuous cloud of points, so these groups are convenient "
            "divisions rather than naturally occurring types.",
        )
    else:
        verdict, detail = (
            "none",
            "No meaningful cluster structure. Any grouping reported is an "
            "artefact of the algorithm, not a property of the data.",
        )

    return {"verdict": verdict, "silhouette": round(silhouette, 4), "detail": detail}


@dataclass
class ClusterProfile:
    """One cluster, described in terms a person could read."""

    cluster_id: int
    size: int
    share: float
    name: str
    #: Trait phrases that produced the name.
    traits: list[str] = field(default_factory=list)
    #: Feature -> mean in original units, for display.
    centroid_raw: dict[str, float] = field(default_factory=dict)
    #: Feature -> mean in standard deviations from the population average.
    centroid_z: dict[str, float] = field(default_factory=dict)


def _name_cluster(centroid_z: dict[str, float]) -> tuple[str, list[str]]:
    """Turn a standardised centroid into a persona name and its traits.

    Returns (name, traits). A cluster with no feature beyond the threshold is
    genuinely unremarkable and is named "Balanced user" - which is an honest
    description, not a fallback.
    """
    ranked = sorted(centroid_z.items(), key=lambda kv: -abs(kv[1]))
    traits: list[str] = []

    for feature, z in ranked:
        if abs(z) < TRAIT_THRESHOLD:
            continue
        high_phrase, low_phrase = TRAIT_VOCABULARY.get(feature, (feature, feature))
        traits.append(high_phrase if z > 0 else low_phrase)
        if len(traits) == 2:
            break

    if not traits:
        return "Balanced user", []

    # Build a readable name from the strongest one or two traits.
    name = traits[0].capitalize()
    if len(traits) > 1:
        name = f"{traits[0].capitalize()}, {traits[1]}"
    return name, traits


def _describe_clusters(
    df: pd.DataFrame,
    labels: np.ndarray,
    features: list[str],
) -> list[ClusterProfile]:
    """Build a readable profile for every cluster found."""
    population_mean = df[features].mean()
    population_std = df[features].std().replace(0, np.nan)

    profiles: list[ClusterProfile] = []
    total = len(labels)

    for cluster_id in sorted(set(labels.tolist())):
        mask = labels == cluster_id
        subset = df.loc[mask, features]
        if subset.empty:
            continue

        raw = subset.mean()
        z = ((raw - population_mean) / population_std).fillna(0.0)

        centroid_z = {f: float(z[f]) for f in features}

        if cluster_id == -1:
            # DBSCAN's noise label. Naming it as a persona would be wrong -
            # these are people who fit no dense group.
            name, traits = "Unclustered (no dense group)", []
        else:
            name, traits = _name_cluster(centroid_z)

        profiles.append(
            ClusterProfile(
                cluster_id=int(cluster_id),
                size=int(mask.sum()),
                share=float(mask.sum() / total),
                name=name,
                traits=traits,
                centroid_raw={f: round(float(raw[f]), 3) for f in features},
                centroid_z={f: round(v, 3) for f, v in centroid_z.items()},
            )
        )

    return profiles


def _internal_scores(X: np.ndarray, labels: np.ndarray) -> dict[str, float | None]:
    """Compute clustering quality scores, excluding DBSCAN noise points.

    All three need at least two clusters with real members; they return None
    rather than a fabricated number when that does not hold.
    """
    mask = labels != -1
    X_eval, labels_eval = X[mask], labels[mask]

    n_clusters = len(set(labels_eval.tolist()))
    if n_clusters < 2 or len(X_eval) < 3:
        return {
            "silhouette": None,
            "calinski_harabasz": None,
            "davies_bouldin": None,
            "note": "needs at least 2 non-noise clusters to score",
        }

    return {
        "silhouette": float(silhouette_score(X_eval, labels_eval)),
        "calinski_harabasz": float(calinski_harabasz_score(X_eval, labels_eval)),
        "davies_bouldin": float(davies_bouldin_score(X_eval, labels_eval)),
        "note": None,
    }


def choose_k(X: np.ndarray) -> tuple[int, list[dict]]:
    """Pick k for K-Means by silhouette score, reporting every candidate.

    k is SELECTED, not assumed. The full sweep is returned so the choice is
    auditable rather than asserted.
    """
    sweep: list[dict] = []
    best_k, best_score = 2, -1.0

    for k in K_RANGE:
        model = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
        labels = model.fit_predict(X)
        score = float(silhouette_score(X, labels))
        sweep.append(
            {
                "k": k,
                "silhouette": score,
                "inertia": float(model.inertia_),
            }
        )
        if score > best_score:
            best_k, best_score = k, score

    return best_k, sweep


def train(df: pd.DataFrame) -> dict:
    """Fit all three clustering algorithms, derive personas, save artifacts."""
    settings = get_settings()

    features = [c for c in CLUSTER_FEATURES if c in df.columns]
    missing = set(CLUSTER_FEATURES) - set(features)
    if not features:
        raise ValueError("none of the clustering features are present")

    work = df[features].dropna()
    scaler = StandardScaler().fit(work.to_numpy(dtype=float))
    X = scaler.transform(work.to_numpy(dtype=float))

    best_k, k_sweep = choose_k(X)

    # min_samples follows the common 2*dimensions heuristic; eps is then
    # derived from the data rather than guessed (a fixed eps that suits 2-D
    # labels everything as noise in 7-D).
    dbscan_min_samples = 2 * len(features)
    dbscan_eps, eps_diagnostics = choose_dbscan_eps(X, dbscan_min_samples)

    algorithms = {
        "KMeans": KMeans(n_clusters=best_k, random_state=RANDOM_STATE, n_init=10),
        "DBSCAN": DBSCAN(eps=dbscan_eps, min_samples=dbscan_min_samples),
        "BIRCH": Birch(n_clusters=best_k, threshold=0.5),
    }

    results: dict[str, dict] = {}
    labels_by_algorithm: dict[str, np.ndarray] = {}

    for name, model in algorithms.items():
        labels = np.asarray(model.fit_predict(X))
        labels_by_algorithm[name] = labels

        scores = _internal_scores(X, labels)
        profiles = _describe_clusters(work.assign(**{}), labels, features)

        n_noise = int((labels == -1).sum())
        results[name] = {
            "n_clusters": len([c for c in set(labels.tolist()) if c != -1]),
            "n_noise": n_noise,
            "noise_share": float(n_noise / len(labels)),
            "scores": scores,
            # Whether the groups mean anything, stated explicitly.
            "structure": assess_structure(scores["silhouette"]),
            "personas": [
                {
                    "cluster_id": p.cluster_id,
                    "name": p.name,
                    "size": p.size,
                    "share": round(p.share, 4),
                    "traits": p.traits,
                    "centroid_raw": p.centroid_raw,
                    "centroid_z": p.centroid_z,
                }
                for p in profiles
            ],
        }

    # K-Means is the model deployed for assigning a persona to the user: it
    # always returns a cluster, which DBSCAN cannot guarantee, and a user who
    # gets "no persona" is a poor experience when a nearest group exists.
    deployed = "KMeans"
    final_pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("model", KMeans(n_clusters=best_k, random_state=RANDOM_STATE, n_init=10)),
        ]
    )
    final_pipeline.fit(work.to_numpy(dtype=float))

    settings.ensure_directories()
    artifact_path = settings.artifacts_dir / f"{ARTIFACT_NAME}.joblib"
    joblib.dump(
        {
            "pipeline": final_pipeline,
            "feature_columns": features,
            "k": best_k,
            "personas": results[deployed]["personas"],
            "model_name": deployed,
            "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
            "n_training_rows": int(len(work)),
        },
        artifact_path,
    )

    # Agreement between KMeans and BIRCH, as a stability check. Measured with
    # adjusted Rand index, which corrects for chance agreement.
    from sklearn.metrics import adjusted_rand_score

    agreement = float(
        adjusted_rand_score(labels_by_algorithm["KMeans"], labels_by_algorithm["BIRCH"])
    )

    report = {
        "task": "clustering",
        "trained_at": dt.datetime.now().isoformat(timespec="seconds"),
        "n_samples": int(len(work)),
        "features": features,
        "features_unavailable": sorted(missing),
        "late_night_note": (
            "This dataset contains no late-night usage column, so a "
            "'late-night scroller' persona cannot be derived from it. Naming "
            "one here would mean labelling a cluster with a behaviour the data "
            "never measured. Late-night behaviour is handled by the personal "
            "baseline module, which uses the user's own records."
        ),
        "k_selection": {
            "chosen_k": best_k,
            "method": "highest silhouette score over k = 2..6",
            "sweep": k_sweep,
            # If every k scores about the same, the "best" k is arbitrary and
            # that must be said rather than presenting it as a finding.
            "silhouette_spread": round(
                max(s["silhouette"] for s in k_sweep)
                - min(s["silhouette"] for s in k_sweep),
                4,
            ),
            "selection_is_meaningful": bool(
                max(s["silhouette"] for s in k_sweep)
                - min(s["silhouette"] for s in k_sweep)
                > 0.05
            ),
        },
        "dbscan_eps_selection": eps_diagnostics,
        "overall_structure": assess_structure(
            results["KMeans"]["scores"]["silhouette"]
        ),
        "deployed_model": deployed,
        "deployed_reason": (
            "K-Means always assigns a cluster. DBSCAN can label a person as "
            "noise, which is informative for analysis but unhelpful when the "
            "app needs to show the user their closest persona."
        ),
        "kmeans_birch_agreement_ari": agreement,
        "algorithms": results,
        "artifact": artifact_path.name,
    }

    metrics_path = settings.artifacts_dir / f"{ARTIFACT_NAME}_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report
