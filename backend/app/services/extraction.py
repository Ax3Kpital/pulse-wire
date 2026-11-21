"""Optional full-text extraction with trafilatura (only for `indexable_content` sources that set
`extract_full_text: true`). Protected publishers are refused by the access policy, robots.txt is
honoured, and requests use the shared polite HTTP client."""

from __future__ import annotations

import logging

from app.connectors.http import ConnectorError, HttpClient
from app.services.access_policy import AccessPolicyError

log = logging.getLogger(__name__)


def extract_full_text(http: HttpClient, url: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        log.warning(
            "trafilatura not installed; skipping extraction (pip install -e 'backend[extract]')"
        )
        return None
    try:
        if not http.allowed_by_robots(url):
            log.info("robots.txt disallows %s; not extracting", url)
            return None
        resp = http.get(url, purpose="page", headers={"Accept": "text/html"})
    except (ConnectorError, AccessPolicyError) as exc:
        log.info("extraction skipped for %s: %s", url, exc)
        return None
    text = trafilatura.extract(resp.content.decode("utf-8", "replace"), include_comments=False)
    return text or None
