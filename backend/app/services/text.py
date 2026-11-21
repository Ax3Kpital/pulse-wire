"""Text normalisation helpers used by ingestion, deduplication and classification."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ocid", "cmpid", "ref", "ref_src",
    "src", "smid", "sh", "taid", "ito", "cid", "mod", "rss", "feed", "partner", "yptr",
    "guccounter",
}  # fmt: skip

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "from",
        "by",
        "with",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "into",
        "over",
        "under",
        "after",
        "before",
        "than",
        "then",
        "about",
        "amid",
        "against",
        "says",
        "say",
        "said",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "new",
        "up",
        "down",
        "out",
        "off",
        "not",
        "no",
        "yes",
        "he",
        "she",
        "they",
        "we",
        "you",
        "his",
        "her",
        "their",
        "our",
        "your",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "more",
        "most",
        "less",
        "least",
        "s",
        "us",
        "vs",
        "via",
        "per",
        "used",
        "use",
        "while",
        "since",
        "among",
        "amid",
        "after",
        "its",
        "just",
        "also",
    ]
)


# --------------------------------------------------------------------------- HTML / whitespace


class _Stripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    if "<" not in value and "&" not in value:
        return collapse_ws(value)
    parser = _Stripper()
    parser.feed(value)
    parser.close()
    return collapse_ws(html.unescape("".join(parser.parts)))


def collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    cut = value[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


# --------------------------------------------------------------------------- URLs


def canonicalize_url(url: str) -> str:
    """Stable URL form: lower-case host, no www, no fragment/tracking params, sorted query."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


# --------------------------------------------------------------------------- titles / tokens


def fold(value: str) -> str:
    """Lower-case, strip accents, unify quotes/dashes."""
    value = unicodedata.normalize("NFKD", html.unescape(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    value = value.replace("–", "-").replace("—", "-")
    return value.lower()


def normalize_title(title: str, publisher: str | None = None) -> str:
    text = fold(strip_html(title))
    if publisher:
        pub = re.escape(fold(publisher))
        text = re.sub(rf"\s*[-|:]\s*{pub}\s*$", "", text)
        text = re.sub(rf"^{pub}\s*[-|:]\s*", "", text)
    text = re.sub(r"[^\w\s%.$€£]", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)  # keep decimal points only
    return collapse_ws(text)


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.%$€£]*")


def stem(token: str) -> str:
    """Tiny deterministic stemmer: cuts/cut, rates/rate, raised/raises/raise all align."""
    if not token or token[0].isdigit() or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("ing", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3 and not token.endswith("ss"):
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    return token


def content_tokens(text: str) -> list[str]:
    folded = normalize_title(text)
    return [stem(t.rstrip(".")) for t in _TOKEN_RE.findall(folded) if t not in STOPWORDS]


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- numbers

_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mln": 1e6, "m": 1e6, "billion": 1e9,
          "bln": 1e9, "bn": 1e9, "b": 1e9, "trillion": 1e12, "tn": 1e12, "t": 1e12}  # fmt: skip

_PCT_RE = re.compile(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:%|per\s?cent\b|pct\b)", re.I)
_BP_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:basis[\s-]points?|bps?\b)", re.I)
_POINT_WORDS_RE = re.compile(r"\b(quarter|half)[\s-](?:percentage[\s-])?point\b", re.I)
_MONEY_RE = re.compile(
    r"([$€£])\s?(\d+(?:[.,]\d+)?)\s*(thousand|million|billion|trillion|mln|bln|bn|tn|k|m|b|t)?\b",
    re.I,
)


_BIG_NUM_RE = re.compile(r"(?<![\w.$€£])(\d{1,3}(?:,\d{3})+|\d{4,})(?![\w%]|\.\d)")


def extract_numbers(text: str) -> list[str]:
    """Salient figures as normalised tokens, e.g. ['pct:3.2', 'bp:25', 'money:$:1.5e+09',
    'num:142000']. Years (1900-2100) are ignored."""
    text = text or ""
    out: set[str] = set()
    for m in _BIG_NUM_RE.finditer(text):
        value = int(m.group(1).replace(",", ""))
        if not 1900 <= value <= 2100:
            out.add(f"num:{value}")
    for m in _PCT_RE.finditer(text):
        out.add(f"pct:{float(m.group(1)):g}")
    for m in _BP_RE.finditer(text):
        out.add(f"bp:{float(m.group(1)):g}")
    for m in _POINT_WORDS_RE.finditer(text):
        out.add("bp:25" if m.group(1).lower() == "quarter" else "bp:50")
    for m in _MONEY_RE.finditer(text):
        amount = float(m.group(2).replace(",", ""))
        scale = _SCALE.get((m.group(3) or "").lower(), 1.0)
        out.add(f"money:{m.group(1)}:{amount * scale:.4g}")
    return sorted(out)


# --------------------------------------------------------------------------- dates

_MONTH_ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_NUM = {m: i for i, m in enumerate(_MONTH_ABBR, start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}(?![a-z])", re.I)
_MONTH_DAY_RE = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?![\d:])", re.I)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def extract_dates(text: str) -> set[str]:
    """Calendar dates named in a headline, normalised to 'MM-DD' (year-less) or 'YYYY-MM-DD'."""
    out: set[str] = set()
    for m in _DAY_MONTH_RE.finditer(text or ""):
        day, month = int(m.group(1)), _MONTH_NUM[m.group(2).lower()]
        if 1 <= day <= 31:
            out.add(f"{month:02d}-{day:02d}")
    for m in _MONTH_DAY_RE.finditer(text or ""):
        month, day = _MONTH_NUM[m.group(1).lower()], int(m.group(2))
        if 1 <= day <= 31:
            out.add(f"{month:02d}-{day:02d}")
    for m in _NUMERIC_DATE_RE.finditer(text or ""):
        year = m.group(3) if len(m.group(3)) == 4 else "20" + m.group(3)
        out.add(f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    for m in _ISO_DATE_RE.finditer(text or ""):
        out.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return out
