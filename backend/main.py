"""FastAPI application entry point.

Run from the `backend/` directory:

    .venv/Scripts/python.exe -m uvicorn main:app --reload --host 0.0.0.0 --port 8000

Binding to 0.0.0.0 is required for the phone to reach the API over the local
network; 127.0.0.1 would only be reachable from this laptop.

Interactive API docs: http://localhost:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import data, health, ml, usage
from app.config import get_settings
from app.database.session import init_db

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Create the database and required directories before serving."""
    init_db()
    yield
    # Nothing to tear down: SQLAlchemy's engine pool closes with the process.


app = FastAPI(
    title=settings.app_name,
    description=(
        "Backend for the Digital Wellbeing Risk & Deviation Detection system.\n\n"
        "This is an academic prototype, not a medical device. It does not "
        "provide medical advice or diagnosis."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# CORS is permissive because this only ever runs on a local development
# network, and the Expo web target makes real cross-origin requests.
# React Native's fetch is not subject to CORS, but Expo web is.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(usage.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(ml.router, prefix="/api")


@app.get("/", tags=["health"])
def root() -> dict:
    """Friendly landing response, so hitting the bare IP is not a 404."""
    return {
        "name": settings.app_name,
        "docs": "/docs",
        "health": "/api/health",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
