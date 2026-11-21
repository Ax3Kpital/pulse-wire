"""Generic RSS/Atom connector (feedparser) for feeds the user is authorised to consume."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser

from app.connectors.base import Connector, ConnectorError, Cursor, FetchedItem, FetchResult

# Entries older than (watermark - slack) are skipped; replays inside the slack are
# cheap because the ingestion runner drops them by external id / canonical URL.
WATERMARK_SLACK = timedelta(days=2)


def _entry_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
    return None


def parse_feed(
    content: bytes, *, source_key: str, base_url: str | None = None
) -> list[FetchedItem]:
    """Parse RSS/Atom. Relative entry links (some feeds, e.g. EIA, use them) are resolved
    against `base_url`, so every stored link opens the original article."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ConnectorError(f"unparseable feed: {parsed.get('bozo_exception')!r}")
    items: list[FetchedItem] = []
    for entry in parsed.entries:
        link = entry.get("link") or ""
        if link and base_url:
            link = urljoin(base_url, link)
        if link and not urlsplit(link).scheme.startswith("http"):
            continue  # unusable link: never show an item we cannot attribute/open
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        tags = [t.get("term") for t in entry.get("tags", []) if t.get("term")]
        authors = [a.get("name") for a in entry.get("authors", []) if a.get("name")]
        content_value = None
        if entry.get("content"):
            content_value = entry["content"][0].get("value")
        items.append(
            FetchedItem(
                external_id=entry.get("id") or entry.get("guid") or link,
                url=link,
                title=title,
                summary=entry.get("summary") or entry.get("description"),
                content=content_value,
                section=tags[0] if tags else None,
                authors=authors,
                published_at=_entry_datetime(entry),
                metadata={"tags": tags[:10], "feed": source_key},
            )
        )
    return items


class RssConnector(Connector):
    def fetch_since(self, cursor: str | None) -> FetchResult:
        url = self.config.resolved_url()
        if not url:
            raise ConnectorError("no feed URL configured")
        if self.config.obey_robots and not self.http.allowed_by_robots(url):
            raise ConnectorError(f"robots.txt disallows {url}")
        state = Cursor.load(cursor)
        resp = self.http.get(
            url,
            purpose="feed",
            etag=state.etag,
            last_modified=state.last_modified,
            headers={
                "Accept": "application/rss+xml, application/atom+xml, application/xml, "
                "text/xml;q=0.9, */*;q=0.8"
            },
        )
        if resp.not_modified:
            return FetchResult([], state.dump(), True, resp.status, resp.latency_ms)

        items = parse_feed(resp.content, source_key=self.config.key, base_url=resp.url)
        floor = state.watermark_dt - WATERMARK_SLACK if state.watermark_dt else None
        if floor:
            items = [i for i in items if i.published_at is None or i.published_at >= floor]

        state.etag = resp.headers.get("ETag")
        state.last_modified = resp.headers.get("Last-Modified")
        state.advance(items)
        return FetchResult(items, state.dump(), False, resp.status, resp.latency_ms)
