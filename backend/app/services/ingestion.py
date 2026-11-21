"""`scan_all_sources`: fetch every runnable source, store permitted data, dedupe, group, classify.

Each source is processed in its own transaction: an error in one source is recorded (sources.
last_error, source_health, log) and the run moves on to the next source.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config.loader import SourceConfig
from app.config.settings import get_settings
from app.connectors.base import Connector, FetchedItem
from app.connectors.registry import build_connector
from app.models import (
    Category,
    DedupeKey,
    IngestionRun,
    RawItem,
    Source,
    SourceHealth,
    Story,
    StoryMember,
)
from app.repositories.stories import add_member, refresh_story
from app.services import catalog, retention
from app.services.classifier import get_classifier
from app.services.dedupe import ClusterIndex, build_features
from app.services.entities import get_matcher
from app.services.extraction import extract_full_text
from app.services.impact import get_analyzer
from app.services.policy import apply_policy
from app.services.text import canonicalize_url, extract_numbers, normalize_title, sha256, strip_html

log = logging.getLogger(__name__)

FUTURE_TOLERANCE = timedelta(minutes=10)
MAX_EXTRACTIONS_PER_SOURCE = 20


@dataclass
class SourceStats:
    fetched: int = 0
    new: int = 0
    replays: int = 0
    joined: int = 0
    new_stories: int = 0
    skipped: int = 0
    not_modified: bool = False
    error: str | None = None
    latency_ms: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False)}


@dataclass
class RunStats:
    sources: dict[str, SourceStats] = field(default_factory=dict)
    purged: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        totals: dict[str, int] = {}
        for s in self.sources.values():
            for k in ("fetched", "new", "replays", "joined", "new_stories"):
                totals[k] = totals.get(k, 0) + getattr(s, k)
        return {
            "totals": totals,
            "errors": sum(1 for s in self.sources.values() if s.error),
            "sources": {k: v.as_dict() for k, v in self.sources.items()},
            "purged": self.purged,
        }


ConnectorFactory = Callable[[SourceConfig], Connector]


class IngestionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        connector_factory: ConnectorFactory = build_connector,
        configs_loader: Callable[[], list[SourceConfig]] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.connector_factory = connector_factory
        self.configs_loader = configs_loader
        self.now = now
        self.settings = get_settings()
        self.matcher = get_matcher()
        self.classifier = get_classifier()
        self.analyzer = get_analyzer()

    # ------------------------------------------------------------------ run

    def scan_all_sources(self, run_id: int) -> RunStats:
        stats = RunStats()
        with self.session_factory() as session:
            configs = catalog.sync_sources(
                session, self.configs_loader() if self.configs_loader else None
            )
            session.commit()
            category_ids = {c.slug: c.id for c in session.scalars(select(Category))}
            if not self.settings.demo_enabled:
                retention.purge_demo_data(session, category_ids)
                session.commit()

        index = self._load_index()
        for cfg in configs:
            if cfg.activation_problem() is not None:
                continue
            stats.sources[cfg.key] = self._scan_source(cfg, run_id, index, category_ids)

        with self.session_factory() as session:
            stats.purged = retention.purge(session, self.now(), category_ids)
            session.commit()
        return stats

    def _load_index(self) -> ClusterIndex:
        index = ClusterIndex(
            self.matcher,
            window_hours=self.settings.cluster_window_hours,
            lsh_threshold=self.settings.lsh_candidate_threshold,
        )
        horizon = self.now() - timedelta(days=21)
        with self.session_factory() as session:
            rows = session.execute(
                select(RawItem, StoryMember.story_id)
                .join(StoryMember, StoryMember.raw_item_id == RawItem.id)
                .join(Story, Story.id == StoryMember.story_id)
                .where(Story.last_member_at >= horizon)
            ).all()
            for item, story_id in rows:
                index.add(self._features(item, story_id))
        return index

    def _features(self, item: RawItem, story_id: int | None) -> Any:
        return build_features(
            key=item.id, story_id=story_id, source_id=item.source_id,
            published_at=item.published_at, title=item.title,
            summary=item.summary if item.source.policy != "metadata_only" else None,
            entities=item.entities, numbers=item.numbers,
        )  # fmt: skip

    # ------------------------------------------------------------------ per source

    def _scan_source(
        self, cfg: SourceConfig, run_id: int, index: ClusterIndex, category_ids: dict[str, int]
    ) -> SourceStats:
        st = SourceStats()
        connector = self.connector_factory(cfg)
        started = time.monotonic()
        keys_before = set(index.items)
        session = self.session_factory()
        try:
            source = session.scalar(select(Source).where(Source.key == cfg.key))
            assert source is not None
            source.status = "running"
            source.last_fetch_at = self.now()
            session.commit()

            result = connector.fetch_since(source.cursor)
            st.fetched = len(result.items)
            st.not_modified = result.not_modified
            st.latency_ms = result.latency_ms

            extractions = 0
            for fetched in result.items:
                want_text = (
                    cfg.extract_full_text and not fetched.content
                    and extractions < MAX_EXTRACTIONS_PER_SOURCE
                )  # fmt: skip
                outcome = self._ingest_item(
                    session, cfg, source, run_id, fetched, index, category_ids,
                    connector if want_text else None,
                )  # fmt: skip
                extractions += int(want_text and outcome in {"new_story", "joined"})
                if outcome == "replay":
                    st.replays += 1
                elif outcome == "skipped":
                    st.skipped += 1
                else:
                    st.new += 1
                    st.joined += outcome == "joined"
                    st.new_stories += outcome == "new_story"

            source.cursor = result.cursor
            source.status = "ok"
            source.last_success_at = self.now()
            source.last_error = None
            session.add(SourceHealth(
                source_id=source.id, run_id=run_id, checked_at=self.now(), ok=True,
                http_status=result.http_status, latency_ms=result.latency_ms,
                items_fetched=st.fetched, items_new=st.new, not_modified=result.not_modified,
            ))  # fmt: skip
            session.commit()
            log.info("source %s: %s", cfg.key, st.as_dict())
        except Exception as exc:  # noqa: BLE001 - isolate every source failure
            session.rollback()
            st.error = f"{type(exc).__name__}: {exc}"
            log.warning("source %s failed: %s", cfg.key, st.error, exc_info=log.isEnabledFor(10))
            self._record_failure(cfg.key, run_id, exc, int((time.monotonic() - started) * 1000))
            # Rows from this source were rolled back: forget their features too.
            for key in set(index.items) - keys_before:
                index.remove(key)
        finally:
            session.close()
            connector.close()
        return st

    def _record_failure(self, key: str, run_id: int, exc: Exception, latency_ms: int) -> None:
        with self.session_factory() as session:
            source = session.scalar(select(Source).where(Source.key == key))
            if source is None:
                return
            source.status = "error"
            source.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            source.last_error_at = self.now()
            session.add(SourceHealth(
                source_id=source.id, run_id=run_id, checked_at=self.now(), ok=False,
                http_status=getattr(exc, "http_status", None), latency_ms=latency_ms,
                error=source.last_error,
            ))  # fmt: skip
            session.commit()

    # ------------------------------------------------------------------ per item

    def _ingest_item(
        self,
        session: Session,
        cfg: SourceConfig,
        source: Source,
        run_id: int,
        fetched: FetchedItem,
        index: ClusterIndex,
        category_ids: dict[str, int],
        extractor: Connector | None,
    ) -> str:
        now = self.now()
        title = strip_html(fetched.title)
        if not title or not fetched.url:
            return "skipped"
        published = fetched.published_at or now
        if published > now + FUTURE_TOLERANCE:
            published = now

        canonical = canonicalize_url(fetched.url)
        ext_key = f"{cfg.key}:{fetched.external_id}"
        norm_title = normalize_title(title, cfg.display_publisher)
        title_hash = sha256(norm_title)
        window = timedelta(hours=self.settings.cluster_window_hours)

        # ---- stage 1: exact replays -------------------------------------------------
        dup = self._find_key(session, "external_id", ext_key) or self._find_key(
            session, "canonical_url", canonical
        )
        if dup is None:
            same_title = self._title_matches(session, title_hash, published, window)
            dup = next((d for d in same_title if d.source_id == source.id), None)
        if dup is not None:
            session.execute(
                update(RawItem).where(RawItem.id == dup.raw_item_id).values(last_seen_at=now)
            )
            return "replay"

        # ---- store permitted data ---------------------------------------------------
        stored = apply_policy(fetched, cfg, now, self.settings.excerpt_max_chars)
        if extractor is not None and cfg.policy == "indexable_content":
            stored.content = extract_full_text(extractor.http, fetched.url) or stored.content
        feature_text = stored.summary if cfg.policy != "metadata_only" else None
        # Classification-only text: used in memory, never stored (see classify_with_summary).
        classify_text = feature_text
        if classify_text is None and cfg.classify_with_summary:
            classify_text = strip_html(fetched.summary) or None
        title_entities = self.matcher.find(title)
        summary_entities = self.matcher.find(feature_text or "")
        numbers = extract_numbers(f"{title} {feature_text or ''}")
        classification = self.classifier.classify(
            title=title, summary=classify_text, section=fetched.section,
            title_entities=title_entities,
            summary_entities=summary_entities | self.matcher.find(classify_text or ""),
            default_categories=cfg.default_categories, section_map=cfg.section_map,
            exclude_categories=cfg.exclude_categories,
        )  # fmt: skip
        # Impact / sentiment of this report (description used in memory only, like above).
        impact = self.analyzer.analyze(
            title=title, summary=classify_text, source_key=cfg.key,
            entities=title_entities | summary_entities, numbers=numbers,
            categorized=bool(classification),
        )  # fmt: skip

        item = RawItem(
            source_id=source.id, run_id=run_id, external_id=fetched.external_id[:500],
            url=fetched.url, canonical_url=canonical, title=title, normalized_title=norm_title,
            summary=stored.summary, content=stored.content, section=fetched.section,
            authors=", ".join(fetched.authors) or None, published_at=published,
            fetched_at=now, last_seen_at=now, title_hash=title_hash,
            content_hash=sha256(stored.content) if stored.content else None,
            entities=sorted(title_entities | summary_entities), numbers=numbers,
            expires_at=stored.expires_at, delete_after=stored.delete_after,
            meta={
                **fetched.metadata,
                "classification": {
                    slug: {"score": c.score, "reasons": c.reasons}
                    for slug, c in classification.items()
                },
                "impact": impact.to_meta(),
            },
        )  # fmt: skip
        item.source = source
        session.add(item)
        session.flush()
        for kind, value in (("external_id", ext_key), ("canonical_url", canonical),
                            ("title_hash", title_hash)):  # fmt: skip
            session.add(DedupeKey(kind=kind, value=value, source_id=source.id, raw_item_id=item.id,
                                  created_at=now))  # fmt: skip

        # ---- stage 2: group into a story ---------------------------------------------
        feats = self._features(item, None)
        story: Story | None = None
        same_title = [d for d in self._title_matches(session, title_hash, published, window)
                      if d.raw_item_id != item.id]  # fmt: skip
        if same_title:
            member = session.scalar(
                select(StoryMember).where(StoryMember.raw_item_id == same_title[0].raw_item_id)
            )
            if member is not None:
                story = member.story
                add_member(session, story, item, method="identical_title", score=1.0,
                           reason={"rule": "identical_normalized_title", "score": 1.0,
                                   "matched_item": same_title[0].raw_item_id})  # fmt: skip
        if story is None:
            match = index.best_match(feats)
            if match is not None:
                story_id, decision, method = match
                story = session.get(Story, story_id)
                if story is not None:
                    reason = {**decision.as_reason(), "candidates": method}
                    add_member(session, story, item, method="cluster", score=decision.score,
                               reason=reason)  # fmt: skip
        outcome = "joined"
        if story is None:
            outcome = "new_story"
            story = Story(title=title, published_at=published, last_member_at=published,
                          created_at=now)  # fmt: skip
            session.add(story)
            session.flush()
            add_member(session, story, item, method="seed", score=None,
                       reason={"rule": "first_report"})  # fmt: skip
        session.flush()
        session.refresh(story, ["members"])
        refresh_story(session, story, category_ids)
        feats.story_id = story.id
        index.add(feats)
        return outcome

    @staticmethod
    def _find_key(session: Session, kind: str, value: str) -> DedupeKey | None:
        return session.scalar(
            select(DedupeKey).where(DedupeKey.kind == kind, DedupeKey.value == value).limit(1)
        )

    @staticmethod
    def _title_matches(
        session: Session, title_hash: str, published: datetime, window: timedelta
    ) -> list[DedupeKey]:
        return list(
            session.scalars(
                select(DedupeKey)
                .join(RawItem, RawItem.id == DedupeKey.raw_item_id)
                .where(
                    DedupeKey.kind == "title_hash",
                    DedupeKey.value == title_hash,
                    RawItem.published_at >= published - window,
                    RawItem.published_at <= published + window,
                )
                .order_by(DedupeKey.id)
            )
        )


def start_run(session: Session, kind: str) -> IngestionRun:
    run = IngestionRun(kind=kind, status="running", started_at=datetime.now(UTC))
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run_id: int, stats: RunStats | None, error: str | None) -> None:
    run = session.get(IngestionRun, run_id)
    if run is None:
        return
    run.finished_at = datetime.now(UTC)
    if error:
        run.status, run.error = "failed", error[:2000]
    else:
        assert stats is not None
        data = stats.as_dict()
        run.stats = data
        attempted = len(stats.sources)
        failed = data["errors"]
        run.status = "success" if failed == 0 else ("failed" if failed == attempted else "partial")


def abort_stale_runs(session: Session) -> int:
    """Runs left 'running' by a crash/restart can never finish: mark them aborted."""
    result = session.execute(
        update(IngestionRun)
        .where(IngestionRun.status == "running")
        .values(status="aborted", finished_at=datetime.now(UTC), error="process restarted")
    )
    session.execute(update(Source).where(Source.status == "running").values(status="never"))
    return int(getattr(result, "rowcount", 0) or 0)
