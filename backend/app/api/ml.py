"""ML endpoints: a thin HTTP layer over app/services/analysis_service.py.

WHAT IS AND IS NOT HERE
=======================
Nothing in this module decides anything. Every rule - which scope to analyse,
when a model may not speak, which caveats a result carries - lives in
analysis_service, because the phone runs that code directly and a copy here
would drift from it the first time either was fixed.

What remains is the one thing genuinely specific to HTTP: turning a refusal
into a status code. A model that cannot honestly answer raises NotReady, and
that becomes 409 with the reason text unchanged.

This module is TEMPORARY. Once the app calls analysis_service through the
Kotlin bridge, nothing needs an HTTP server at all, and this file goes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.services import analysis_service
from app.services.analysis_service import AnalysisScope, NotReady

router = APIRouter(prefix="/ml", tags=["ml"])

SCOPE = Query(default="all", description="Which data to analyse.")


def _run(fn, *args, **kwargs) -> dict:
    """Call an analysis function, translating a refusal into 409.

    409 and not 500: a model declining to answer is an expected state with a
    clear user meaning, and the detail says precisely which measurement is
    missing or how many days are needed.
    """
    try:
        return fn(*args, **kwargs)
    except NotReady as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get("/status", summary="Which models are trained, and how good are they")
def ml_status() -> dict:
    return analysis_service.ml_status()


@router.get("/model-performance", summary="Full evaluation report for every model")
def model_performance() -> dict:
    return _run(analysis_service.model_performance)


@router.get("/forecast", summary="Predicted screen time for the coming days")
def forecast(
    horizon: int = Query(default=7, ge=1, le=14),
    scope: AnalysisScope = SCOPE,
    db: Session = Depends(get_db),
) -> dict:
    return _run(analysis_service.forecast, db, horizon=horizon, scope=scope)


@router.get("/actual-vs-predicted", summary="Model fit over observed history")
def actual_vs_predicted(
    scope: AnalysisScope = SCOPE, db: Session = Depends(get_db)
) -> dict:
    return _run(analysis_service.actual_vs_predicted, db, scope=scope)


@router.get("/risk-classification", summary="Risk class for your typical day")
def risk_classification(
    scope: AnalysisScope = SCOPE, db: Session = Depends(get_db)
) -> dict:
    return _run(analysis_service.risk_classification, db, scope=scope)


@router.get(
    "/risk-confidence",
    summary="Bayesian confidence in the risk class, from week-by-week classification",
)
def risk_confidence(
    scope: AnalysisScope = SCOPE,
    weeks: int = Query(default=12, ge=2, le=52),
    db: Session = Depends(get_db),
) -> dict:
    return _run(analysis_service.risk_confidence, db, scope=scope, weeks=weeks)


@router.get("/persona", summary="Closest usage persona")
def persona(scope: AnalysisScope = SCOPE, db: Session = Depends(get_db)) -> dict:
    return _run(analysis_service.persona, db, scope=scope)


@router.get("/clustering-report", summary="How the personas were derived")
def clustering_report() -> dict:
    return _run(analysis_service.clustering_report)


@router.get("/anomalies", summary="Days that deviate from your personal baseline")
def anomalies(
    limit: int = Query(default=60, ge=1, le=400),
    only_anomalous: bool = Query(default=False),
    scope: AnalysisScope = SCOPE,
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        analysis_service.anomalies,
        db,
        limit=limit,
        only_anomalous=only_anomalous,
        scope=scope,
    )


@router.get("/risk-score", summary="PCA behaviour index with Bayesian confidence")
def risk_score(scope: AnalysisScope = SCOPE, db: Session = Depends(get_db)) -> dict:
    return _run(analysis_service.risk_score, db, scope=scope)


@router.get("/risk-score-method", summary="How the risk score is constructed")
def risk_score_method() -> dict:
    return _run(analysis_service.risk_score_method)


@router.get("/recommendations", summary="Personalised, evidence-rated suggestions")
def recommendations(
    scope: AnalysisScope = SCOPE, db: Session = Depends(get_db)
) -> dict:
    return _run(analysis_service.recommendations, db, scope=scope)


@router.get("/recommendations-method", summary="What the recommender may claim")
def recommendations_method() -> dict:
    return _run(analysis_service.recommendations_method)
