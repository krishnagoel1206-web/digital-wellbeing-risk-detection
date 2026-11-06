"""Shared pytest fixtures.

Each test gets its own throwaway SQLite file and a TestClient wired to it, so
tests never touch the real development database and cannot leak state into one
another.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import Base
from app.database.session import get_db
from main import app


@pytest.fixture
def db_session(tmp_path) -> Iterator[Session]:
    """A Session bound to a fresh per-test SQLite file."""
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """TestClient with the get_db dependency pointed at the test session."""

    def _override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def valid_payload() -> dict:
    """A realistic, internally consistent record."""
    return {
        "record_date": "2026-11-01",
        "total_screen_minutes": 384,
        "social_minutes": 150,
        "gaming_minutes": 60,
        "productivity_minutes": 90,
        "app_launches": 142,
        "notification_count": 318,
        "late_night_minutes": 48,
        "sleep_hours": 6.5,
    }
