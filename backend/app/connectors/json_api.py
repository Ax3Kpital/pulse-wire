"""Config-driven JSON API connector, plus skeletons for licensed publisher APIs.

`options` for `json_api` sources:

    items_path: "data.items"          # dotted path to the list of items
    fields:                           # item field -> dotted path inside each item
      id: id
      title: headline
      url: links.web
      published: published_at         # ISO 8601 or unix seconds
      summary: standfirst             # optional
      section: section.name           # optional
    auth:                             # optional
      header: Authorization
      template: "Bearer {api_key}"    # placeholders = keys of credentials_env
    params: {lang: en}                # optional static query params
    since_param: since                # optional: watermark sent as ISO 8601
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.connectors.base import Connector, ConnectorError, Cursor, FetchedItem, FetchResult


def dig(obj: Any, path: str | None) -> Any:
    if not path:
        return None
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), tz=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class JsonApiConnector(Connector):
    default_options: dict[str, Any] = {}

    def _options(self) -> dict[str, Any]:
        return {**self.default_options, **self.config.options}

    def _auth_headers(self, opts: dict[str, Any]) -> dict[str, str]:
        auth = opts.get("auth")
        if not auth:
            return {}
        creds = {f: self.config.credential(f) or "" for f in self.config.credentials_env}
        return {auth["header"]: auth["template"].format(**creds)}

    def fetch_since(self, cursor: str | None) -> FetchResult:
        url = self.config.resolved_url()
        if not url:
            raise ConnectorError("no API URL configured")
        opts = self._options()
        state = Cursor.load(cursor)
        params = {k: str(v) for k, v in (opts.get("params") or {}).items()}
        if opts.get("since_param") and state.watermark:
            params[opts["since_param"]] = state.watermark

        resp = self.http.get(
            url,
            purpose="api",
            etag=state.etag,
            last_modified=state.last_modified,
            headers={"Accept": "application/json", **self._auth_headers(opts)},
            params=params,
        )
        if resp.not_modified:
            return FetchResult([], state.dump(), True, resp.status, resp.latency_ms)

        items = self.map_items(resp.json(), opts)
        state.etag = resp.headers.get("ETag")
        state.last_modified = resp.headers.get("Last-Modified")
        state.advance(items)
        return FetchResult(items, state.dump(), False, resp.status, resp.latency_ms)

    def map_items(self, payload: Any, opts: dict[str, Any]) -> list[FetchedItem]:
        raw_items = dig(payload, opts.get("items_path")) if opts.get("items_path") else payload
        if not isinstance(raw_items, list):
            raise ConnectorError("API response does not contain an item list at items_path")
        fields = opts.get("fields") or {}
        out: list[FetchedItem] = []
        for raw in raw_items:
            title = dig(raw, fields.get("title", "title"))
            link = dig(raw, fields.get("url", "url"))
            if not title or not link:
                continue
            out.append(
                FetchedItem(
                    external_id=str(dig(raw, fields.get("id", "id")) or link),
                    url=str(link),
                    title=str(title),
                    summary=dig(raw, fields.get("summary")),
                    section=dig(raw, fields.get("section")),
                    published_at=parse_datetime(dig(raw, fields.get("published", "published"))),
                    metadata={"api": self.config.key},
                )
            )
        return out


class ReutersConnectConnector(JsonApiConnector):
    """Skeleton for a licensed Reuters Connect API contract.

    Stays disabled unless `REUTERS_CONNECT_API_URL` and credentials are present. Field mapping
    and auth must be adapted to your contract's API documentation (options in sources.yaml).
    It only ever calls the API endpoint (`purpose="api"`); article pages are never fetched.
    """

    default_options = {
        "auth": {"header": "Authorization", "template": "Bearer {access_token}"},
        "items_path": "data.items",
        "fields": {
            "id": "id",
            "title": "headline",
            "url": "uri",
            "published": "versionCreated",
            "summary": "caption",
        },  # fmt: skip
        "since_param": "dateRangeStart",
    }


class FinancialTimesConnector(JsonApiConnector):
    """Skeleton for the FT licensed content API (X-Api-Key header).

    Stays disabled unless `FT_API_URL` and `FT_API_KEY` are present. Adapt `fields` and
    `items_path` to the endpoint in your licence. No ft.com page is ever fetched.
    """

    default_options = {
        "auth": {"header": "X-Api-Key", "template": "{api_key}"},
        "items_path": "results.0.results",
        "fields": {
            "id": "id",
            "title": "title.title",
            "url": "location.uri",
            "published": "lifecycle.lastPublishDateTime",
            "summary": "summary.excerpt",
        },  # fmt: skip
    }
