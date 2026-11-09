"""The app's entry point into Python, called from Kotlin.

WHAT THIS IS
============
On the laptop the React Native app talks to FastAPI over HTTP. On the phone
there is no server, no socket and no port: Kotlin calls `call()` below
directly, inside the app's own process, and gets a JSON string back.

WHY IT MIRRORS HTTP ANYWAY
==========================
Every response is {"status": <int>, "body": <json>} - the shape an HTTP
response already has. That is deliberate, and it is what keeps this migration
small: the TypeScript client interprets status codes, so pointing it at this
function instead of `fetch` changes one file and no screens at all. A bespoke
result format would have meant rewriting every call site and every error path
in the app for no benefit.

It also keeps the two transports honestly comparable. A refusal is 409 here
exactly as it is over HTTP, carrying the same sentence, because both call the
same service function - see app/services/analysis_service.py.

WHAT RUNS WHERE
===============
Nothing in this module makes a decision. It resolves a name to a service
function, converts arguments, and serialises the result. Every rule about
what the models may say lives in the services, which the laptop API calls
too.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import json
import os
import traceback
from typing import Any, Callable

# NOTE: no module-level import of app.config or anything that reads settings.
# configure() must set the environment FIRST, because get_settings() caches on
# first call and the paths are fixed for the life of the process afterwards.

_CONFIGURED = False


def configure(files_dir: str, assets_dir: str | None = None) -> str:
    """Point the backend at the app's private storage, then prepare the database.

    Called once from Kotlin before anything else. `files_dir` is Android's
    per-app directory, which is writable, private to this app, and backed up
    or deleted with it; `assets_dir` is where the trained models were unpacked
    from the APK, and is read-only.

    Returns a JSON report rather than raising, because the caller is a Kotlin
    try/catch across a language boundary where a Python traceback would
    otherwise be lost.
    """
    global _CONFIGURED
    try:
        data_dir = os.path.join(files_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        # Set BEFORE importing app.config: settings are read once and cached,
        # so an import that happens first would pin the laptop's paths and the
        # app would try to write inside a read-only APK.
        os.environ["DATA_DIR"] = data_dir
        os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
            data_dir, "wellbeing.db"
        ).replace("\\", "/")
        if assets_dir:
            os.environ["ARTIFACTS_DIR"] = assets_dir

        from app.config import get_settings, reset_settings
        from app.database import session as db_session
        from app.database.models import Base

        # Drop any settings cached by an earlier import, then rebind the
        # engine. Without the rebind, a module that imported the session layer
        # before configure() ran would have pinned the engine to the wrong
        # path - and the failure is silent: it reads and writes a real
        # database somewhere else rather than erroring.
        reset_settings()
        settings = get_settings()
        db_session.rebind(settings.database_url)
        # create_all is safe on every launch: it creates what is missing and
        # leaves existing tables, and their rows, untouched. That is what
        # preserves history across app updates.
        Base.metadata.create_all(bind=db_session.engine)

        _register()
        _CONFIGURED = True
        return _dumps(
            {
                "ok": True,
                "database": settings.database_url,
                "artifacts_dir": str(settings.artifacts_dir),
                "models_found": sorted(
                    p.name for p in settings.artifacts_dir.glob("*.joblib")
                )
                if settings.artifacts_dir.is_dir()
                else [],
            }
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return _dumps(
            {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}
        )


# ---------------------------------------------------------------------------
# JSON encoding
# ---------------------------------------------------------------------------


class _Encoder(json.JSONEncoder):
    """Serialise what the services actually return.

    The ML layer hands back numpy scalars, pandas timestamps, dates, enums and
    dataclasses. FastAPI converted all of these silently; crossing into Kotlin
    by hand means doing it explicitly, and a missed type here would surface on
    the device as an opaque serialisation failure rather than a wrong number.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, (dt.date, dt.datetime)):
            return o.isoformat()
        if isinstance(o, enum.Enum):
            return o.value
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (set, frozenset)):
            return sorted(o)

        # numpy / pandas, imported lazily so this module stays importable
        # without them.
        try:
            import numpy as np

            if isinstance(o, np.integer):
                return int(o)
            if isinstance(o, np.floating):
                # NaN and infinity are not valid JSON. They reach here from a
                # metric that could not be computed, and null is the honest
                # representation of that - a silently coerced 0.0 would read
                # as a real measurement.
                value = float(o)
                return value if value == value and abs(value) != float("inf") else None
            if isinstance(o, np.bool_):
                return bool(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
        except ImportError:
            pass

        try:
            import pandas as pd

            if o is pd.NaT:
                return None
            if isinstance(o, pd.Timestamp):
                return o.isoformat()
        except ImportError:
            pass

        # Last resort: stringify rather than raise. An unserialisable value
        # reaching the app as text is recoverable; a serialisation error means
        # the whole screen shows nothing.
        try:
            return super().default(o)
        except TypeError:
            return str(o)


def _dumps(payload: Any) -> str:
    """Serialise with _Encoder.

    NOTE the absence of `default=str`. Passing it looks like a harmless safety
    net and silently REPLACES the encoder class's default method, so every
    dataclass came back as its repr string instead of an object - the usage
    summary reached the caller as text and indexing it raised
    "string indices must be integers". The fallback belongs inside the
    encoder, where it runs after the real conversions, and that is where it
    now lives.
    """
    return json.dumps(payload, cls=_Encoder)


def _response(status: int, body: Any) -> str:
    return _dumps({"status": status, "body": body})


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def call(name: str, params_json: str = "{}") -> str:
    """Run one endpoint by name and return {"status", "body"} as JSON.

    Never raises. A failure that would be a 500 over HTTP comes back as one
    here, with the traceback in the body, because a Python exception thrown
    across the Kotlin boundary loses its stack and surfaces as an unhelpful
    JNI error.
    """
    if not _CONFIGURED:
        return _response(
            503,
            {
                "detail": (
                    "Python backend not configured. configure() must run before "
                    "any call; it sets the database path and creates the schema."
                )
            },
        )

    try:
        params = json.loads(params_json) if params_json else {}
    except json.JSONDecodeError as exc:
        return _response(400, {"detail": f"parameters were not valid JSON: {exc}"})

    if not _ROUTES:
        _register()
    handler = _ROUTES.get(name)
    if handler is None:
        return _response(
            404,
            {
                "detail": f"unknown endpoint {name!r}",
                "available": sorted(_ROUTES),
            },
        )

    from app.database.session import SessionLocal
    from app.services.analysis_service import NotReady

    db = SessionLocal()
    try:
        return _response(200, handler(db, params))
    except NotReady as exc:
        # The same 409 the HTTP API returns, with the same sentence: both
        # paths raise from the same service function.
        return _response(409, {"detail": str(exc)})
    except LookupError as exc:
        return _response(404, {"detail": str(exc)})
    except (TypeError, ValueError) as exc:
        # Validation lives in the schemas and raises ValueError; a dataclass
        # rejects an unexpected keyword with TypeError. Both are the caller's
        # fault, not a server fault.
        return _response(422, {"detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return _response(
            500, {"detail": str(exc), "traceback": traceback.format_exc()}
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Handlers - thin argument shuffling, no logic
# ---------------------------------------------------------------------------


def _scope(params: dict) -> str:
    return params.get("scope") or "all"


def _health(db, params: dict) -> dict:
    """Device health, which asks a different question from the server's.

    The HTTP version reports the laptop's LAN addresses so the phone can find
    it. On the device that is meaningless - there is nothing to reach - so
    what matters instead is whether the database opened and which models were
    unpacked from the APK.
    """
    import sys

    from app.config import get_settings
    from app.database.models import UsageRecord

    settings = get_settings()
    try:
        db.query(UsageRecord).limit(1).all()
        connected, error = True, None
    except Exception as exc:  # noqa: BLE001
        connected, error = False, str(exc)

    models = (
        sorted(p.stem for p in settings.artifacts_dir.glob("*.joblib"))
        if settings.artifacts_dir.is_dir()
        else []
    )
    return {
        "status": "ok" if connected and models else "degraded",
        "app_name": settings.app_name,
        "python_version": sys.version.split()[0],
        "database": {"connected": connected, "error": error},
        "models_trained": models,
        "min_days_for_baseline": settings.min_days_for_baseline,
        # Kept so the existing TypeScript type still matches. Empty on the
        # device by definition: nothing is reached over a network.
        "lan_addresses": [],
        "port": 0,
        "runtime": "embedded-python",
    }


def _usage_records(db, params: dict) -> list:
    from app.database.models import DataSource
    from app.schemas.usage import UsageRecordRead
    from app.services import usage_service

    sources = params.get("sources")
    parsed = [DataSource(s) for s in sources] if sources else None

    records = usage_service.list_records(
        db,
        limit=int(params.get("limit", 365)),
        offset=int(params.get("offset", 0)),
        start_date=_as_date(params.get("start_date")),
        end_date=_as_date(params.get("end_date")),
        sources=parsed,
    )
    return [UsageRecordRead.from_record(r) for r in records]


def _as_date(value):
    return dt.date.fromisoformat(value) if value else None


def _usage_summary(db, params: dict):
    from app.services import usage_service

    return usage_service.get_summary(db)


def _create_record(db, params: dict):
    from app.schemas.usage import UsageRecordCreate, UsageRecordRead
    from app.services import usage_service

    record = usage_service.create_record(db, UsageRecordCreate(**params))
    return UsageRecordRead.from_record(record)


def _delete_by_source(db, params: dict) -> dict:
    from app.database.models import DataSource
    from app.services import usage_service

    source = DataSource(params["source"])
    deleted = usage_service.delete_by_source(db, source)
    return {"deleted": deleted, "source": source.value}


def _sync(db, params: dict):
    from app.schemas.sync import DeviceDayPayload, SyncRequest
    from app.services import sync_service

    request = SyncRequest(
        days=[DeviceDayPayload(**day) for day in params.get("days", [])],
        permissions=params.get("permissions", {}),
        device_note=params.get("device_note"),
    )
    return sync_service.sync_days(db, request)


def _feature_dictionary(db, params: dict) -> dict:
    from app.ml.preprocessing import feature_dictionary as fd

    return {
        "personal_raw": [fd.to_dict(s) for s in fd.PERSONAL_RAW],
        "personal_derived": [fd.to_dict(s) for s in fd.PERSONAL_DERIVED],
        "population": [fd.to_dict(s) for s in fd.POPULATION_FEATURES],
        "targets": [fd.to_dict(s) for s in fd.TARGETS],
        "classifier_features": fd.classifier_features(),
    }


_ROUTES: dict[str, Callable[[Any, dict], Any]] = {}


def _register() -> None:
    """Bind endpoint names to handlers.

    Deferred into a function so importing this module costs nothing until
    configure() has run - several of these imports pull in scikit-learn, which
    is slow to load and pointless before the database exists.
    """
    from app.services import analysis_service as a
    from app.services import data_service as d

    _ROUTES.update(
        {
            # names mirror the HTTP paths the app already uses, minus /api/
            "health": _health,
            "ml/status": lambda db, p: a.ml_status(),
            "ml/model-performance": lambda db, p: a.model_performance(),
            "ml/forecast": lambda db, p: a.forecast(
                db, horizon=int(p.get("horizon", 7)), scope=_scope(p)
            ),
            "ml/actual-vs-predicted": lambda db, p: a.actual_vs_predicted(
                db, scope=_scope(p)
            ),
            "ml/risk-classification": lambda db, p: a.risk_classification(
                db, scope=_scope(p)
            ),
            "ml/persona": lambda db, p: a.persona(db, scope=_scope(p)),
            "ml/clustering-report": lambda db, p: a.clustering_report(),
            "ml/anomalies": lambda db, p: a.anomalies(
                db,
                limit=int(p.get("limit", 60)),
                only_anomalous=bool(p.get("only_anomalous", False)),
                scope=_scope(p),
            ),
            "ml/risk-score": lambda db, p: a.risk_score(db, scope=_scope(p)),
            "ml/risk-score-method": lambda db, p: a.risk_score_method(),
            "ml/recommendations": lambda db, p: a.recommendations(db, scope=_scope(p)),
            "ml/recommendations-method": lambda db, p: a.recommendations_method(),
            "usage/records": _usage_records,
            "usage/records/create": _create_record,
            "usage/records/delete-by-source": _delete_by_source,
            "usage/summary": _usage_summary,
            "data/feature-dictionary": _feature_dictionary,
            "data/sync": _sync,
            "data/sync-status": lambda db, p: d.sync_status(db),
            "data/seed-demo": lambda db, p: d.seed_demo(
                db,
                days=int(p.get("days", 60)),
                profile=p.get("profile", "balanced"),
                seed=int(p.get("seed", 42)),
                replace=bool(p.get("replace", True)),
            ),
        }
    )


def routes() -> str:
    """The endpoint names this build serves, for diagnostics."""
    if not _ROUTES:
        _register()
    return _dumps(sorted(_ROUTES))
