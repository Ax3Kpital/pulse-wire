"""Application settings, loaded from environment variables and the repo-level `.env` file."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path

from dotenv import dotenv_values
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent
CONFIG_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWS_",
        env_file=REPO_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = REPO_DIR / "data"
    log_dir: Path = REPO_DIR / "logs"
    frontend_dist: Path = REPO_DIR / "frontend" / "dist"

    sources_file: Path = CONFIG_DIR / "sources.yaml"
    categories_file: Path = CONFIG_DIR / "categories.yaml"
    entities_dir: Path = CONFIG_DIR / "entities"

    scan_interval_minutes: int = 15
    scan_on_startup: bool = True
    scheduler_enabled: bool = True
    demo_enabled: bool = False  # fixtures only for tests/demos; never mixed with real data
    display_timezone: str = "Europe/Bucharest"

    # Identifiable user agent. Some publishers (e.g. SEC) require contact details here;
    # set NEWS_CONTACT to an email you control to enable those sources.
    contact: str = ""
    user_agent_base: str = "PulseWire/0.1 (personal, single-user; non-commercial)"

    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3
    default_min_interval_seconds: float = 2.0

    # Deduplication tuning
    cluster_window_hours: int = 72
    lsh_candidate_threshold: int = 40
    excerpt_max_chars: int = 600

    log_level: str = "INFO"

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, value: str) -> str:
        """The app must never be exposed on the network."""
        if value == "localhost":
            return "127.0.0.1"
        try:
            if not ipaddress.ip_address(value).is_loopback:
                raise ValueError
        except ValueError as exc:
            raise ValueError(f"host must be a loopback address, got {value!r}") from exc
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / "news.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def user_agent(self) -> str:
        if self.contact:
            return f"{self.user_agent_base} contact: {self.contact}"
        return self.user_agent_base


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_secret(name: str | None) -> str | None:
    """Resolve a credential/private URL by env var name, from the process env or `.env`.

    Source configs reference secrets by *name* only, so nothing private lives in YAML.
    """
    if not name:
        return None
    value = os.environ.get(name)
    if value is None:
        value = dotenv_values(REPO_DIR / ".env").get(name)
    return value or None
