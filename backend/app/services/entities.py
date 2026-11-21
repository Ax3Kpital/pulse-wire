"""Dictionary-based entity matcher (deterministic, no NLP model)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.config.loader import EntityDef, load_entities


@dataclass(frozen=True)
class EntityMatcher:
    entities: dict[str, EntityDef]
    patterns: tuple[tuple[str, re.Pattern[str]], ...]

    def find(self, text: str) -> set[str]:
        if not text:
            return set()
        return {eid for eid, pat in self.patterns if pat.search(text)}

    def replace(self, text: str) -> tuple[str, set[str]]:
        """Remove entity mentions from `text`, returning the remainder and the entity ids."""
        found: set[str] = set()
        for eid, pat in self.patterns:
            text, n = pat.subn(" ", text)
            if n:
                found.add(eid)
        return text, found

    def kind(self, entity_id: str) -> str:
        ent = self.entities.get(entity_id)
        return ent.type if ent else "unknown"

    def weight(self, entity_id: str) -> float:
        ent = self.entities.get(entity_id)
        return ent.weight if ent else 1.0


def _alias_pattern(alias: str, case_sensitive: bool) -> str:
    escaped = re.escape(alias)
    return rf"(?<![\w.]){escaped}(?![\w])" if case_sensitive else rf"(?<!\w){escaped}(?!\w)"


@lru_cache
def get_matcher() -> EntityMatcher:
    return build_matcher(load_entities())


def build_matcher(defs: tuple[EntityDef, ...]) -> EntityMatcher:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for ent in defs:
        parts = [_alias_pattern(a, False) for a in ent.aliases]
        cs_parts = [_alias_pattern(a, True) for a in ent.cs_aliases]
        if parts:
            patterns.append((ent.id, re.compile("|".join(parts), re.I)))
        if cs_parts:
            patterns.append((ent.id, re.compile("|".join(cs_parts))))
    return EntityMatcher({e.id: e for e in defs}, tuple(patterns))
