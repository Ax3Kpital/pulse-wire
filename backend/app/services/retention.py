"""Retention: purge text past `retention_days`, delete rows past `metadata_retention_days`,
and prune old operational history."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import Select, delete, select, update
from sqlalchemy.orm import Session

from app.models import IngestionRun, RawItem, Source, SourceHealth, Story, StoryMember
from app.repositories.stories import refresh_story
from app.services import search

log = logging.getLogger(__name__)

HEALTH_HISTORY_DAYS = 30
RUN_HISTORY_DAYS = 90


def purge(session: Session, now: datetime, category_ids: dict[str, int]) -> dict[str, int]:
    touched: set[int] = set()

    expired = list(session.scalars(
        select(RawItem.id).where(RawItem.expires_at <= now, RawItem.text_purged_at.is_(None))
    ))  # fmt: skip
    if expired:
        session.execute(
            update(RawItem).where(RawItem.id.in_(expired))
            .values(summary=None, content=None, content_hash=None, text_purged_at=now)
        )  # fmt: skip
        touched.update(session.scalars(
            select(StoryMember.story_id).where(StoryMember.raw_item_id.in_(expired))
        ))  # fmt: skip

    doomed = list(session.scalars(select(RawItem.id).where(RawItem.delete_after <= now)))
    if doomed:
        touched.update(session.scalars(
            select(StoryMember.story_id).where(StoryMember.raw_item_id.in_(doomed))
        ))  # fmt: skip
        session.execute(delete(RawItem).where(RawItem.id.in_(doomed)))

    session.flush()
    session.expire_all()
    removed_stories = 0
    for story_id in touched:
        story = session.get(Story, story_id)
        if story is None:
            continue
        if not story.members:
            session.delete(story)
            session.flush()
            search.reindex_story(session, story_id)  # removes the FTS row
            removed_stories += 1
        else:
            refresh_story(session, story, category_ids)

    session.execute(delete(SourceHealth).where(
        SourceHealth.checked_at < now - timedelta(days=HEALTH_HISTORY_DAYS)))  # fmt: skip
    session.execute(delete(IngestionRun).where(
        IngestionRun.started_at < now - timedelta(days=RUN_HISTORY_DAYS),
        IngestionRun.status != "running"))  # fmt: skip

    stats = {"text_purged": len(expired), "items_deleted": len(doomed),
             "stories_deleted": removed_stories}  # fmt: skip
    if any(stats.values()):
        log.info("retention: %s", stats)
    return stats


def purge_demo_data(session: Session, category_ids: dict[str, int]) -> int:
    """Remove every item from demo sources (used when demo mode is off), so fixture stories never
    appear next to real news."""
    removed = purge_items(
        session,
        select(RawItem.id).join(Source, Source.id == RawItem.source_id).where(Source.is_demo),
        category_ids,
    )
    if removed:
        session.execute(update(Source).where(Source.is_demo).values(cursor=None, status="disabled"))
        log.info("removed %d demo items", removed)
    return removed


def purge_source(session: Session, key: str, category_ids: dict[str, int]) -> int:
    """Delete everything ingested from one source and reset its cursor."""
    removed = purge_items(
        session,
        select(RawItem.id).join(Source, Source.id == RawItem.source_id).where(Source.key == key),
        category_ids,
    )
    session.execute(update(Source).where(Source.key == key).values(cursor=None))
    log.info("removed %d items from source %s", removed, key)
    return removed


def purge_items(session: Session, ids_query: Select[int], category_ids: dict[str, int]) -> int:
    """Delete raw items (with dedupe keys and memberships via cascade); stories left without
    members are deleted, stories that still have members are rebuilt and re-indexed."""
    item_ids = list(session.scalars(ids_query))
    if not item_ids:
        return 0
    touched = set(session.scalars(
        select(StoryMember.story_id).where(StoryMember.raw_item_id.in_(item_ids))
    ))  # fmt: skip
    session.execute(delete(RawItem).where(RawItem.id.in_(item_ids)))
    session.flush()
    session.expire_all()
    for story_id in touched:
        story = session.get(Story, story_id)
        if story is None:
            continue
        if not story.members:
            session.delete(story)
            session.flush()
            search.reindex_story(session, story_id)
        else:
            refresh_story(session, story, category_ids)
    return len(item_ids)
