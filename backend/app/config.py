"""Application configuration.

Everything environment-dependent lives here so that nothing else in the
codebase hard-codes a path, a port or a connection string. Values can be
overridden with a .env file or real environment variables (see .env.example).

WHY THIS NO LONGER USES pydantic-settings
=========================================
It did, and that single import blocked twelve of the eighteen ML modules from
running on the phone. pydantic-settings depends on pydantic v2, whose core is
a compiled Rust extension with no Android build, so every module that called
`get_settings()` - which is most of them - could not even be imported inside
the embedded interpreter.

The replacement is a frozen dataclass reading os.environ: a few dozen lines
with no dependencies at all. The public surface is deliberately unchanged -
`get_settings()` still returns an object with the same attribute names,
`ensure_directories()` still exists, and the same environment variables and
.env file are still honoured. Nothing that imports this module had to change.

WHY THE PATHS ARE NOT FIXED AT IMPORT TIME
==========================================
On the laptop everything hangs off the repository. On Android there is no
repository: the app writes to its own private directory, assigned at runtime
and different per install. So paths resolve from the environment, and the
Android entry point sets them before importing anything else. Treating
BACKEND_ROOT as the only source of truth would have produced an app trying to
write into a read-only APK asset directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# backend/app/config.py -> parents[1] is backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file.

    Only the subset pydantic-settings was actually being used for: no
    interpolation, no multi-line values, no `export` keyword. Anything more
    elaborate belongs in a real environment variable, which is read directly.

    A missing or unreadable file is not an error - the defaults below are
    complete on their own, which is what makes the app runnable with no
    configuration at all.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip().upper()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return values


_DOTENV = _load_dotenv(BACKEND_ROOT / ".env")


def _raw(name: str) -> str | None:
    """A real environment variable wins over the .env file, as before."""
    return os.environ.get(name.upper()) or _DOTENV.get(name.upper())


def _str(name: str, default: str) -> str:
    value = _raw(name)
    return default if value is None else value


def _int(name: str, default: int) -> int:
    value = _raw(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        # A malformed override falls back to the default rather than crashing
        # at import time, where the traceback would be far from the mistake.
        return default


def _bool(name: str, default: bool) -> bool:
    value = _raw(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    value = _raw(name)
    return default if value is None else Path(value)


def _default_data_dir() -> Path:
    return _path("DATA_DIR", BACKEND_ROOT / "data")


@dataclass
class Settings:
    """Runtime settings, read from the environment with sensible defaults.

    NOT frozen, deliberately. pydantic's BaseSettings was mutable, and tests
    override a path on the live instance to redirect artifact writes into a
    temporary directory. Freezing it looked tidier and was a silent behaviour
    change: three tests failed with FrozenInstanceError at teardown. Swapping
    an implementation is not the moment to tighten an unrelated contract.
    """

    # --- API ----------------------------------------------------------------
    app_name: str = field(
        default_factory=lambda: _str("APP_NAME", "Digital Wellbeing Risk API")
    )
    # 0.0.0.0 (not 127.0.0.1) so a phone on the same Wi-Fi/hotspot can reach
    # the server. Only relevant when the API runs on a development machine; on
    # the device nothing listens on a socket at all.
    host: str = field(default_factory=lambda: _str("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))
    debug: bool = field(default_factory=lambda: _bool("DEBUG", True))

    # --- Database -----------------------------------------------------------
    # On Android this points into the app's private storage, set through
    # DATABASE_URL by the mobile entry point before this module is imported.
    database_url: str = field(
        default_factory=lambda: _str(
            "DATABASE_URL",
            f"sqlite:///{(_default_data_dir() / 'wellbeing.db').as_posix()}",
        )
    )

    # --- Paths --------------------------------------------------------------
    data_dir: Path = field(default_factory=_default_data_dir)
    raw_data_dir: Path = field(
        default_factory=lambda: _path("RAW_DATA_DIR", _default_data_dir() / "raw")
    )
    synthetic_data_dir: Path = field(
        default_factory=lambda: _path(
            "SYNTHETIC_DATA_DIR", _default_data_dir() / "synthetic"
        )
    )
    # Trained models and their metrics are written here by the training
    # scripts and only ever READ by the API.
    artifacts_dir: Path = field(
        default_factory=lambda: _path(
            "ARTIFACTS_DIR", BACKEND_ROOT / "app" / "ml" / "artifacts"
        )
    )

    # --- Modelling rules ----------------------------------------------------
    # The personal baseline is meaningless on a handful of days. Below this
    # many records the API reports "insufficient history" instead of a score.
    # The brief explicitly requires this to be handled gracefully.
    min_days_for_baseline: int = field(
        default_factory=lambda: _int("MIN_DAYS_FOR_BASELINE", 14)
    )

    # Hours counted as "late night" for the late-night usage ratio.
    late_night_start_hour: int = field(
        default_factory=lambda: _int("LATE_NIGHT_START_HOUR", 23)
    )
    late_night_end_hour: int = field(
        default_factory=lambda: _int("LATE_NIGHT_END_HOUR", 6)
    )

    def ensure_directories(self) -> None:
        """Create the directories the app writes to, if they are missing."""
        for path in (
            self.data_dir,
            self.raw_data_dir,
            self.synthetic_data_dir,
            self.artifacts_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor, so the .env file is parsed only once.

    The cache is why the Android entry point must set its environment BEFORE
    the first call: afterwards the paths are fixed for the life of the
    process. `reset_settings()` exists for tests, which need to vary them.
    """
    return Settings()


def reset_settings() -> None:
    """Drop the cached settings, so the next call re-reads the environment."""
    get_settings.cache_clear()
