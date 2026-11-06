"""Health and capability endpoints.

The mobile app calls `/api/health` on startup. Two reasons this matters:

1. The phone reaches the backend over Wi-Fi/hotspot by IP, which changes. The
   app needs to distinguish "backend unreachable" from "backend fine but no
   data yet" so it can show an honest message instead of an empty dashboard.

2. `models_trained` reports which ML artifacts actually exist on disk. The app
   uses this to hide or disable ML sections rather than displaying a
   placeholder number, which the brief forbids.
"""

from __future__ import annotations

import socket
import sys

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.session import get_db

router = APIRouter(tags=["health"])


def _lan_addresses() -> list[str]:
    """This machine's LAN IPv4 addresses.

    The phone needs one of these to reach the backend over Wi-Fi, and it
    cannot discover them on its own - so the backend advertises them. The app
    displays them, which means a changed hotspot address is visible in the UI
    rather than having to be hunted down with ipconfig.

    Loopback is excluded: it is never useful to a different device.
    """
    addresses: set[str] = set()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    # A UDP socket to an external address reveals which local interface the OS
    # would route through, without sending anything. This catches the active
    # interface even when the hostname lookup above misses it.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    return sorted(addresses)


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """Liveness plus a truthful picture of what the backend can currently do."""
    settings = get_settings()

    # Actually touch the database rather than assuming it works.
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
        db_error: str | None = None
    except Exception as exc:  # pragma: no cover - defensive
        db_ok = False
        db_error = str(exc)

    # Report which trained artifacts exist. Nothing is trained yet in Phase 3,
    # so this is expected to be an empty list - and the app must respect that.
    artifacts_dir = settings.artifacts_dir
    trained = (
        sorted(p.stem for p in artifacts_dir.glob("*.joblib"))
        if artifacts_dir.exists()
        else []
    )

    return {
        "status": "ok" if db_ok else "degraded",
        "app_name": settings.app_name,
        "python_version": sys.version.split()[0],
        "database": {"connected": db_ok, "error": db_error},
        "models_trained": trained,
        "min_days_for_baseline": settings.min_days_for_baseline,
        # Advertised so the app can show which address to use over Wi-Fi.
        # The phone cannot discover the laptop's IP by itself.
        "lan_addresses": _lan_addresses(),
        "port": settings.port,
    }
