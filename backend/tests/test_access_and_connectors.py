"""Access policy guarantees and network connectors (mocked transport, no real network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.config.loader import SourceConfig, load_sources
from app.connectors.base import Cursor
from app.connectors.http import ConnectorError, HttpClient, RateLimiter
from app.connectors.json_api import JsonApiConnector
from app.connectors.rss import RssConnector
from app.connectors.scrapling_connector import ScraplingConnector
from app.services.access_policy import (
    AccessPolicyError,
    assert_fetch_allowed,
    is_protected_publisher,
)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><title>Fed holds rates</title><link>https://www.federalreserve.gov/a.htm</link>
<guid>a1</guid><pubDate>Fri, 13 Nov 2026 18:00:00 GMT</pubDate><description>&lt;p&gt;Text&lt;/p&gt;</description>
<category>Monetary Policy</category></item>
<item><title>Old item</title><link>https://www.federalreserve.gov/old.htm</link>
<guid>a0</guid><pubDate>Mon, 01 Jun 2026 18:00:00 GMT</pubDate></item>
</channel></rss>"""


def client(handler, **kw) -> HttpClient:  # type: ignore[no-untyped-def]
    return HttpClient(transport=httpx.MockTransport(handler), sleep=lambda s: None,
                      rate_limiter=RateLimiter(sleep=lambda s: None), min_interval_seconds=0, **kw)  # fmt: skip


def rss_cfg(**kw) -> SourceConfig:  # type: ignore[no-untyped-def]
    return SourceConfig(key="t", name="T", connector="rss", enabled=True,
                        url="https://www.federalreserve.gov/feeds/x.xml", **kw)  # fmt: skip


# --------------------------------------------------------------------------- access policy


@pytest.mark.parametrize("url", [
    "https://www.reuters.com/markets/x", "https://www.bloomberg.com/news/a",
    "https://www.ft.com/content/1", "https://www.washingtonpost.com/b", "https://amp.ft.com/x",
])  # fmt: skip
def test_protected_pages_are_always_refused(url: str) -> None:
    assert is_protected_publisher(url)
    with pytest.raises(AccessPolicyError):
        assert_fetch_allowed(url, "page")
    assert_fetch_allowed(url, "feed")  # licensed feeds/APIs are allowed if user configures them


def test_lookalike_domains_not_confused() -> None:
    assert not is_protected_publisher("https://notreuters.com/x")
    assert not is_protected_publisher("https://raft.com/x")


def test_scrapling_config_refuses_protected_publishers() -> None:
    with pytest.raises(ValueError, match="never target"):
        SourceConfig(key="x", name="x", connector="scrapling", url="https://www.reuters.com/",
                     options={"allowlist": ["https://www.reuters.com/"]})  # fmt: skip


def test_full_text_extraction_refused_for_protected_publisher() -> None:
    with pytest.raises(ValueError):
        SourceConfig(key="x", name="x", connector="rss", url="https://feeds.bloomberg.com/x.rss",
                     policy="indexable_content", extract_full_text=True)  # fmt: skip


def test_default_config_never_scrapes_protected_publishers() -> None:
    for cfg in load_sources():
        for url in [cfg.url or "", *(cfg.options.get("allowlist") or [])]:
            if url and is_protected_publisher(url):
                assert cfg.connector != "scrapling"
                assert not cfg.extract_full_text
        if cfg.connector == "scrapling":
            assert not cfg.enabled or not cfg.options.get("permission_confirmed")


def test_licensed_sources_inactive_without_credentials() -> None:
    by_key = {c.key: c for c in load_sources()}
    for key in ("reuters-connect", "ft-api", "licensed-rss-1"):
        assert by_key[key].activation_problem() is not None


def test_redirect_to_protected_page_is_refused() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "example.org":
            return httpx.Response(302, headers={"Location": "https://www.ft.com/content/abc"})
        return httpx.Response(200, text="<html></html>")

    with pytest.raises(AccessPolicyError):
        client(handler).get("https://example.org/r", purpose="page")


# --------------------------------------------------------------------------- HTTP behaviour


def test_rss_conditional_headers_and_304() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200, content=RSS, headers={"ETag": '"v1"', "Last-Modified": "Fri, 13 Nov 2026"}
        )

    conn = RssConnector(rss_cfg(), http=client(handler))
    first = conn.fetch_since(None)
    assert [i.external_id for i in first.items] == ["a1", "a0"]
    assert first.items[0].section == "Monetary Policy"
    assert first.items[0].published_at == datetime(2026, 11, 13, 18, tzinfo=UTC)
    assert "PulseWire" in seen[0].headers["User-Agent"]

    second = conn.fetch_since(first.cursor)
    assert second.not_modified and second.items == []
    assert seen[1].headers["If-None-Match"] == '"v1"'
    assert seen[1].headers["If-Modified-Since"] == "Fri, 13 Nov 2026"
    assert second.cursor == first.cursor


def test_rss_watermark_skips_old_entries() -> None:
    conn = RssConnector(rss_cfg(), http=client(lambda r: httpx.Response(200, content=RSS)))
    cursor = Cursor(watermark="2026-11-10T00:00:00+00:00").dump()
    assert [i.external_id for i in conn.fetch_since(cursor).items] == ["a1"]


def test_retry_with_backoff_then_success() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, content=RSS)

    http = HttpClient(transport=httpx.MockTransport(handler), sleep=sleeps.append,
                      rate_limiter=RateLimiter(sleep=lambda s: None), min_interval_seconds=0)  # fmt: skip
    result = RssConnector(rss_cfg(), http=http).fetch_since(None)
    assert calls["n"] == 3 and len(result.items) == 2
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]  # exponential


def test_errors_are_visible_connector_errors() -> None:
    conn = RssConnector(rss_cfg(), http=client(lambda r: httpx.Response(404)))
    with pytest.raises(ConnectorError, match="HTTP 404"):
        conn.fetch_since(None)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(ConnectorError, match="network error"):
        RssConnector(rss_cfg(), http=client(boom)).fetch_since(None)


def test_rate_limiter_spaces_requests_per_host() -> None:
    sleeps: list[float] = []
    limiter = RateLimiter(sleep=sleeps.append)
    limiter.wait("a.gov", 2.0)
    limiter.wait("a.gov", 2.0)
    limiter.wait("b.gov", 2.0)
    assert len(sleeps) == 1 and 1.5 < sleeps[0] <= 2.0


def test_json_api_mapping_auth_and_since(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_API_KEY", "secret")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = {"data": {"items": [{"uid": "x1", "headline": "Banks rally", "link": "https://api.x/1",
                                    "ts": "2026-11-13T10:00:00Z"}]}}  # fmt: skip
        return httpx.Response(200, content=json.dumps(body).encode())

    cfg = SourceConfig(key="api", name="API", connector="json_api", enabled=True,
                       url="https://api.example.com/v1/news",
                       credentials_env={"api_key": "TEST_API_KEY"},
                       options={"items_path": "data.items", "since_param": "since",
                                "auth": {"header": "Authorization", "template": "Bearer {api_key}"},
                                "fields": {"id": "uid", "title": "headline", "url": "link",
                                           "published": "ts"}})  # fmt: skip
    conn = JsonApiConnector(cfg, http=client(handler))
    res = conn.fetch_since(Cursor(watermark="2026-11-12T00:00:00+00:00").dump())
    assert res.items[0].external_id == "x1" and res.items[0].title == "Banks rally"
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert seen[0].url.params["since"] == "2026-11-12T00:00:00+00:00"


# --------------------------------------------------------------------------- scrapling


def scrapling_cfg(**opts) -> SourceConfig:  # type: ignore[no-untyped-def]
    return SourceConfig(key="s", name="S", connector="scrapling", enabled=True,
                        url="https://example.org/news/",
                        options={"allowlist": ["https://example.org/news/"],
                                 "permission_confirmed": True, "mode": "feed", **opts})  # fmt: skip


def test_scrapling_obeys_robots_txt() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /news/\n")
        return httpx.Response(200, content=RSS)

    with pytest.raises(ConnectorError, match="robots.txt disallows"):
        ScraplingConnector(scrapling_cfg(), http=client(handler)).fetch_since(None)


def test_scrapling_allowed_feed_and_allowlist_enforced() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=RSS)

    res = ScraplingConnector(scrapling_cfg(), http=client(handler)).fetch_since(None)
    assert len(res.items) == 2

    cfg = scrapling_cfg()
    cfg.url = "https://example.org/private/"
    with pytest.raises(ConnectorError, match="allowlist"):
        ScraplingConnector(cfg, http=client(handler)).fetch_since(None)


def test_scrapling_requires_permission_confirmation() -> None:
    cfg = scrapling_cfg(permission_confirmed=False)
    assert "permission_confirmed" in (cfg.activation_problem() or "")


def test_relative_feed_links_are_resolved() -> None:
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>EIA</title>
    <item><title>EIA forecasts demand</title><link>/pressroom/releases/press582.php</link>
    <guid>press582</guid><pubDate>Fri, 13 Nov 2026 18:00:00 GMT</pubDate></item>
    <item><title>No usable link</title><link>javascript:void(0)</link><guid>x</guid></item>
    </channel></rss>"""
    cfg = SourceConfig(
        key="eia",
        name="EIA",
        connector="rss",
        enabled=True,
        url="https://www.eia.gov/rss/press_rss.xml",
    )
    res = RssConnector(cfg, http=client(lambda r: httpx.Response(200, content=feed))).fetch_since(
        None
    )
    assert [i.url for i in res.items] == ["https://www.eia.gov/pressroom/releases/press582.php"]


def test_protected_publishers_are_feed_only_and_metadata_only() -> None:
    """FT / Bloomberg / WaPo public RSS: headline+link only, robots-checked, never page fetches."""
    protected = [c for c in load_sources() if c.url and is_protected_publisher(c.url)]
    assert {c.display_publisher for c in protected} >= {
        "Financial Times",
        "Bloomberg",
        "The Washington Post",
    }
    for cfg in protected:
        assert cfg.connector == "rss"
        assert cfg.policy == "metadata_only"
        assert not cfg.extract_full_text
        assert cfg.obey_robots


def test_rss_obeys_robots_when_configured() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /feeds/\n")
        return httpx.Response(200, content=RSS)

    cfg = rss_cfg(obey_robots=True)
    with pytest.raises(ConnectorError, match="robots.txt"):
        RssConnector(cfg, http=client(handler)).fetch_since(None)


def test_metadata_only_feed_summary_used_for_classification_but_not_stored(
    session_factory, make_service, run_scan
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.connectors.base import FetchedItem, FetchResult
    from app.models import RawItem, Story

    cfg = SourceConfig(
        key="pub",
        name="Publisher",
        connector="rss",
        enabled=True,
        url="https://feeds.example.com/x.rss",
        policy="metadata_only",
        classify_with_summary=True,
    )

    class Fake:
        def __init__(self, c):  # type: ignore[no-untyped-def]
            self.config = c

        def fetch_since(self, cursor):  # type: ignore[no-untyped-def]
            return FetchResult(
                [
                    FetchedItem(
                        external_id="1",
                        url="https://feeds.example.com/a",
                        title="Shares slide on Friday",
                        summary="Nvidia and other chipmakers fell after new semiconductor export rules.",
                        published_at=None,
                    )
                ],
                cursor="{}",
            )

        def close(self) -> None: ...

    run_scan(make_service(configs=[cfg], connector_factory=Fake))
    with session_factory() as s:
        item = s.scalar(select(RawItem))
        assert item.summary is None and item.content is None  # nothing stored
        story = s.scalar(select(Story))
        assert "tech" in {c.category.slug for c in story.categories}  # yet classified correctly


def test_reclassify_keeps_summary_signals_of_unstored_descriptions(
    session_factory, make_service, run_scan, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.connectors.base import FetchedItem, FetchResult
    from app.models import Story
    from app.services import reclassify

    cfg = SourceConfig(
        key="pub",
        name="Publisher",
        connector="rss",
        enabled=True,
        url="https://feeds.example.com/x.rss",
        policy="metadata_only",
        classify_with_summary=True,
    )

    class Fake:
        def __init__(self, c):  # type: ignore[no-untyped-def]
            self.config = c

        def fetch_since(self, cursor):  # type: ignore[no-untyped-def]
            return FetchResult(
                [
                    FetchedItem(
                        external_id="1",
                        url="https://feeds.example.com/a",
                        title="Shares slide on Friday",
                        summary="Chipmakers fell after new semiconductor export rules.",
                        published_at=None,
                    )
                ],
                cursor="{}",
            )

        def close(self) -> None: ...

    run_scan(make_service(configs=[cfg], connector_factory=Fake))
    monkeypatch.setattr(reclassify, "load_sources", lambda: [cfg])
    with session_factory() as s:
        reclassify.reclassify_all(s)
        s.commit()
        story = s.scalar(select(Story))
        assert "tech" in {c.category.slug for c in story.categories}
