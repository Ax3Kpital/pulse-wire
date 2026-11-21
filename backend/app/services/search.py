"""SQLite FTS5 index over stories. Only text the source policy allows us to keep is indexed:
titles always, excerpts for temporary_excerpt/indexable_content, full text only for
indexable_content."""

from __future__ import annotations

import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import RawItem, Story, StoryMember

_TERM_RE = re.compile(r"[\w$€£%.-]+", re.UNICODE)


def reindex_story(session: Session, story_id: int) -> None:
    session.execute(text("DELETE FROM stories_fts WHERE story_id = :id"), {"id": story_id})
    story = session.get(Story, story_id)
    if story is None:
        return
    rows = session.execute(
        select(RawItem).join(StoryMember, StoryMember.raw_item_id == RawItem.id)
        .where(StoryMember.story_id == story_id)
    ).scalars()  # fmt: skip
    titles, bodies, sources = [story.title], [], set()
    for item in rows:
        titles.append(item.title)
        sources.add(item.source.publisher)
        sources.add(item.source.name)
        if item.source.policy != "metadata_only" and item.summary:
            bodies.append(item.summary)
        if item.source.policy == "indexable_content" and item.content:
            bodies.append(item.content[:20000])
    session.execute(
        text("INSERT INTO stories_fts(story_id, title, body, sources) VALUES (:id, :t, :b, :s)"),
        {
            "id": story_id,
            "t": " \n".join(dict.fromkeys(titles)),
            "b": "\n".join(bodies),
            "s": " ".join(sorted(sources)),
        },  # fmt: skip
    )


def build_match_query(user_query: str) -> str | None:
    """Turn free text into a safe FTS5 query: every term must match (prefix match on each)."""
    terms = [t.strip(".-") for t in _TERM_RE.findall(user_query)]
    terms = [t.replace('"', "") for t in terms if t]
    if not terms:
        return None
    return " AND ".join(f'"{t}"*' for t in terms[:12])


def search_story_ids(session: Session, user_query: str, limit: int = 2000) -> list[int]:
    """Story ids ordered by relevance (bm25: title weighted above body and sources)."""
    match = build_match_query(user_query)
    if match is None:
        return []
    rows = session.execute(
        text(
            "SELECT story_id FROM stories_fts WHERE stories_fts MATCH :q "
            "ORDER BY bm25(stories_fts, 0.0, 5.0, 1.0, 2.0) LIMIT :limit"
        ),
        {"q": match, "limit": limit},
    )
    return [int(r[0]) for r in rows]
