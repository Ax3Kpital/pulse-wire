"""SQLAlchemy ORM models. Timestamps are stored as timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """SQLite has no tz support: store naive UTC, always return aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    publisher: Mapped[str] = mapped_column(String(200))
    connector: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    policy: Mapped[str] = mapped_column(String(32))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    metadata_retention_days: Mapped[int | None] = mapped_column(Integer)
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="never")
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_fetch_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # scheduled | manual
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None]
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    coalesced_requests: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class RawItem(Base):
    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_raw_items_source_external"),
        Index("ix_raw_items_published_at", "published_at"),
        Index("ix_raw_items_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    external_id: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    normalized_title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(200))
    authors: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime]
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    title_hash: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    entities: Mapped[list[Any]] = mapped_column(default=list)
    numbers: Mapped[list[Any]] = mapped_column(default=list)
    expires_at: Mapped[datetime | None]  # text (summary/content) purge deadline
    delete_after: Mapped[datetime | None]  # whole-row deletion deadline
    text_purged_at: Mapped[datetime | None]

    source: Mapped[Source] = relationship(lazy="joined")


class Story(Base):
    __tablename__ = "stories"
    __table_args__ = (Index("ix_stories_published_at", "published_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    primary_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_items.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime]  # earliest member publication
    last_member_at: Mapped[datetime]  # latest member publication
    member_count: Mapped[int] = mapped_column(Integer, default=1)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    impact_score: Mapped[int] = mapped_column(Integer, default=1, index=True)  # 1..10
    impact_level: Mapped[str] = mapped_column(String(16), default="low")
    impact_reasons: Mapped[list[Any]] = mapped_column(default=list)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # tone -1..+1
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    members: Mapped[list[StoryMember]] = relationship(
        back_populates="story", cascade="all, delete-orphan", order_by="StoryMember.id"
    )
    categories: Mapped[list[StoryCategory]] = relationship(
        back_populates="story", cascade="all, delete-orphan"
    )


class StoryMember(Base):
    __tablename__ = "story_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), unique=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    method: Mapped[str] = mapped_column(String(32))  # seed | identical_title | cluster
    match_score: Mapped[float | None] = mapped_column(Float)
    match_reason: Mapped[dict[str, Any]] = mapped_column(default=dict)
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)

    story: Mapped[Story] = relationship(back_populates="members")
    item: Mapped[RawItem] = relationship(lazy="joined")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class StoryCategory(Base):
    __tablename__ = "story_categories"

    story_id: Mapped[int] = mapped_column(
        ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[Any]] = mapped_column(default=list)

    story: Mapped[Story] = relationship(back_populates="categories")
    category: Mapped[Category] = relationship(lazy="joined")


class DedupeKey(Base):
    __tablename__ = "dedupe_keys"
    __table_args__ = (Index("ix_dedupe_keys_kind_value", "kind", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # external_id | canonical_url | title_hash
    value: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    raw_item_id: Mapped[int] = mapped_column(ForeignKey("raw_items.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SourceHealth(Base):
    __tablename__ = "source_health"
    __table_args__ = (Index("ix_source_health_source_checked", "source_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id", ondelete="SET NULL"))
    checked_at: Mapped[datetime] = mapped_column(default=utcnow)
    ok: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    items_fetched: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    not_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
