"""Analysis logic, with no web framework in sight.

WHY THIS MODULE EXISTS
======================
This was the body of app/api/ml.py. Everything here - which scope to analyse,
when a model may not speak, which caveats a result carries - is application
logic that has nothing to do with HTTP. It lived inside FastAPI handlers only
because that is where it was first written.

Separating it is what lets the SAME code serve two very different callers:

  the laptop API, where app/api/ml.py is now a thin layer that catches the
  exceptions below and turns them into status codes;

  the phone, where app/mobile_api.py calls these functions directly through
  the Kotlin bridge - no sockets, no ports, no server.

The alternative was a second implementation for the device, which would have
meant two copies of every refusal rule and every caveat, drifting apart the
first time one was fixed.

REFUSALS ARE EXCEPTIONS, NOT STATUS CODES
=========================================
A model that cannot honestly answer raises NotReady with the reason. The
caller decides how to express that: HTTP 409 on the laptop, a structured
error object on the phone. The reason text is identical either way, because
it is written once, here.
"""

from __future__ import annotations

import math
from typing import Literal

import pandas as pd
from sqlalchemy.orm import Session

from app.database.models import DataSource
from app.ml.anomaly_detection import personal_baseline as pb
from app.ml.classification import bayesian_confidence as bayes_conf
from app.ml.classification import inference as clf_inference
from app.ml.clustering import inference as cluster_inference
from app.ml.preprocessing.feature_engineering import build_personal_features
from app.ml.regression import inference as reg_inference
from app.ml.regression.train_forecast import drop_incomplete_today
from app.ml.recommendations import inference as rec_inference
from app.ml.risk_scoring import inference as score_inference
from app.services import usage_service


class NotReady(Exception):
    """A model cannot answer yet, and the reason is worth stating.

    Carries exactly the text the user sees. Every refusal in this project is
    specific - which measurement is missing, on how many days, what would make
    it available - and that specificity is the whole point, so it travels with
    the exception rather than being reconstructed by the caller.
    """


class NotTrained(NotReady):
    """A model artifact is absent. A kind of NotReady with a different remedy."""

#: Which provenances the analysis runs on.
#:
#: "device"    only data measured on the phone - the honest default once any
#:             device data exists, because the user asked the app to work from
#:             their own phone.
#: "real"      device + manual + csv, i.e. everything except generated demo data.
#: "all"       everything, including synthetic demonstration rows.
AnalysisScope = Literal["device", "real", "all"]


def _sources_for(scope: AnalysisScope) -> list[DataSource] | None:
    if scope == "device":
        return [DataSource.DEVICE]
    if scope == "real":
        return [DataSource.DEVICE, DataSource.MANUAL, DataSource.CSV]
    return None  # all


def _daily_frame(
    db: Session,
    scope: AnalysisScope = "all",
) -> pd.DataFrame:
    """Pull the user's daily history into a DataFrame.

    `scope` decides which provenances count. Keeping demonstration rows in the
    database while excluding them from analysis means the project stays
    demonstrable without the models being fitted on invented data.

    THE DAY IN PROGRESS IS EXCLUDED FROM EVERY MODEL
    ------------------------------------------------
    Today's totals are still accumulating, so the row is a day-so-far. Every
    model downstream treats a row as a day, and each one is wrong in its own
    way if handed a morning:

      the forecaster puts it at lag_1, where it has the most influence of any
      feature - a 7-minute morning pulled the next day from 8.7 h to 5.0 h;

      the classifier and the persona average it in, dragging the mean down and
      shifting the result toward a lighter user than the person is;

      anomaly detection would flag the day in progress as unusually quiet,
      every morning, forever.

    One rule in one place, rather than four partial fixes. The Home and Usage
    screens still show today's figure, where it is correctly presented as the
    day so far - this guards the models only.

    The excluded total is recorded in `.attrs` so endpoints can say what was
    set aside instead of appearing to lose a day.
    """
    records = usage_service.list_records(
        db, limit=2000, sources=_sources_for(scope)
    )
    frame = pd.DataFrame(
        [
            {
                "record_date": r.record_date,
                "total_screen_minutes": r.total_screen_minutes,
                "social_minutes": r.social_minutes,
                "gaming_minutes": r.gaming_minutes,
                "productivity_minutes": r.productivity_minutes,
                "app_launches": r.app_launches,
                "notification_count": r.notification_count,
                "late_night_minutes": r.late_night_minutes,
                "sleep_hours": r.sleep_hours,
            }
            for r in records
        ]
    )

    frame, excluded = drop_incomplete_today(frame)
    frame.attrs["excluded_today_minutes"] = excluded
    return frame


def _today_note(daily: pd.DataFrame) -> str | None:
    """A caveat naming the day that was held back, if one was."""
    excluded = daily.attrs.get("excluded_today_minutes")
    if excluded is None:
        return None
    return (
        f"Today is not included: it held {excluded:.0f} minutes when this was "
        "calculated and is still accumulating, so counting it as a day would "
        "understate your usage. It joins the analysis tomorrow."
    )


def ml_status() -> dict:
    """Report the real state of every model. Nothing is implied or assumed."""
    reg_metrics = reg_inference.load_metrics()
    clf_metrics = clf_inference.load_metrics()

    return {
        "regression": {
            "trained": reg_inference.is_trained(),
            "model": reg_metrics.get("best_model") if reg_metrics else None,
            "trained_at": reg_metrics.get("trained_at") if reg_metrics else None,
            "n_samples": reg_metrics.get("n_samples") if reg_metrics else None,
            "sample_size_warning": (
                reg_metrics.get("sample_size_warning") if reg_metrics else None
            ),
        },
        "classification": {
            "trained": clf_inference.is_trained(),
            "model": clf_metrics.get("best_model") if clf_metrics else None,
            "trained_at": clf_metrics.get("trained_at") if clf_metrics else None,
            "n_samples": clf_metrics.get("n_samples_total") if clf_metrics else None,
        },
        "how_to_train": "python scripts/train_models.py",
    }


def model_performance() -> dict:
    """Serve the metrics JSON written at training time.

    These figures are READ from disk, not recomputed, so the app always shows
    what the shipped models actually scored.
    """
    regression = reg_inference.load_metrics()
    classification = clf_inference.load_metrics()

    if regression is None and classification is None:
        raise NotReady((
                "No models have been trained yet. Run: "
                "python scripts/train_models.py"
            ),
        )

    return {
        "regression": regression,
        "classification": classification,
        "note": (
            "All scores were measured at training time on held-out data and "
            "are read from disk, never recomputed in the API."
        ),
    }


def _personal_model_readiness(
    db: Session,
) -> dict:
    """How far the measured history is from supporting a personal forecaster.

    Answers the question the provenance caveat provokes: "then when WILL it be
    about me?". Given in days the user can count, derived from the training
    module's own thresholds so the two cannot drift apart.

    The arithmetic: the model needs MIN_ROWS_TO_TRAIN supervised rows, and the
    first 7 days of any history are consumed building the 7-day lag, so the
    requirement in calendar days is that sum.
    """
    from app.ml.regression.train_forecast import (
        LAG_WARMUP_DAYS,
        MIN_ROWS_FOR_CONFIDENCE,
        MIN_ROWS_TO_TRAIN,
    )

    device_days = len(
        usage_service.list_records(db, limit=2000, sources=[DataSource.DEVICE])
    )
    needed = MIN_ROWS_TO_TRAIN + LAG_WARMUP_DAYS
    comfortable = MIN_ROWS_FOR_CONFIDENCE + LAG_WARMUP_DAYS

    return {
        "measured_days": device_days,
        "days_needed_to_train": needed,
        "days_needed_for_stable_metrics": comfortable,
        "can_train_on_your_data": device_days >= needed,
        "explanation": (
            f"A forecaster fitted on your own days needs {needed} consecutive "
            f"measured days ({MIN_ROWS_TO_TRAIN} usable rows plus "
            f"{LAG_WARMUP_DAYS} consumed by the 7-day lag). You have "
            f"{device_days}."
            if device_days < needed
            else (
                f"You have {device_days} measured days, enough to fit the "
                "forecaster on your own history. Run: python "
                "scripts/train_models.py --only regression --sources device"
            )
        ),
    }


def _provenance_caveat(training_data: dict | None, scope: AnalysisScope) -> str | None:
    """State plainly when a forecast was not learned from the user's own days.

    The forecaster predicts ONE PERSON's screen time, so a model fitted on
    generated demonstration days is demonstrating the method rather than
    describing that person - even when it is being fed their real history at
    prediction time. Without this, a synthetic-fitted model served under
    scope=device looks exactly like a personal forecast.
    """
    if not training_data:
        return (
            "This model does not record which data it was fitted on, so what "
            "its predictions describe cannot be established. Retrain it to "
            "restore that record."
        )

    fitted_on = training_data.get("fitted_on")
    if fitted_on == "real":
        return None

    note = training_data.get("note") or ""

    if scope in ("device", "real"):
        return (
            "Your own days are being fed into a model that was NOT fitted on "
            f"them. {note} The shape of the prediction comes from that "
            "training data, so treat these numbers as a demonstration of the "
            "forecasting method rather than a forecast about you."
        )
    return note or None


def forecast(
    db: Session,
    horizon: int = 7,
    scope: AnalysisScope = "all",
) -> dict:
    """Forecast upcoming daily screen time."""
    if not reg_inference.is_trained():
        raise NotReady((
                "The forecasting model is not trained. Run: "
                "python scripts/train_models.py --only regression"
            ),
        )

    daily = _daily_frame(db, scope)
    if daily.empty:
        raise NotReady((
                f"No usage records in scope '{scope}', so there is no history "
                "to forecast from."
            ),
        )

    try:
        predictions = reg_inference.forecast_next_days(daily, horizon=horizon)
    except reg_inference.ModelNotTrained as exc:
        raise NotReady(str(exc)) from exc

    metrics = reg_inference.load_metrics() or {}

    if not predictions:
        raise NotReady((
                "Not enough consecutive history to populate the model's lag "
                "features. At least 8 consecutive days are required."
            ),
        )

    rmse = metrics.get("models", [{}])[0].get("rmse")

    return {
        "model": metrics.get("best_model"),
        "horizon": len(predictions),
        "week_comparison": _week_comparison(daily, predictions, rmse),
        "predictions": [
            {
                "date": p.date,
                "predicted_minutes": p.predicted_minutes,
                "predicted_hours": round(p.predicted_minutes / 60.0, 2),
                "is_recursive": p.is_recursive,
                "is_current_day": p.is_current_day,
            }
            for p in predictions
        ],
        "accuracy": {
            "rmse_minutes": metrics.get("models", [{}])[0].get("rmse"),
            "cv_strategy": metrics.get("cv_strategy"),
            "naive_baseline_rmse": metrics.get("naive_persistence_rmse"),
        },
        "training_data": metrics.get("training_data"),
        "personal_model": _personal_model_readiness(db),
        "caveats": [
            "Only the first day rests purely on observed history. Later days "
            "use earlier predictions as input, so their error compounds - "
            "these are marked is_recursive.",
            (
                "The day in progress is excluded from the model's inputs, "
                "because a day so far is not a day. The first prediction is "
                "therefore for today as a whole, marked is_current_day - it "
                "will not match the smaller figure the Usage screen shows for "
                "today, and is not meant to."
                if any(p.is_current_day for p in predictions)
                else None
            ),
            _provenance_caveat(metrics.get("training_data"), scope),
            metrics.get("sample_size_warning"),
        ],
    }


def _week_comparison(daily: pd.DataFrame, predictions: list, rmse: float | None) -> dict | None:
    """The week ahead against the last complete week, per day.

    Only stated as a rise or fall when the gap is larger than the model's
    typical error for a 7-day average (daily RMSE / sqrt(7)); otherwise it is
    reported as "about the same", because a smaller gap is noise the model
    cannot distinguish from no change.
    """
    if daily.empty or len(predictions) < 7:
        return None
    last = daily.sort_values("record_date").tail(7)
    if len(last) < 7:
        return None
    last_avg = float(pd.to_numeric(last["total_screen_minutes"]).mean())
    next_avg = float(sum(p.predicted_minutes for p in predictions[:7]) / 7)
    change = next_avg - last_avg
    threshold = (rmse / math.sqrt(7)) if rmse else 0.0
    within = abs(change) < threshold
    if within:
        summary = (
            f"About the same as last week ({last_avg / 60:.1f} h a day); the "
            "difference is within the model's error."
        )
    else:
        direction = "more" if change > 0 else "less"
        summary = (
            f"Projected {abs(change) / 60:.1f} h a day {direction} than last week "
            f"({last_avg / 60:.1f} h) - about {abs(change) * 7 / 60:.1f} h {direction} "
            "over the week."
        )
    return {
        "last_week_avg_minutes": round(last_avg, 1),
        "next_week_avg_minutes": round(next_avg, 1),
        "change_minutes_per_day": round(change, 1),
        "noise_threshold_minutes": round(threshold, 1),
        "within_model_error": within,
        "summary": summary,
    }


def actual_vs_predicted(
    db: Session,
    scope: AnalysisScope = "all",
) -> dict:
    """Return one-step-ahead predictions alongside the actual values."""
    if not reg_inference.is_trained():
        raise NotReady("The forecasting model is not trained.",
        )

    daily = _daily_frame(db, scope)
    if daily.empty:
        raise NotReady(f"No usage records in scope '{scope}'.",
        )

    series = reg_inference.actual_vs_predicted(daily)
    return {
        "series": series,
        "n_points": len(series),
        "note": (
            "These predictions are IN-SAMPLE: the deployed model was refitted "
            "on all available data, so this chart flatters the model. The "
            "honest out-of-sample error is the cross-validated RMSE shown on "
            "the model performance screen."
        ),
    }


def risk_classification(
    db: Session,
    scope: AnalysisScope = "all",
) -> dict:
    """Classify this user's typical behaviour into Low / Medium / High risk."""
    if not clf_inference.is_trained():
        raise NotReady((
                "The risk classifier is not trained. Run: "
                "python scripts/train_models.py --only classification"
            ),
        )

    daily = _daily_frame(db, scope)

    try:
        prediction = clf_inference.predict_from_personal_history(daily)
    except clf_inference.MissingFeatures as exc:
        # A missing measurement, not a missing day. Named separately so the
        # message does not read "7 recorded, 7 needed" while refusing.
        raise NotReady(str(exc)
        ) from exc
    except clf_inference.InsufficientHistory as exc:
        # 409, not 500: this is an expected state with a clear user meaning.
        raise NotReady((
                f"Not enough history to classify yet - {exc.have} day(s) "
                f"recorded, {exc.need} needed."
            ),
        ) from exc
    except clf_inference.ModelNotTrained as exc:
        raise NotReady(str(exc)) from exc

    metrics = clf_inference.load_metrics() or {}
    best = next(
        (m for m in metrics.get("models", []) if m["name"] == prediction.model_name),
        {},
    )

    return {
        "risk_class": prediction.risk_class,
        "probabilities": prediction.probabilities,
        "confidence": prediction.confidence,
        "model": prediction.model_name,
        "days_averaged": prediction.days_averaged,
        "model_inputs": prediction.inputs,
        "model_accuracy": {
            "cv_accuracy": best.get("cv_accuracy"),
            "cv_f1_macro": best.get("cv_f1_macro"),
            "test_accuracy": (best.get("test") or {}).get("accuracy"),
            "roc_auc_ovr": (best.get("test") or {}).get("roc_auc_ovr"),
            "majority_baseline": metrics.get("majority_baseline"),
        },
        "caveats": [
            "The classifier was trained on cross-sectional population data "
            "where each row is a different person. Your daily records are "
            "averaged into a comparable 'typical day' before classification.",
            _today_note(daily),
            metrics.get("expected_ceiling_note"),
        ],
    }


def risk_confidence(
    db: Session,
    scope: AnalysisScope = "all",
    weeks: int = bayes_conf.DEFAULT_MAX_WEEKS,
) -> dict:
    """Bayesian confidence in the risk class.

    Each recent week is classified on its own and the soft results are
    combined with a Dirichlet prior - see ml/classification/bayesian_confidence.py.
    Uses the same scope and the same day-in-progress rule as every other model.
    """
    if not clf_inference.is_trained():
        raise NotTrained(
            "The risk classifier is not trained. Run: "
            "python scripts/train_models.py --only classification"
        )

    records = usage_service.list_records(db, limit=2000, sources=_sources_for(scope))
    frame = pd.DataFrame(
        [
            {
                "record_date": r.record_date,
                "total_screen_minutes": r.total_screen_minutes,
                "social_minutes": r.social_minutes,
                "gaming_minutes": r.gaming_minutes,
                "productivity_minutes": r.productivity_minutes,
                "app_launches": r.app_launches,
                "notification_count": r.notification_count,
                "late_night_minutes": r.late_night_minutes,
                "sleep_hours": r.sleep_hours,
                "source": r.source.value,
            }
            for r in records
        ]
    )
    frame, excluded = drop_incomplete_today(frame)
    frame.attrs["excluded_today_minutes"] = excluded

    try:
        result = bayes_conf.estimate(frame, max_weeks=max(bayes_conf.MIN_WEEKS, min(weeks, 52)))
    except bayes_conf.InsufficientWeeks as exc:
        blocker = (
            f" Weeks were set aside because of missing measurements: {', '.join(exc.missing)}."
            if exc.missing
            else ""
        )
        raise NotReady(
            f"Not enough complete weeks yet - {exc.have} usable, {exc.need} needed. "
            f"A week counts once it has at least {bayes_conf.MIN_DAYS_PER_WEEK} recorded days."
            + blocker
        ) from exc

    return {
        "classes": result.classes,
        "n_weeks": len(result.weeks),
        "most_likely_class": result.most_likely_class,
        "verdict": result.verdict,
        "plain_language": result.plain_language,
        "prob_most_common": result.prob_most_common,
        "prob_elevated_majority": result.prob_elevated_majority,
        "share_mean": result.share_mean,
        "share_interval": {k: list(v) for k, v in result.share_interval.items()},
        "credible_mass": bayes_conf.CREDIBLE_MASS,
        "prior": "Dirichlet(1, 1, 1) - uniform over the three classes",
        "posterior_alpha": result.posterior_alpha,
        "weeks": [
            {
                "start": w.start,
                "end": w.end,
                "days": w.days,
                "predicted": w.predicted,
                "probabilities": w.probabilities,
                "synthetic_days": w.synthetic_days,
            }
            for w in result.weeks
        ],
        "caveats": [*result.caveats, _today_note(frame)],
    }


# ===========================================================================
# PHASE 6: clustering personas and personal-baseline anomaly detection
# ===========================================================================


def persona(
    db: Session,
    scope: AnalysisScope = "all",
) -> dict:
    """Assign the user to their nearest persona.

    The response always carries the measured cluster-structure verdict. On
    this dataset that verdict is "weak", so the app must present the result
    as a closest match rather than as a discovered behavioural type.
    """
    if not cluster_inference.is_trained():
        raise NotReady((
                "Clustering has not been trained. Run: "
                "python scripts/train_clusters_and_score_anomalies.py"
            ),
        )

    daily = _daily_frame(db, scope)

    try:
        assignment = cluster_inference.assign_persona(daily)
    except clf_inference.MissingFeatures as exc:
        raise NotReady(str(exc)) from exc
    except clf_inference.InsufficientHistory as exc:
        raise NotReady((
                f"Not enough history to assign a persona - {exc.have} day(s) "
                f"recorded, {exc.need} needed."
            ),
        ) from exc
    except cluster_inference.ModelNotTrained as exc:
        raise NotReady(str(exc)) from exc

    close_run = (
        assignment.distance_to_next is not None
        and assignment.distance_to_assigned > 0
        and (assignment.distance_to_next - assignment.distance_to_assigned)
        / assignment.distance_to_assigned
        < 0.25
    )

    return {
        "cluster_id": assignment.cluster_id,
        "name": assignment.name,
        "traits": assignment.traits,
        "population_share": assignment.population_share,
        "days_averaged": assignment.days_averaged,
        "model_inputs": assignment.inputs,
        "distance_to_assigned": assignment.distance_to_assigned,
        "distance_to_next": assignment.distance_to_next,
        "assignment_was_close_run": close_run,
        "reliability": {
            "structure": assignment.structure_verdict,
            "detail": assignment.structure_detail,
        },
        "caveats": [
            "Persona names are derived from each cluster's measured centroid, "
            "not chosen by hand.",
            (
                "Cluster structure in this dataset is weak, so this is your "
                "CLOSEST group rather than a naturally distinct type."
                if assignment.structure_verdict in {"weak", "none"}
                else None
            ),
            _today_note(daily),
        ],
    }


def clustering_report() -> dict:
    """Serve the clustering metrics recorded at training time."""
    metrics = cluster_inference.load_metrics()
    if metrics is None:
        raise NotReady((
                "Clustering has not been trained. Run: "
                "python scripts/train_clusters_and_score_anomalies.py"
            ),
        )
    return metrics


def anomalies(
    db: Session,
    limit: int = 60,
    only_anomalous: bool = False,
    scope: AnalysisScope = "all",
) -> dict:
    """Assess each day against this user's OWN baseline.

    Nothing here compares the user to anyone else: a habitually heavy user has
    a heavy baseline, and a heavy day is normal for them.

    Requires enough history first - the API refuses rather than guessing.
    """
    # Routed through _daily_frame rather than assembling the same columns
    # again here. This endpoint used to build its own frame, which meant it
    # quietly opted out of the shared rule excluding the day in progress -
    # visible as a refusal that said "you have 8" while every other model was
    # working from 7. Worse, had there been enough history, it would have
    # assessed the morning as an unusually quiet day, every morning.
    raw = _daily_frame(db, scope)
    if raw.empty:
        held_back = raw.attrs.get("excluded_today_minutes")
        raise NotReady((
                "Today is the only day on record, and a day still in progress "
                "cannot be assessed against a baseline. Come back tomorrow, "
                "once it is a complete day."
                if held_back is not None
                else f"No usage records in scope '{scope}', so there is no "
                "baseline to compare against."
            ),
        )

    features, _ = build_personal_features(raw)

    try:
        assessments = pb.assess_history(features)
    except clf_inference.MissingFeatures as exc:
        raise NotReady(str(exc)
        ) from exc
    except pb.InsufficientHistory as exc:
        raise NotReady((
                f"A reliable personal baseline needs {exc.need} days of "
                f"history; you have {exc.have}."
            ),
        ) from exc

    selected = [a for a in assessments if a.is_anomalous] if only_anomalous else assessments
    # Newest first, then trim.
    selected = sorted(selected, key=lambda a: a.date, reverse=True)[:limit]

    severity_counts: dict[str, int] = {}
    for a in assessments:
        severity_counts[a.severity] = severity_counts.get(a.severity, 0) + 1

    return {
        "note_today": _today_note(raw),
        "days_assessed": len(assessments),
        "days_flagged": sum(1 for a in assessments if a.is_anomalous),
        "severity_counts": severity_counts,
        "combination_only_days": sum(
            1 for a in assessments if a.unusual_combination and not a.contributing()
        ),
        "features_checked": [pb.FEATURE_LABELS.get(f, f) for f in pb.BASELINE_FEATURES],
        "combination_method": (
            "Mahalanobis distance of the day from your own history across all "
            "measures at once, accounting for how they normally move together, "
            "with a chi-squared p-value. A day can be unusual as a combination "
            "even when no single measure is extreme."
        ),
        "baseline_method": (
            "Median and scaled Median Absolute Deviation over the user's own "
            "history, computed leave-one-out so a day never contributes to the "
            "baseline it is judged against. Robust statistics are used because "
            "mean and standard deviation are themselves distorted by the "
            "anomalies being looked for."
        ),
        "days": [
            {
                "date": a.date,
                "is_anomalous": a.is_anomalous,
                "severity": a.severity,
                "max_abs_z": a.max_abs_z,
                "unusual_combination": a.unusual_combination,
                "mahalanobis_distance": a.mahalanobis_distance,
                "mahalanobis_p": a.mahalanobis_p,
                "explanation": a.explanation,
                "contributing_features": [
                    {
                        "feature": d.feature,
                        "label": d.label,
                        "value": round(d.value, 2),
                        "your_usual": round(d.baseline, 2),
                        "robust_z": d.robust_z,
                        "direction": d.direction,
                        "explanation": d.explain(),
                    }
                    for d in a.contributing()
                ],
            }
            for a in selected
        ],
    }


# ===========================================================================
# PHASE 7: PCA risk score and Bayesian confidence
# ===========================================================================


def risk_score(
    db: Session,
    scope: AnalysisScope = "all",
) -> dict:
    """The 0-100 composite score, its history, and a Bayesian credible interval.

    IMPORTANT: the response always carries `validation.validated`. On this
    dataset the PCA score did NOT validate (Spearman rho 0.18; mean score for
    Medium 53.4 vs High 54.4 - under one point apart), so the app must not
    present the band as an authoritative risk verdict. The classifier, which
    reached ROC-AUC 0.80, is the better-supported risk signal and is served by
    /api/ml/risk-classification.
    """
    if not score_inference.is_trained():
        raise NotReady((
                "The risk score has not been trained. Run: "
                "python scripts/train_risk_score.py"
            ),
        )

    daily = _daily_frame(db, scope)
    if daily.empty:
        raise NotReady(f"No usage records in scope '{scope}', so there is nothing to score.",
        )

    try:
        result = score_inference.score_with_confidence(daily)
    except clf_inference.MissingFeatures as exc:
        raise NotReady(str(exc)) from exc
    except score_inference.InsufficientHistory as exc:
        raise NotReady((
                f"Not enough complete days to score - {exc.have} usable, "
                f"{exc.need} needed."
            ),
        ) from exc
    except score_inference.ModelNotTrained as exc:
        raise NotReady(str(exc)) from exc

    metrics = score_inference.load_metrics() or {}
    validation = metrics.get("validation", {})
    is_validated = bool(validation.get("validated"))

    result["validation"] = {
        "validated": is_validated,
        "spearman_rho": validation.get("spearman_rho"),
        "mean_score_per_class": validation.get("mean_score_per_class"),
        "monotonic": validation.get("monotonic_low_medium_high"),
        "low_to_high_separation": validation.get("low_to_high_separation"),
    }
    result["suitable_as_headline_risk"] = is_validated
    # Nulls are filtered rather than emitted: this response's declared type
    # is string[], unlike the forecast's (string | null)[].
    result["caveats"] = [
        c
        for c in [
            _today_note(daily),
            (
                "This score did NOT pass validation against the dataset's own "
                "risk labels: it separates Low from the rest but barely "
                "distinguishes Medium from High. Treat it as a behaviour "
                "index, not a risk verdict."
                if not is_validated
                else "This score was validated against the dataset's risk labels."
            ),
            (
                "PCA explained variance was almost uniform across components "
                f"({metrics.get('n_components_retained')} of "
                f"{len(metrics.get('features', []))} needed for 80%), because "
                "the dataset's features are mutually uncorrelated. PCA "
                "therefore achieved almost no dimensionality reduction on "
                "this data."
            ),
            "The credible interval describes a long-run rate, not any single day.",
        ]
        if c
    ]

    return result


def risk_score_method() -> dict:
    """Serve the full construction and validation report for the score."""
    metrics = score_inference.load_metrics()
    if metrics is None:
        raise NotReady((
                "The risk score has not been trained. Run: "
                "python scripts/train_risk_score.py"
            ),
        )
    return metrics


# ===========================================================================
# PHASE 8: KNN recommendations
# ===========================================================================


def recommendations(
    db: Session,
    scope: AnalysisScope = "all",
) -> dict:
    """Generate recommendations from two separately-labelled evidence sources.

    Every suggestion carries `evidence_basis` (population_comparison or
    personal_baseline) and `claim_type` (associational or
    personal_deviation). Nothing is ever phrased as a causal promise: the
    dataset is cross-sectional, so it contains no outcomes and cannot say
    what happens after somebody changes their habits.
    """
    if not rec_inference.is_trained():
        raise NotReady((
                "The recommendation engine is not trained. Run: "
                "python scripts/train_recommender.py"
            ),
        )

    # Shared frame, for the same reason as /anomalies: this endpoint built its
    # own and so excluded itself from the incomplete-day rule. The effect here
    # is subtler than a wrong number - averaging a morning in makes the user
    # look like a lighter user than they are, which changes WHICH neighbours
    # the KNN finds, and therefore what advice comes back.
    raw = _daily_frame(db, scope)
    if raw.empty:
        held_back = raw.attrs.get("excluded_today_minutes")
        raise NotReady((
                "Today is the only day on record, and a day still in progress "
                "is not a usable summary of how you use your phone. Advice "
                "becomes available once there is a complete day."
                if held_back is not None
                else f"No usage records in scope '{scope}', so there is "
                "nothing to base advice on."
            ),
        )

    # Personal-baseline recommendations need the Phase 6 assessments. When
    # there is too little history for a baseline, those are simply absent -
    # the population half still works, so this is not an error.
    assessments: list = []
    try:
        features, _ = build_personal_features(raw)
        assessments = pb.assess_history(features)
    except (pb.InsufficientHistory, clf_inference.MissingFeatures):
        # Either way the personal half is unavailable while the population
        # half still works, so this is not an error - see the note above.
        assessments = []

    try:
        result = rec_inference.recommend(raw, assessments)
    except clf_inference.MissingFeatures as exc:
        raise NotReady(str(exc)
        ) from exc
    except clf_inference.InsufficientHistory as exc:
        raise NotReady((
                f"Not enough history for recommendations - {exc.have} day(s) "
                f"recorded, {exc.need} needed."
            ),
        ) from exc
    except rec_inference.ModelNotTrained as exc:
        raise NotReady(str(exc)) from exc

    result["personal_baseline_available"] = bool(assessments)
    if not assessments:
        result["personal_baseline_note"] = (
            "Personal-baseline suggestions are unavailable because there is "
            "not yet enough history for a reliable baseline."
        )

    today_note = _today_note(raw)
    if today_note:
        result["caveats"] = [*result.get("caveats", []), today_note]

    return result


def recommendations_method() -> dict:
    """Serve the evidence assessment recorded at training time."""
    metrics = rec_inference.load_metrics()
    if metrics is None:
        raise NotReady((
                "The recommendation engine is not trained. Run: "
                "python scripts/train_recommender.py"
            ),
        )
    return metrics
