"""Test setup: every test gets a fresh SQLite database in a temp dir, no network, no scheduler."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("NEWS_SCHEDULER_ENABLED", "false")
os.environ.setdefault("NEWS_CONTACT", "")


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("NEWS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEWS_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("NEWS_DEMO_ENABLED", "true")
    monkeypatch.setenv("NEWS_HTTP_MAX_RETRIES", "2")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    from app.main import bootstrap_database

    url = f"sqlite:///{tmp_path / 'test.db'}"
    bootstrap_database(url)
    return url


@pytest.fixture
def session_factory(db_url: str):  # type: ignore[no-untyped-def]
    from app.db import get_sessionmaker

    return get_sessionmaker()


@pytest.fixture
def demo_configs():  # type: ignore[no-untyped-def]
    from app.config.loader import load_sources

    return [c for c in load_sources() if c.demo and c.enabled]


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_service(session_factory, demo_configs):  # type: ignore[no-untyped-def]
    from app.connectors.demo import DemoConnector
    from app.connectors.registry import build_connector
    from app.services.ingestion import IngestionService

    def factory(configs=None, now=NOW, connector_factory=None):  # type: ignore[no-untyped-def]
        configs = demo_configs if configs is None else configs

        def default_factory(cfg):  # type: ignore[no-untyped-def]
            if cfg.connector == "demo":
                return DemoConnector(cfg, now=now)
            return build_connector(cfg)

        return IngestionService(
            session_factory,
            connector_factory=connector_factory or default_factory,
            configs_loader=lambda: configs,
            now=lambda: now,
        )

    return factory


@pytest.fixture
def run_scan(session_factory):  # type: ignore[no-untyped-def]
    from app.services.ingestion import finish_run, start_run

    def runner(service):  # type: ignore[no-untyped-def]
        with session_factory() as s:
            run = start_run(s, "manual")
            s.commit()
            run_id = run.id
        stats = service.scan_all_sources(run_id)
        with session_factory() as s:
            finish_run(s, run_id, stats, None)
            s.commit()
        return stats

    return runner
