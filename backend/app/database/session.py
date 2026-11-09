"""Database engine and session management.

The FastAPI handlers never touch the engine directly - they depend on
`get_db`, which hands out a session and guarantees it is closed again. This
keeps the data-access layer swappable and the handlers free of connection
bookkeeping.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.database.models import Base

settings = get_settings()

# `check_same_thread=False` is required because FastAPI serves requests from a
# threadpool, and SQLite otherwise refuses to use a connection created on a
# different thread.
engine: Engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """Enable the PRAGMAs SQLite needs but does not default to.

    SQLite does NOT enforce foreign keys unless explicitly told to, and the
    default journal mode serialises readers against writers. WAL lets the ML
    layer read history while the API writes new records.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


# Attached as a function rather than with @event.listens_for, so rebind() can
# attach the same listener to a replacement engine.
event.listen(engine, "connect", _set_sqlite_pragmas)


def rebind(database_url: str) -> None:
    """Point the engine at a different database, after import time.

    WHY THIS IS NEEDED
    ------------------
    The engine above is created when this module is first imported, from
    whatever settings were cached at that moment. On the laptop that is
    correct and never changes. On Android it is not: the app's private
    directory is assigned at runtime, so the real path is known only once
    mobile_api.configure() has been told it.

    Relying on configure() running before anything imports this module is the
    kind of ordering assumption that holds until one import chain changes.
    When it broke in tests the failure was silent and alarming - the device
    simulation quietly read and wrote the DEVELOPER's database instead of the
    temporary one, so a refusal test got a real answer and passed data it was
    never given.

    Rebinding explicitly removes the assumption. The previous engine is
    disposed so its SQLite file handle is released rather than leaked.
    """
    global engine, SessionLocal

    if engine is not None:
        engine.dispose()

    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        echo=False,
        future=True,
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SessionLocal.configure(bind=engine)


def init_db() -> None:
    """Create the database file, its directory, and all tables if missing.

    Safe to call on every startup: `create_all` only creates what is absent.
    No migration tool is used - for a single-developer prototype the schema is
    created from the models, and the file can simply be deleted and recreated.
    """
    get_settings().ensure_directories()
    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session that is always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
