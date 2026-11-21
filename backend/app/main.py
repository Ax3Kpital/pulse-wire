"""FastAPI application factory and lifecycle (migrations, seed, scheduler)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api.routes import router
from app.config.settings import get_settings
from app.db import get_sessionmaker, init_engine, run_migrations, session_scope
from app.logging_setup import configure_logging
from app.models import Category
from app.scheduler.coordinator import ScanCoordinator
from app.scheduler.jobs import build_scheduler
from app.services import catalog, retention
from app.services.ingestion import IngestionService, abort_stale_runs

log = logging.getLogger(__name__)


def bootstrap_database(url: str | None = None) -> None:
    init_engine(url)
    run_migrations(url)
    with session_scope() as session:
        catalog.seed_categories(session)
        catalog.sync_sources(session)
        if not get_settings().demo_enabled:  # never show fixture stories next to real news
            category_ids = {c.slug: c.id for c in session.scalars(select(Category))}
            retention.purge_demo_data(session, category_ids)
        aborted = abort_stale_runs(session)
        if aborted:
            log.warning("marked %d stale run(s) as aborted", aborted)


def create_app(*, database_url: str | None = None, start_scheduler: bool | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        bootstrap_database(database_url)
        service = IngestionService(get_sessionmaker())
        app.state.coordinator = ScanCoordinator(get_sessionmaker(), service.scan_all_sources)
        app.state.scheduler = None
        run_scheduler = settings.scheduler_enabled if start_scheduler is None else start_scheduler
        if run_scheduler:
            app.state.scheduler = build_scheduler(
                app.state.coordinator,
                interval_minutes=settings.scan_interval_minutes,
                run_on_startup=settings.scan_on_startup,
            )
            app.state.scheduler.start()
            log.info("scheduler started: every %d min", settings.scan_interval_minutes)
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.shutdown(wait=False)
            app.state.coordinator.wait(timeout=30)

    app = FastAPI(title="Pulse Wire", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA (frontend/dist) from the same loopback server, if present."""
    dist = get_settings().frontend_dist
    index = dist / "index.html"
    if not index.exists():
        return
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
