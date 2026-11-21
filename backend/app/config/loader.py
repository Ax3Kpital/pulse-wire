"""Typed loaders for sources.yaml, categories.yaml and entities/*.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from app.config.settings import get_secret, get_settings
from app.services.access_policy import is_protected_publisher

Policy = Literal["metadata_only", "temporary_excerpt", "indexable_content"]
ConnectorType = Literal["demo", "rss", "json_api", "reuters_connect", "ft", "scrapling"]


# --------------------------------------------------------------------------- sources


class SourceConfig(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    publisher: str | None = None
    connector: ConnectorType
    enabled: bool = False
    demo: bool = False

    url: str | None = None
    url_env: str | None = None  # name of an env var holding a private URL
    credentials_env: dict[str, str] = Field(default_factory=dict)  # field -> env var name
    homepage: str | None = None

    policy: Policy = "metadata_only"
    retention_days: int | None = 30  # how long excerpt/content may be kept
    metadata_retention_days: int | None = None  # None = keep metadata indefinitely
    extract_full_text: bool = False  # only honoured for indexable_content
    # metadata_only sources: use the feed's description in memory to classify the item, without
    # ever storing it (only derived category labels are kept).
    classify_with_summary: bool = False
    obey_robots: bool = False  # check robots.txt before fetching the feed itself

    priority: int = 50  # higher wins when choosing a story's primary source
    paywalled: bool = False  # articles need a subscription; never chosen as primary if a free
    # report of the same story exists, and flagged in the UI
    default_categories: list[str] = Field(default_factory=list)
    exclude_categories: list[str] = Field(default_factory=list)  # never assign (incl. children)
    section_map: dict[str, list[str]] = Field(default_factory=dict)
    min_interval_seconds: float | None = None
    requires_contact: bool = False  # publisher requires contact info in the User-Agent
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> SourceConfig:
        if self.connector == "scrapling":
            allow = self.options.get("allowlist") or []
            if not allow:
                raise ValueError(f"{self.key}: scrapling connector needs options.allowlist")
            for url in [*allow, self.url or ""]:
                if url and is_protected_publisher(url):
                    raise ValueError(
                        f"{self.key}: scrapling may never target Reuters/Bloomberg/FT/WaPo"
                    )
        if self.extract_full_text and self.policy != "indexable_content":
            raise ValueError(f"{self.key}: extract_full_text requires policy indexable_content")
        if self.extract_full_text and self.url and is_protected_publisher(self.url):
            raise ValueError(f"{self.key}: full-text extraction is not allowed for this publisher")
        return self

    @property
    def display_publisher(self) -> str:
        return self.publisher or self.name

    def resolved_url(self) -> str | None:
        return get_secret(self.url_env) or self.url

    def credential(self, field: str) -> str | None:
        return get_secret(self.credentials_env.get(field))

    def activation_problem(self) -> str | None:
        """Why this source cannot run right now (None = runnable)."""
        settings = get_settings()
        if not self.enabled:
            return "disabled in sources.yaml"
        if self.demo and not settings.demo_enabled:
            return "demo sources disabled (NEWS_DEMO_ENABLED=false)"
        if self.connector != "demo" and not self.resolved_url():
            env = f" (set {self.url_env})" if self.url_env else ""
            return f"no authorised URL configured{env}"
        missing = [f for f, env in self.credentials_env.items() if not get_secret(env)]
        if missing:
            names = ", ".join(self.credentials_env[f] for f in missing)
            return f"missing credentials: {names}"
        if self.requires_contact and not settings.contact:
            return "publisher requires contact info in the User-Agent (set NEWS_CONTACT)"
        if self.connector == "scrapling" and not self.options.get("permission_confirmed"):
            return "scrapling source requires options.permission_confirmed: true"
        return None


class SourcesFile(BaseModel):
    sources: list[SourceConfig]

    @model_validator(mode="after")
    def _unique(self) -> SourcesFile:
        keys = [s.key for s in self.sources]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate source keys: {sorted(dupes)}")
        return self


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    path = path or get_settings().sources_file
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return SourcesFile.model_validate(data).sources


# --------------------------------------------------------------------------- categories


class CategoryRule(BaseModel):
    slug: str
    name: str
    sections: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    strong_keywords: list[str] = Field(default_factory=list)
    requires_parent: bool = False
    # Dropped when one of these entities is present and none of the category's own
    # supporting entities is (e.g. an ECB rate decision is not a FED story).
    exclude_entities: list[str] = Field(default_factory=list)
    children: list[CategoryRule] = Field(default_factory=list)
    parent: str | None = None  # filled by the loader


class Taxonomy(BaseModel):
    threshold: float = 2.0
    categories: list[CategoryRule]

    def flat(self) -> list[CategoryRule]:
        out: list[CategoryRule] = []
        for cat in self.categories:
            out.append(cat)
            for child in cat.children:
                child.parent = cat.slug
                child.exclude_entities = child.exclude_entities or list(cat.exclude_entities)
                out.append(child)
        return out


@lru_cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    path = path or get_settings().categories_file
    return Taxonomy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- entities


class EntityDef(BaseModel):
    id: str
    type: str = "company"
    aliases: list[str] = Field(default_factory=list)
    cs_aliases: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    weight: float = 1.0


@lru_cache
def load_entities(directory: Path | None = None) -> tuple[EntityDef, ...]:
    directory = directory or get_settings().entities_dir
    out: list[EntityDef] = []
    for file in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        out.extend(EntityDef.model_validate(e) for e in data.get("entities", []))
    return tuple(out)
