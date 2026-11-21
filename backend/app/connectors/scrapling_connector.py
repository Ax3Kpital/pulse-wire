"""ScraplingConnector: disabled by default, allowlist-only, robots.txt-obeying.

Only Scrapling's HTML parser is used (`scrapling.parser.Selector`, base install). Its
`fetchers` extra is deliberately NOT used: it ships Playwright/stealth browsers, which this
project forbids. Requests go through our HttpClient (identifiable UA, rate limit, retries).

`options`:
    permission_confirmed: true          # you confirm the site permits automated access
    allowlist: ["https://example.org/news/"]   # URL prefixes that may be requested
    mode: feed | page                   # feed = RSS/XML via feedparser; page = HTML listing
    # page mode selectors (CSS):
    item_selector: "article"
    title_selector: "h2 a::text"
    link_selector: "h2 a::attr(href)"
    date_selector: "time::attr(datetime)"   # optional
    summary_selector: "p::text"             # optional
"""

from __future__ import annotations

from urllib.parse import urljoin

from app.connectors.base import Connector, ConnectorError, Cursor, FetchedItem, FetchResult
from app.connectors.json_api import parse_datetime
from app.connectors.rss import parse_feed
from app.services.access_policy import is_protected_publisher


class ScraplingConnector(Connector):
    def _check_url(self, url: str) -> None:
        if is_protected_publisher(url):
            raise ConnectorError("Scrapling may never target Reuters/Bloomberg/FT/WaPo")
        allowlist: list[str] = self.config.options.get("allowlist") or []
        if not any(url.startswith(prefix) for prefix in allowlist):
            raise ConnectorError(f"URL not in allowlist: {url}")
        if not self.config.options.get("permission_confirmed"):
            raise ConnectorError("permission_confirmed is not set for this source")
        if not self.http.allowed_by_robots(url):
            raise ConnectorError(f"robots.txt disallows {url}")

    def fetch_since(self, cursor: str | None) -> FetchResult:
        url = self.config.resolved_url()
        if not url:
            raise ConnectorError("no URL configured")
        self._check_url(url)
        opts = self.config.options
        state = Cursor.load(cursor)
        resp = self.http.get(url, purpose="feed" if opts.get("mode") == "feed" else "page",
                             etag=state.etag, last_modified=state.last_modified)  # fmt: skip
        if resp.not_modified:
            return FetchResult([], state.dump(), True, resp.status, resp.latency_ms)

        if opts.get("mode", "feed") == "feed":
            items = parse_feed(resp.content, source_key=self.config.key, base_url=resp.url)
        else:
            items = self._parse_page(resp.content.decode("utf-8", "replace"), resp.url)

        state.etag = resp.headers.get("ETag")
        state.last_modified = resp.headers.get("Last-Modified")
        state.advance(items)
        return FetchResult(items, state.dump(), False, resp.status, resp.latency_ms)

    def _parse_page(self, html: str, base_url: str) -> list[FetchedItem]:
        try:
            from scrapling.parser import Selector
        except ImportError as exc:  # optional dependency
            raise ConnectorError(
                "scrapling not installed: pip install -e 'backend[scrapling]'"
            ) from exc
        opts = self.config.options
        page = Selector(html, url=base_url)
        items: list[FetchedItem] = []
        for node in page.css(opts.get("item_selector", "article")):
            title = node.css(opts["title_selector"]).get()
            href = node.css(opts["link_selector"]).get()
            if not title or not href:
                continue
            link = urljoin(base_url, str(href))
            date = node.css(opts["date_selector"]).get() if opts.get("date_selector") else None
            summary = (
                node.css(opts["summary_selector"]).get() if opts.get("summary_selector") else None
            )
            items.append(
                FetchedItem(
                    external_id=link,
                    url=link,
                    title=str(title).strip(),
                    summary=str(summary).strip() if summary else None,
                    published_at=parse_datetime(date),
                    metadata={"scrapling": True},
                )
            )
        return items
