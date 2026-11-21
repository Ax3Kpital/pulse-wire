"""Applies a source's storage policy before anything is written to the database.

metadata_only      title, link, date, section, attribution. No summary/content stored,
                   and the summary is not used for features (only the title is).
temporary_excerpt  + a short excerpt, purged after `retention_days`.
indexable_content  + excerpt and (if provided/allowed) full text, indexed for search,
                   purged after `retention_days` when set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config.loader import SourceConfig
from app.connectors.base import FetchedItem
from app.services.text import strip_html, truncate


@dataclass
class StoredText:
    summary: str | None
    content: str | None
    expires_at: datetime | None
    delete_after: datetime | None


def apply_policy(
    item: FetchedItem, cfg: SourceConfig, now: datetime, excerpt_max: int
) -> StoredText:
    delete_after = (
        now + timedelta(days=cfg.metadata_retention_days) if cfg.metadata_retention_days else None
    )
    if cfg.policy == "metadata_only":
        return StoredText(None, None, None, delete_after)

    raw_summary = strip_html(item.summary) or strip_html(item.content)
    summary = truncate(raw_summary, excerpt_max) if raw_summary else None
    expires_at = now + timedelta(days=cfg.retention_days) if cfg.retention_days else None
    if cfg.policy == "temporary_excerpt":
        return StoredText(summary, None, expires_at, delete_after)

    content = strip_html(item.content) or None
    return StoredText(summary, content, expires_at, delete_after)
