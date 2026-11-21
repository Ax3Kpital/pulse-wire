"""Hard access rules that no configuration can override.

Reuters, Bloomberg, Financial Times and The Washington Post may only be consumed through
feeds/APIs the user is licensed for. Their article pages are never fetched (no scraping,
no full-text extraction), and the Scrapling connector refuses them entirely.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

PROTECTED_PUBLISHER_DOMAINS: frozenset[str] = frozenset(
    {
        "reuters.com",
        "reutersagency.com",
        "bloomberg.com",
        "bloomberg.net",
        "bloombergtax.com",
        "ft.com",
        "ft.net",
        "washingtonpost.com",
        "wapo.st",
    }
)

FetchPurpose = Literal["feed", "api", "page"]


class AccessPolicyError(RuntimeError):
    """Raised when a fetch would violate the access policy."""


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def is_protected_publisher(url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in PROTECTED_PUBLISHER_DOMAINS)


def assert_fetch_allowed(url: str, purpose: FetchPurpose) -> None:
    """Gate every outgoing request. Pages of protected publishers are always refused."""
    if urlsplit(url).scheme not in {"http", "https"}:
        raise AccessPolicyError(f"unsupported URL scheme: {url}")
    if purpose == "page" and is_protected_publisher(url):
        raise AccessPolicyError(
            f"refusing to fetch article page from protected publisher ({host_of(url)}); "
            "use a licensed feed/API instead"
        )
