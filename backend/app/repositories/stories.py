"""Story persistence: membership, aggregate fields, categories, and list/detail queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Category, RawItem, Source, Story, StoryCategory, StoryMember
from app.services import search
from app.services.impact import get_analyzer, report_impact


def add_member(
    session: Session,
    story: Story,
    item: RawItem,
    *,
    method: str,
    score: float | None,
    reason: dict[str, Any],
) -> StoryMember:
    member = StoryMember(story=story, raw_item_id=item.id, method=method, match_score=score,
                         match_reason=reason)  # fmt: skip
    member.item = item
    session.add(member)
    return member


def refresh_story(session: Session, story: Story, category_ids: dict[str, int]) -> None:
    """Recompute primary member, title, dates, counts and categories from members."""
    members = list(story.members)
    if not members:
        return

    def rank(m: StoryMember) -> tuple[bool, int, float, int]:
        cfg = m.item.source.config or {}
        # Free-to-read reports first, then source priority, then the earliest report.
        paywalled = bool(cfg.get("paywalled", False))
        return (
            paywalled,
            -int(cfg.get("priority", 50)),
            m.item.published_at.timestamp(),
            m.raw_item_id,
        )

    primary = min(members, key=rank)
    for m in members:
        m.is_primary = m is primary
    story.primary_item_id = primary.raw_item_id
    story.title = primary.item.title
    story.published_at = min(m.item.published_at for m in members)
    story.last_member_at = max(m.item.published_at for m in members)
    story.member_count = len(members)
    story.source_count = len({m.item.source.publisher for m in members})  # publishers, not feeds

    merged: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    for m in members:
        for slug, data in (m.item.meta.get("classification") or {}).items():
            score, reasons = merged.get(slug, (0.0, []))
            tagged = [{**r, "item": m.raw_item_id} for r in data["reasons"]]
            merged[slug] = (max(score, data["score"]), (reasons + tagged)[:12])
    session.flush()
    session.execute(delete(StoryCategory).where(StoryCategory.story_id == story.id))
    for slug, (score, reasons) in merged.items():
        if slug in category_ids:
            session.add(StoryCategory(story_id=story.id, category_id=category_ids[slug],
                                      score=score, reasons=reasons))  # fmt: skip
    session.flush()

    imp = get_analyzer().combine([report_impact(m.item) for m in members], story.source_count)
    story.impact_score, story.impact_level, story.impact_reasons, story.sentiment = (
        imp.score, imp.level, imp.reasons, imp.sentiment)  # fmt: skip
    session.flush()
    search.reindex_story(session, story.id)


# --------------------------------------------------------------------------- queries


@dataclass
class StoryFilters:
    q: str | None = None
    category: str | None = None
    source: str | None = None
    free_only: bool = False  # hide stories whose every report is behind a paywall
    min_impact: int | None = None  # only stories scoring at least this (1..10)
    sort: str = "latest"  # latest | impact
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 30
    offset: int = 0


def list_stories(session: Session, f: StoryFilters) -> tuple[list[Story], int]:
    stmt = select(Story)
    ranked_ids: list[int] | None = None
    if f.q:
        ranked_ids = search.search_story_ids(session, f.q)
        if not ranked_ids:
            return [], 0
        stmt = stmt.where(Story.id.in_(ranked_ids))
    if f.category:
        stmt = stmt.where(
            exists().where(and_(
                StoryCategory.story_id == Story.id,
                StoryCategory.category_id == Category.id,
                Category.slug == f.category,
            ))
        )  # fmt: skip
    if f.source:
        stmt = stmt.where(
            exists().where(and_(
                StoryMember.story_id == Story.id,
                StoryMember.raw_item_id == RawItem.id,
                RawItem.source_id == Source.id,
                Source.key == f.source,
            ))
        )  # fmt: skip
    if f.min_impact:
        stmt = stmt.where(Story.impact_score >= f.min_impact)
    if f.free_only:
        stmt = stmt.where(
            exists().where(and_(
                StoryMember.story_id == Story.id,
                StoryMember.raw_item_id == RawItem.id,
                RawItem.source_id == Source.id,
                func.coalesce(func.json_extract(Source.config, "$.paywalled"), 0) == 0,
            ))
        )  # fmt: skip
    if f.since:
        stmt = stmt.where(Story.last_member_at >= f.since)
    if f.until:
        stmt = stmt.where(Story.published_at <= f.until)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.options(
        selectinload(Story.members).joinedload(StoryMember.item),
        selectinload(Story.categories).joinedload(StoryCategory.category),
    )
    if ranked_ids is not None:
        rows = list(session.scalars(stmt))
        order = {sid: i for i, sid in enumerate(ranked_ids)}
        rows.sort(key=lambda s: order.get(s.id, 1 << 30))
        return rows[f.offset : f.offset + f.limit], total
    if f.sort == "impact":
        stmt = stmt.order_by(Story.impact_score.desc(), Story.last_member_at.desc(),
                             Story.id.desc())  # fmt: skip
    else:
        stmt = stmt.order_by(Story.published_at.desc(), Story.id.desc())
    return list(session.scalars(stmt.offset(f.offset).limit(f.limit))), total


def get_story(session: Session, story_id: int) -> Story | None:
    return session.scalar(
        select(Story)
        .where(Story.id == story_id)
        .options(
            selectinload(Story.members).joinedload(StoryMember.item),
            selectinload(Story.categories).joinedload(StoryCategory.category),
        )
    )
