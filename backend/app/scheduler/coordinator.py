"""Single-flight scan coordinator.

The scheduler and the `Fetch now` button both call `request_scan`. If a scan is already in
progress, the request is coalesced into it (no second scan starts) and the caller receives
the running scan's id with `coalesced=True`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.models import IngestionRun
from app.services.ingestion import RunStats, finish_run, start_run

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanTicket:
    run_id: int
    coalesced: bool
    status: str


class ScanCoordinator:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        scan_fn: Callable[[int], RunStats],
    ) -> None:
        self._session_factory = session_factory
        self._scan_fn = scan_fn
        self._lock = threading.Lock()
        self._current_run_id: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def current_run_id(self) -> int | None:
        return self._current_run_id

    def is_running(self) -> bool:
        return self._current_run_id is not None

    def request_scan(self, kind: str = "manual") -> ScanTicket:
        with self._lock:
            if self._current_run_id is not None:
                run_id = self._current_run_id
                with self._session_factory() as session:
                    run = session.get(IngestionRun, run_id)
                    if run is not None:
                        run.coalesced_requests += 1
                        session.commit()
                log.info("%s scan request coalesced into running scan #%d", kind, run_id)
                return ScanTicket(run_id, True, "running")

            with self._session_factory() as session:
                run = start_run(session, kind)
                session.commit()
                run_id = run.id
            self._current_run_id = run_id
            self._thread = threading.Thread(
                target=self._execute, args=(run_id,), name=f"scan-{run_id}", daemon=True
            )
            self._thread.start()
            log.info("%s scan #%d started", kind, run_id)
            return ScanTicket(run_id, False, "running")

    def _execute(self, run_id: int) -> None:
        stats: RunStats | None = None
        error: str | None = None
        try:
            stats = self._scan_fn(run_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("scan #%d crashed", run_id)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                with self._session_factory() as session:
                    finish_run(session, run_id, stats, error)
                    session.commit()
            finally:
                with self._lock:
                    self._current_run_id = None
            log.info("scan #%d finished", run_id)

    def wait(self, timeout: float | None = None) -> None:
        """Block until the current scan (if any) finishes. Used by tests and CLI."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
