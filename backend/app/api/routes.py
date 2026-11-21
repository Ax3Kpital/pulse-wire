"""HTTP routes. The app only ever listens on 127.0.0.1."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import schemas as s
from app.config.settings import get_settings
from app.db import get_db
from app.models import (
    Category,
    IngestionRun,
    RawItem,
    Source,
    SourceHealth,
    Story,
    StoryCategory,
    StoryMember,
)
from app.repositories.stories import StoryFilters, get_story, list_stories
from app.scheduler.jobs import next_run_time
from app.services.impact import get_analyzer, report_impact

router = APIRouter()
DB = Annotated[Session, Depends(get_db)]

PERIODS: dict[str, timedelta | None] = {
    "24h": timedelta(hours=24), "3d": timedelta(days=3), "7d": timedelta(days=7),
    "30d": timedelta(days=30), "all": None,
}  # fmt: skip


# --------------------------------------------------------------------------- mapping helpers


def _source_ref(src: Source) -> s.SourceRef:
    return s.SourceRef(
        key=src.key,
        name=src.name,
        publisher=src.publisher,
        is_demo=src.is_demo,
        paywalled=bool((src.config or {}).get("paywalled", False)),
    )


def _report(m: StoryMember) -> s.StoryReport:
    item: RawItem = m.item
    imp = report_impact(item)
    return s.StoryReport(
        item_id=item.id, source=_source_ref(item.source), title=item.title, url=item.url,
        published_at=item.published_at, excerpt=item.summary, is_primary=m.is_primary,
        method=m.method, match_score=m.match_score, match_reason=m.match_reason or {},
        impact_score=imp.score, impact_level=imp.level, sentiment=imp.sentiment, tone=imp.tone,
        impact_reasons=imp.reasons,
    )  # fmt: skip


def _tone(value: float) -> str:
    band = get_analyzer().cfg.sentiment.neutral_band
    return "negative" if value <= -band else "positive" if value >= band else "neutral"


def _chips(story: Story) -> list[s.CategoryChip]:
    cats = sorted(story.categories, key=lambda c: (c.category.parent_id is not None,
                                                   c.category.sort_order))  # fmt: skip
    return [s.CategoryChip(slug=c.category.slug, name=c.category.name, score=c.score) for c in cats]


def _story_out(story: Story) -> s.StoryOut:
    members = list(story.members)
    primary = next((m for m in members if m.is_primary), members[0])
    excerpt = primary.item.summary or next(
        (m.item.summary for m in members if m.item.summary), None
    )
    others = sorted((m for m in members if m is not primary), key=lambda m: m.item.published_at)
    return s.StoryOut(
        id=story.id, title=story.title, url=primary.item.url,
        primary_source=_source_ref(primary.item.source), published_at=story.published_at,
        last_member_at=story.last_member_at, excerpt=excerpt, categories=_chips(story),
        source_count=story.source_count, member_count=story.member_count,
        impact_score=story.impact_score, impact_level=story.impact_level,
        impact_reasons=story.impact_reasons or [], sentiment=story.sentiment,
        tone=_tone(story.sentiment), primary_impact_score=report_impact(primary.item).score,
        others=[_report(m) for m in others],
    )  # fmt: skip


def _run_out(run: IngestionRun | None) -> s.RunOut | None:
    if run is None:
        return None
    return s.RunOut(id=run.id, kind=run.kind, status=run.status, started_at=run.started_at,
                    finished_at=run.finished_at, coalesced_requests=run.coalesced_requests,
                    stats=run.stats or {}, error=run.error)  # fmt: skip


# --------------------------------------------------------------------------- routes


@router.get("/health")
def health(request: Request, db: DB) -> dict[str, Any]:
    db.execute(select(1))
    scheduler = getattr(request.app.state, "scheduler", None)
    return {
        "status": "ok",
        "database": "ok",
        "scheduler": "running" if scheduler is not None and scheduler.running else "stopped",
        "time": datetime.now(UTC),
    }


@router.get("/api/meta")
def meta() -> dict[str, Any]:
    st = get_settings()
    return {"timezone": st.display_timezone, "interval_minutes": st.scan_interval_minutes,
            "demo_enabled": st.demo_enabled, "periods": list(PERIODS)}  # fmt: skip


@router.get("/api/stories", response_model=s.StoryPage)
def stories(
    db: DB,
    q: str | None = None,
    category: str | None = None,
    source: str | None = None,
    period: Literal["24h", "3d", "7d", "30d", "all"] = "7d",
    free_only: bool = False,
    min_impact: Annotated[int | None, Query(ge=1, le=10)] = None,
    sort: Literal["latest", "impact"] = "latest",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.StoryPage:
    delta = PERIODS[period]
    filters = StoryFilters(
        q=(q or "").strip() or None, category=category, source=source, free_only=free_only,
        min_impact=min_impact, sort=sort,
        since=datetime.now(UTC) - delta if delta else None, limit=limit, offset=offset,
    )  # fmt: skip
    rows, total = list_stories(db, filters)
    return s.StoryPage(items=[_story_out(r) for r in rows], total=total, offset=offset, limit=limit)


@router.get("/api/stories/{story_id}", response_model=s.StoryDetail)
def story_detail(story_id: int, db: DB) -> s.StoryDetail:
    story = get_story(db, story_id)
    if story is None:
        raise HTTPException(404, "story not found")
    base = _story_out(story)
    classification = [
        {"slug": c.category.slug, "name": c.category.name, "score": c.score, "reasons": c.reasons}
        for c in story.categories
    ]
    return s.StoryDetail(**base.model_dump(), reports=[_report(m) for m in story.members],
                         classification=classification)  # fmt: skip


@router.get("/api/categories", response_model=list[s.CategoryNode])
def categories(
    db: DB,
    period: Literal["24h", "3d", "7d", "30d", "all"] = "7d",
    source: str | None = None,
    free_only: bool = False,
    min_impact: Annotated[int | None, Query(ge=1, le=10)] = None,
) -> list[s.CategoryNode]:
    """Category tree with story counts for the same period/source filter as the story list."""
    delta = PERIODS[period]
    count_q = (
        select(StoryCategory.category_id, func.count())
        .join(Story, Story.id == StoryCategory.story_id)
        .group_by(StoryCategory.category_id)
    )
    if source:
        count_q = count_q.where(
            Story.id.in_(
                select(StoryMember.story_id)
                .join(RawItem, RawItem.id == StoryMember.raw_item_id)
                .join(Source, Source.id == RawItem.source_id)
                .where(Source.key == source)
            )
        )
    if min_impact:
        count_q = count_q.where(Story.impact_score >= min_impact)
    if free_only:
        count_q = count_q.where(
            Story.id.in_(
                select(StoryMember.story_id)
                .join(RawItem, RawItem.id == StoryMember.raw_item_id)
                .join(Source, Source.id == RawItem.source_id)
                .where(func.coalesce(func.json_extract(Source.config, "$.paywalled"), 0) == 0)
            )
        )
    if delta:
        count_q = count_q.where(Story.last_member_at >= datetime.now(UTC) - delta)
    counts = dict(db.execute(count_q).tuples().all())
    cats = list(db.scalars(select(Category).order_by(Category.sort_order)))
    nodes = {
        c.id: s.CategoryNode(slug=c.slug, name=c.name, count=counts.get(c.id, 0)) for c in cats
    }
    roots: list[s.CategoryNode] = []
    for c in cats:
        if c.parent_id is None:
            roots.append(nodes[c.id])
        elif c.parent_id in nodes:
            nodes[c.parent_id].children.append(nodes[c.id])
    return roots


@router.get("/api/sources", response_model=list[s.SourceOut])
def sources(db: DB) -> list[s.SourceOut]:
    rows = list(db.scalars(select(Source).order_by(Source.is_demo, Source.name)))
    out = []
    for src in rows:
        recent = db.scalars(
            select(SourceHealth).where(SourceHealth.source_id == src.id)
            .order_by(SourceHealth.checked_at.desc()).limit(5)
        )  # fmt: skip
        out.append(s.SourceOut(
            key=src.key, name=src.name, publisher=src.publisher, connector=src.connector,
            enabled=src.enabled, is_demo=src.is_demo, status=src.status, policy=src.policy,
            retention_days=src.retention_days, disabled_reason=src.disabled_reason,
            last_error=src.last_error, last_error_at=src.last_error_at,
            last_success_at=src.last_success_at, last_fetch_at=src.last_fetch_at,
            homepage=(src.config or {}).get("homepage"),
            recent=[s.HealthCheck.model_validate(h, from_attributes=True) for h in recent],
        ))  # fmt: skip
    return out


@router.post("/api/scan", response_model=s.ScanTicketOut)
def scan_now(request: Request) -> s.ScanTicketOut:
    ticket = request.app.state.coordinator.request_scan("manual")
    message = (
        f"A scan is already running (#{ticket.run_id}); your request joined it."
        if ticket.coalesced
        else f"Scan #{ticket.run_id} started."
    )
    return s.ScanTicketOut(run_id=ticket.run_id, coalesced=ticket.coalesced,
                           status=ticket.status, message=message)  # fmt: skip


@router.get("/api/scan/status", response_model=s.ScanStatus)
def scan_status(request: Request, db: DB) -> s.ScanStatus:
    coordinator = request.app.state.coordinator
    current_id = coordinator.current_run_id
    current = db.get(IngestionRun, current_id) if current_id else None
    last = db.scalar(
        select(IngestionRun).where(IngestionRun.status != "running")
        .order_by(IngestionRun.id.desc()).limit(1)
    )  # fmt: skip
    last_refresh = db.scalar(select(func.max(Source.last_success_at)))
    return s.ScanStatus(
        running=current_id is not None, current_run=_run_out(current), last_run=_run_out(last),
        last_refresh_at=last_refresh,
        next_scheduled_at=next_run_time(getattr(request.app.state, "scheduler", None)),
        interval_minutes=get_settings().scan_interval_minutes,
    )  # fmt: skip


@router.get("/api/runs", response_model=list[s.RunOut])
def runs(db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> list[s.RunOut]:
    rows = db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(limit))
    return [r for r in (_run_out(x) for x in rows) if r is not None]
