"""`python -m app` — run the local server (loopback only), or `python -m app scan` once."""

from __future__ import annotations

import sys

import uvicorn

from app.config.settings import get_settings


def main() -> None:
    settings = get_settings()  # validates that host is a loopback address
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command == "scan":
        _scan_once()
        return
    if command == "reclassify":
        _reclassify()
        return
    if command == "purge-source" and len(sys.argv) == 3:
        _purge_source(sys.argv[2])
        return
    if command != "serve":
        sys.exit("usage: python -m app [serve|scan|reclassify|purge-source <key>]")
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=1,  # single process: the scan coordinator lock is in-memory
        log_config=None,
    )


def _scan_once() -> None:
    from app.db import get_sessionmaker
    from app.logging_setup import configure_logging
    from app.main import bootstrap_database
    from app.scheduler.coordinator import ScanCoordinator
    from app.services.ingestion import IngestionService

    configure_logging()
    bootstrap_database()
    service = IngestionService(get_sessionmaker())
    coordinator = ScanCoordinator(get_sessionmaker(), service.scan_all_sources)
    ticket = coordinator.request_scan("manual")
    coordinator.wait()
    print(f"scan #{ticket.run_id} done")


def _reclassify() -> None:
    from app.db import session_scope
    from app.logging_setup import configure_logging
    from app.main import bootstrap_database
    from app.services.reclassify import reclassify_all

    configure_logging()
    bootstrap_database()
    with session_scope() as session:
        print(reclassify_all(session))


def _purge_source(key: str) -> None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.main import bootstrap_database
    from app.models import Category
    from app.services.retention import purge_source

    bootstrap_database()
    with session_scope() as session:
        category_ids = {c.slug: c.id for c in session.scalars(select(Category))}
        print(f"removed {purge_source(session, key, category_ids)} items from {key}")


if __name__ == "__main__":
    main()
