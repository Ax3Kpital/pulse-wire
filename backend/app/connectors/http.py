"""Polite HTTP client shared by all network connectors.

Every request goes through `assert_fetch_allowed`, a per-host rate limiter, a timeout and
retry with exponential backoff. Conditional requests (ETag / If-Modified-Since) are built in.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib import robotparser
from urllib.parse import urlsplit

import httpx

from app.config.settings import get_settings
from app.services.access_policy import FetchPurpose, assert_fetch_allowed, host_of

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class ConnectorError(RuntimeError):
    """A visible, per-source failure. Never stops other sources."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass
class HttpResponse:
    status: int
    content: bytes
    headers: httpx.Headers
    url: str
    latency_ms: int

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def json(self) -> object:
        import json

        return json.loads(self.content)


class RateLimiter:
    """Minimum interval between requests to the same host (process-wide)."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}
        self._sleep = sleep

    def wait(self, host: str, min_interval: float) -> None:
        with self._lock:
            now = time.monotonic()
            ready_at = self._next_allowed.get(host, now)
            delay = max(0.0, ready_at - now)
            self._next_allowed[host] = max(now, ready_at) + min_interval
        if delay > 0:
            self._sleep(delay)


_rate_limiter = RateLimiter()


@dataclass
class HttpClient:
    min_interval_seconds: float | None = None
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    rate_limiter: RateLimiter = field(default_factory=lambda: _rate_limiter)
    _robots: dict[str, tuple[float, robotparser.RobotFileParser]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        s = get_settings()
        self.timeout = s.http_timeout_seconds
        self.max_retries = s.http_max_retries
        self.user_agent = s.user_agent
        if self.min_interval_seconds is None:
            self.min_interval_seconds = s.default_min_interval_seconds
        self._client = httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            transport=self.transport,
        )

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        purpose: FetchPurpose,
        etag: str | None = None,
        last_modified: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> HttpResponse:
        return self.request("GET", url, purpose=purpose, etag=etag, last_modified=last_modified,
                            headers=headers, params=params)  # fmt: skip

    def request(
        self,
        method: str,
        url: str,
        *,
        purpose: FetchPurpose,
        etag: str | None = None,
        last_modified: str | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> HttpResponse:
        assert_fetch_allowed(url, purpose)
        req_headers = dict(headers or {})
        if etag:
            req_headers["If-None-Match"] = etag
        if last_modified:
            req_headers["If-Modified-Since"] = last_modified

        attempt = 0
        while True:
            attempt += 1
            self.rate_limiter.wait(host_of(url), self.min_interval_seconds or 0)
            started = time.monotonic()
            try:
                resp = self._client.request(
                    method, url, headers=req_headers, params=params, data=data
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > self.max_retries:
                    raise ConnectorError(f"network error after {attempt} attempts: {exc}") from exc
                self._backoff(attempt, None)
                continue
            latency = int((time.monotonic() - started) * 1000)
            # Redirects must not land on a page we are not allowed to fetch.
            assert_fetch_allowed(str(resp.url), purpose)
            if resp.status_code in RETRYABLE_STATUS and attempt <= self.max_retries:
                self._backoff(attempt, resp.headers.get("Retry-After"))
                continue
            if resp.status_code >= 400:
                raise ConnectorError(
                    f"HTTP {resp.status_code} from {host_of(url)}", http_status=resp.status_code
                )
            return HttpResponse(
                resp.status_code, resp.content, resp.headers, str(resp.url), latency
            )

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(30.0, 1.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.5)
        if retry_after and retry_after.isdigit():
            delay = min(60.0, max(delay, float(retry_after)))
        log.info("retrying in %.1fs (attempt %d)", delay, attempt)
        self.sleep(delay)

    # ---------------------------------------------------------------- robots.txt

    def allowed_by_robots(self, url: str) -> bool:
        """Check robots.txt for our user agent (cached 6h per host). Missing robots = allowed."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        cached = self._robots.get(origin)
        if cached is None or time.monotonic() - cached[0] > 6 * 3600:
            parser = robotparser.RobotFileParser()
            try:
                resp = self.get(f"{origin}/robots.txt", purpose="feed")
                parser.parse(resp.content.decode("utf-8", "replace").splitlines())
            except ConnectorError as exc:
                if exc.http_status in (401, 403):
                    parser.disallow_all = True  # type: ignore[attr-defined]
                else:  # 404 or unreachable robots.txt => no restrictions declared
                    parser.parse([])
            cached = (time.monotonic(), parser)
            self._robots[origin] = cached
        return cached[1].can_fetch(self.user_agent, url)
