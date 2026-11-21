"""DemoConnector: replays local fixtures so the whole app works with no network access.

Fixture items carry a `batch` number. Each fetch releases the next batch while re-emitting
everything already released (exactly like a real feed that keeps old entries), which exercises
cursor persistence, replay de-duplication and late articles joining existing stories.
Publication times are relative to the first fetch (`anchor`) so the demo always looks fresh.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.connectors.base import Connector, ConnectorError, Cursor, FetchedItem, FetchResult

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"


@lru_cache
def _load_fixture(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class DemoConnector(Connector):
    def __init__(self, *args: Any, now: datetime | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._now = now

    def fetch_since(self, cursor: str | None) -> FetchResult:
        opts = self.config.options
        if opts.get("simulate_error"):
            raise ConnectorError(f"simulated outage: {opts['simulate_error']} (demo)")
        state = Cursor.load(cursor)
        fetch_no = int(state.extra.get("fetches", 0)) + 1

        now = self._now or datetime.now(UTC)
        anchor = datetime.fromisoformat(state.extra.get("anchor") or now.isoformat())
        batch = int(state.extra.get("batch", -1)) + 1

        fixture_file = DEMO_DIR / opts.get("fixture", "fixtures.json")
        data = _load_fixture(str(fixture_file))
        feed_key = opts.get("feed", self.config.key)
        max_batch = max((i.get("batch", 0) for i in data["items"]), default=0)
        batch = min(batch, max_batch)

        items: list[FetchedItem] = []
        for raw in data["items"]:
            if raw["feed"] != feed_key or raw.get("batch", 0) > batch:
                continue
            published = anchor - timedelta(hours=float(raw["hours_ago"]))
            items.append(
                FetchedItem(
                    external_id=raw["id"],
                    url=raw["url"],
                    title=raw["title"],
                    summary=raw.get("summary"),
                    content=raw.get("content"),
                    section=raw.get("section"),
                    authors=raw.get("authors", []),
                    published_at=published,
                    metadata={"demo": True},
                )
            )

        state.extra.update({"anchor": anchor.isoformat(), "batch": batch, "fetches": fetch_no})
        state.advance(items)
        return FetchResult(items=items, cursor=state.dump(), http_status=200, latency_ms=1)
