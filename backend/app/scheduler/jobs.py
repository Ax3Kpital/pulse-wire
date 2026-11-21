"""APScheduler wiring: `scan_all_sources` every N minutes (default 15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app.scheduler.coordinator import ScanCoordinator

JOB_ID = "scan_all_sources"


def scan_all_sources_job(coordinator: ScanCoordinator) -> None:
    """Scheduled trigger. Goes through the same coordinator as `Fetch now`."""
    coordinator.request_scan("scheduled")


def build_scheduler(
    coordinator: ScanCoordinator, *, interval_minutes: int, run_on_startup: bool
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=UTC)
    first_run = datetime.now(UTC) + (
        timedelta(seconds=3) if run_on_startup else timedelta(minutes=interval_minutes)
    )
    scheduler.add_job(
        scan_all_sources_job,
        "interval",
        minutes=interval_minutes,
        args=[coordinator],
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
        next_run_time=first_run,
    )
    return scheduler


def next_run_time(scheduler: BackgroundScheduler | None) -> datetime | None:
    if scheduler is None:
        return None
    job = scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None
