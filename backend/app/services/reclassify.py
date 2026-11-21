"""Re-apply the current classification rules to every stored item (after editing
categories.yaml / entities/*.yaml / sources.yaml). Uses only text the policy allowed us to keep."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.loader import load_sources
from app.models import Category, RawItem, Story
from app.repositories.stories import refresh_story
from app.services.classifier import get_classifier
from app.services.entities import get_matcher
from app.services.impact import get_analyzer

log = logging.getLogger(__name__)


def reclassify_all(session: Session) -> dict[str, int]:
    configs = {c.key: c for c in load_sources()}
    classifier, matcher, analyzer = get_classifier(), get_matcher(), get_analyzer()
    items = 0
    for item in session.scalars(select(RawItem)):
        cfg = configs.get(item.source.key)
        summary = item.summary if item.source.policy != "metadata_only" else None
        prior = None
        if summary is None and cfg is not None and cfg.classify_with_summary:
            # The description was never stored: keep the summary signals derived at ingestion.
            prior = {
                slug: data.get("reasons", [])
                for slug, data in ((item.meta or {}).get("classification") or {}).items()
            }
        title_ents, summary_ents = matcher.find(item.title), matcher.find(summary or "")
        result = classifier.classify(
            title=item.title, summary=summary, section=item.section,
            title_entities=title_ents, summary_entities=summary_ents,
            default_categories=cfg.default_categories if cfg else [],
            section_map=cfg.section_map if cfg else {},
            exclude_categories=cfg.exclude_categories if cfg else [],
            prior_summary_signals=prior,
        )  # fmt: skip
        item.entities = sorted(title_ents | summary_ents)
        # Sentiment terms of a description that was never stored: reuse those found at ingestion.
        prior_terms = ((item.meta or {}).get("impact") or {}).get("summary_terms")
        impact = analyzer.analyze(
            title=item.title, summary=summary, source_key=item.source.key,
            entities=item.entities, numbers=list(item.numbers or []), categorized=bool(result),
            prior_summary_terms=prior_terms if summary is None else None,
        )  # fmt: skip
        item.meta = {
            **(item.meta or {}),
            "classification": {
                s: {"score": c.score, "reasons": c.reasons} for s, c in result.items()
            },
            "impact": impact.to_meta(),
        }
        items += 1
    session.flush()
    category_ids = {c.slug: c.id for c in session.scalars(select(Category))}
    stories = 0
    for story in session.scalars(select(Story)):
        refresh_story(session, story, category_ids)
        stories += 1
    log.info("reclassified %d items across %d stories", items, stories)
    return {"items": items, "stories": stories}
