"""Syncs YAML configuration (categories, sources) into the database."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.loader import SourceConfig, Taxonomy, load_sources, load_taxonomy
from app.models import Category, Source

log = logging.getLogger(__name__)


def seed_categories(session: Session, taxonomy: Taxonomy | None = None) -> None:
    taxonomy = taxonomy or load_taxonomy()
    existing = {c.slug: c for c in session.scalars(select(Category))}
    order = 0
    for top in taxonomy.categories:
        order += 10
        parent = existing.get(top.slug) or Category(slug=top.slug)
        parent.name, parent.parent_id, parent.sort_order = top.name, None, order
        session.add(parent)
        session.flush()
        existing[top.slug] = parent
        for i, child in enumerate(top.children, start=1):
            row = existing.get(child.slug) or Category(slug=child.slug)
            row.name, row.parent_id, row.sort_order = child.name, parent.id, order + i
            session.add(row)
            existing[child.slug] = row
    session.flush()


def sync_sources(session: Session, configs: list[SourceConfig] | None = None) -> list[SourceConfig]:
    """Upsert config fields, keep runtime state (cursor, health). Sources removed from YAML are
    disabled, not deleted, so their stories keep attribution."""
    configs = configs if configs is not None else load_sources()
    existing = {s.key: s for s in session.scalars(select(Source))}
    seen = set()
    for cfg in configs:
        seen.add(cfg.key)
        row = existing.get(cfg.key) or Source(key=cfg.key, status="never")
        problem = cfg.activation_problem()
        row.name = cfg.name
        row.publisher = cfg.display_publisher
        row.connector = cfg.connector
        row.is_demo = cfg.demo
        row.enabled = problem is None
        row.disabled_reason = problem
        row.policy = cfg.policy
        row.retention_days = cfg.retention_days
        row.metadata_retention_days = cfg.metadata_retention_days
        row.config = cfg.model_dump(
            include={"homepage", "default_categories", "priority", "paywalled", "url", "url_env"}
        )
        if problem and row.status in {"never", "ok"}:
            row.status = "disabled"
        elif not problem and row.status == "disabled":
            row.status = "never"
        session.add(row)
    for key, row in existing.items():
        if key not in seen:
            row.enabled = False
            row.status = "disabled"
            row.disabled_reason = "removed from sources.yaml"
    session.flush()
    return configs
