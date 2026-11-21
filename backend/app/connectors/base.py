"""Common connector interface: `fetch_since(cursor) -> FetchResult`."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config.loader import SourceConfig
from app.connectors.http import ConnectorError, HttpClient

__all__ = ["Connector", "ConnectorError", "Cursor", "FetchResult", "FetchedItem"]


@dataclass
class FetchedItem:
    external_id: str
    url: str
    title: str
    published_at: datetime | None
    summary: str | None = None
    content: str | None = None
    section: str | None = None
    authors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[FetchedItem]
    cursor: str | None
    not_modified: bool = False
    http_status: int | None = None
    latency_ms: int | None = None


@dataclass
class Cursor:
    """Persistent per-source state, serialised as JSON in `sources.cursor`."""

    etag: str | None = None
    last_modified: str | None = None
    watermark: str | None = None  # ISO timestamp of the newest item seen
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, raw: str | None) -> Cursor:
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls()
        return cls(**{k: data.get(k) for k in ("etag", "last_modified", "watermark")},
                   extra=data.get("extra") or {})  # fmt: skip

    def dump(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @property
    def watermark_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.watermark) if self.watermark else None

    def advance(self, items: list[FetchedItem]) -> None:
        dates = [i.published_at for i in items if i.published_at]
        if dates:
            newest = max(dates)
            current = self.watermark_dt
            if current is None or newest > current:
                self.watermark = newest.astimezone(UTC).isoformat()


class Connector(ABC):
    """Base class. Subclasses must be side-effect free apart from network I/O."""

    def __init__(self, config: SourceConfig, http: HttpClient | None = None) -> None:
        self.config = config
        self._http = http

    @property
    def http(self) -> HttpClient:
        if self._http is None:
            self._http = HttpClient(min_interval_seconds=self.config.min_interval_seconds)
        return self._http

    @abstractmethod
    def fetch_since(self, cursor: str | None) -> FetchResult:
        """Return items newer than `cursor` (best effort) and the next cursor."""

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
