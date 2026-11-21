"""Response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SourceRef(BaseModel):
    key: str
    name: str
    publisher: str
    is_demo: bool
    paywalled: bool = False


class CategoryChip(BaseModel):
    slug: str
    name: str
    score: float


class StoryReport(BaseModel):
    """One publication's report on the story (attribution + link are always present)."""

    item_id: int
    source: SourceRef
    title: str
    url: str
    published_at: datetime
    excerpt: str | None
    is_primary: bool
    method: str
    match_score: float | None
    match_reason: dict[str, Any]
    impact_score: int = 1
    impact_level: str = "low"
    sentiment: float = 0.0
    tone: str = "neutral"
    impact_reasons: list[dict[str, Any]] = []


class StoryOut(BaseModel):
    id: int
    title: str
    url: str
    primary_source: SourceRef
    published_at: datetime
    last_member_at: datetime
    excerpt: str | None
    categories: list[CategoryChip]
    source_count: int
    member_count: int
    impact_score: int = 1  # 1 (routine) .. 10 (critical)
    impact_level: str = "low"  # low=green | moderate=yellow | important=orange | critical=red
    impact_reasons: list[dict[str, Any]] = []
    sentiment: float = 0.0  # average tone of the reports, -1 .. +1
    tone: str = "neutral"  # negative | neutral | positive
    primary_impact_score: int = 1  # the shown report's own score
    others: list[StoryReport]


class StoryDetail(StoryOut):
    reports: list[StoryReport]
    classification: list[dict[str, Any]]


class StoryPage(BaseModel):
    items: list[StoryOut]
    total: int
    offset: int
    limit: int


class CategoryNode(BaseModel):
    slug: str
    name: str
    count: int
    children: list[CategoryNode] = []


class HealthCheck(BaseModel):
    checked_at: datetime
    ok: bool
    http_status: int | None
    latency_ms: int | None
    items_fetched: int
    items_new: int
    not_modified: bool
    error: str | None


class SourceOut(BaseModel):
    key: str
    name: str
    publisher: str
    connector: str
    enabled: bool
    is_demo: bool
    status: str
    policy: str
    retention_days: int | None
    disabled_reason: str | None
    last_error: str | None
    last_error_at: datetime | None
    last_success_at: datetime | None
    last_fetch_at: datetime | None
    homepage: str | None
    recent: list[HealthCheck]


class RunOut(BaseModel):
    id: int
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    coalesced_requests: int
    stats: dict[str, Any]
    error: str | None


class ScanTicketOut(BaseModel):
    run_id: int
    coalesced: bool
    status: str
    message: str


class ScanStatus(BaseModel):
    running: bool
    current_run: RunOut | None
    last_run: RunOut | None
    last_refresh_at: datetime | None
    next_scheduled_at: datetime | None
    interval_minutes: int
