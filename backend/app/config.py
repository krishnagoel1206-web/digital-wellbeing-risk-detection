"""Application configuration.

Everything environment-dependent lives here so that nothing else in the
codebase hard-codes a path, a port or a connection string. Values can be
overridden with a .env file or real environment variables (see .env.example).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[1] is backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Runtime settings, read from the environment with sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- API ----------------------------------------------------------------
    app_name: str = "Digital Wellbeing Risk API"
    # 0.0.0.0 (not 127.0.0.1) so a phone on the same Wi-Fi/hotspot can reach
    # the server. Binding to localhost only would make the API unreachable
    # from the device even with the firewall open.
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True

    # --- Database -----------------------------------------------------------
    # SQLite lives on the backend rather than on the device, because the ML
    # modules need direct access to the full usage history as a DataFrame.
    database_url: str = f"sqlite:///{(BACKEND_ROOT / 'data' / 'wellbeing.db').as_posix()}"

    # --- Paths --------------------------------------------------------------
    data_dir: Path = BACKEND_ROOT / "data"
    raw_data_dir: Path = BACKEND_ROOT / "data" / "raw"
    synthetic_data_dir: Path = BACKEND_ROOT / "data" / "synthetic"
    # Trained models and their metrics are written here by the training
    # scripts and only ever READ by the API.
    artifacts_dir: Path = BACKEND_ROOT / "app" / "ml" / "artifacts"

    # --- Modelling rules ----------------------------------------------------
    # The personal baseline is meaningless on a handful of days. Below this
    # many records the API reports "insufficient history" instead of a score.
    # The brief explicitly requires this to be handled gracefully.
    min_days_for_baseline: int = 14

    # Hours counted as "late night" for the late-night usage ratio.
    late_night_start_hour: int = 23
    late_night_end_hour: int = 6

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
    """Cached settings accessor, so the .env file is parsed only once."""
    return Settings()
