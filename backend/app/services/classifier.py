"""Explainable, rule-based multi-label classifier (no external AI model).

Every assigned category carries the list of signals that produced its score, so a wrong
label can be traced to one keyword/entity/section rule and fixed in categories.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.config.loader import CategoryRule, Taxonomy, load_entities, load_taxonomy
from app.services.entities import get_matcher

W_SOURCE_DEFAULT = 2.0
W_SECTION = 2.0
W_STRONG_TITLE, W_STRONG_SUMMARY = 2.0, 1.0
W_KEYWORD_TITLE, W_KEYWORD_SUMMARY = 1.25, 0.6
W_ENTITY_TITLE, W_ENTITY_SUMMARY = 2.0, 1.0
MAX_KEYWORD_SIGNALS = 4  # cap so long summaries cannot dominate


@dataclass
class CategoryScore:
    slug: str
    score: float = 0.0
    reasons: list[dict[str, Any]] = field(default_factory=list)

    def add(self, signal: str, value: str, weight: float, where: str | None = None) -> None:
        self.score += weight
        reason: dict[str, Any] = {"signal": signal, "value": value, "weight": weight}
        if where:
            reason["field"] = where
        self.reasons.append(reason)


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    prefix = keyword.endswith("*")
    word = keyword.rstrip("*")
    body = re.escape(word) + (r"\w*" if prefix else "")
    # Acronyms/short all-caps keywords (US, AI, CPI, EU) are case-sensitive.
    case_sensitive = not any(c.islower() for c in word)
    return re.compile(rf"(?<![\w]){body}(?![\w])", 0 if case_sensitive else re.I)


@dataclass
class _CompiledRule:
    rule: CategoryRule
    strong: list[tuple[str, re.Pattern[str]]]
    normal: list[tuple[str, re.Pattern[str]]]


class Classifier:
    def __init__(self, taxonomy: Taxonomy, entity_categories: dict[str, list[str]]) -> None:
        self.taxonomy = taxonomy
        self.threshold = taxonomy.threshold
        self.rules = {
            r.slug: _CompiledRule(
                r,
                [(k, _keyword_pattern(k)) for k in r.strong_keywords],
                [(k, _keyword_pattern(k)) for k in r.keywords],
            )
            for r in taxonomy.flat()
        }
        self.entity_categories = entity_categories

    def classify(
        self,
        *,
        title: str,
        summary: str | None,
        section: str | None,
        title_entities: set[str],
        summary_entities: set[str],
        default_categories: list[str] | None = None,
        section_map: dict[str, list[str]] | None = None,
        exclude_categories: list[str] | None = None,
        prior_summary_signals: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, CategoryScore]:
        """`prior_summary_signals`: summary-derived reasons recorded at ingestion for sources whose
        text may not be stored (metadata_only + classify_with_summary); used by reclassify."""
        scores = {slug: CategoryScore(slug) for slug in self.rules}
        summary = summary or ""
        section_l = (section or "").strip().lower()

        for slug in default_categories or []:
            if slug in scores:
                scores[slug].add("source_default", slug, W_SOURCE_DEFAULT)

        if section_l:
            mapped = {k.lower(): v for k, v in (section_map or {}).items()}.get(section_l, [])
            for slug in mapped:
                if slug in scores:
                    scores[slug].add("source_section", section or "", W_SECTION)
            for slug, compiled in self.rules.items():
                if slug in mapped:
                    continue
                hit = next((s for s in compiled.rule.sections
                            if re.search(rf"(?<!\w){re.escape(s.lower())}(?!\w)", section_l)),
                           None)  # fmt: skip
                if hit:
                    scores[slug].add("section_rule", hit, W_SECTION)

        # Keywords are matched on text with entity names removed ("Bank of England" must not
        # count as the keyword "bank"); entities contribute through their own categories.
        matcher = get_matcher()
        title_kw = matcher.replace(title)[0]
        summary_kw = matcher.replace(summary)[0] if summary else ""
        for slug, compiled in self.rules.items():
            hits = 0
            for kw, pat, wt, ws in [
                *((k, p, W_STRONG_TITLE, W_STRONG_SUMMARY) for k, p in compiled.strong),
                *((k, p, W_KEYWORD_TITLE, W_KEYWORD_SUMMARY) for k, p in compiled.normal),
            ]:
                if hits >= MAX_KEYWORD_SIGNALS:
                    break
                if pat.search(title_kw):
                    scores[slug].add("keyword", kw, wt, "title")
                    hits += 1
                elif summary_kw and pat.search(summary_kw):
                    scores[slug].add("keyword", kw, ws, "summary")
                    hits += 1

        for entity in sorted(title_entities | summary_entities):
            in_title = entity in title_entities
            for slug in self.entity_categories.get(entity, []):
                if slug in scores:
                    weight = W_ENTITY_TITLE if in_title else W_ENTITY_SUMMARY
                    scores[slug].add("entity", entity, weight, "title" if in_title else "summary")

        for slug, reasons in (prior_summary_signals or {}).items():
            for r in reasons:
                if slug in scores and r.get("field") == "summary":
                    scores[slug].add(r["signal"], r["value"], float(r["weight"]), "summary")

        entities = title_entities | summary_entities
        for slug, compiled in self.rules.items():
            rule = compiled.rule
            if slug in (exclude_categories or []) or rule.parent in (exclude_categories or []):
                scores[slug].score = 0.0
                scores[slug].reasons = [{"signal": "source_excludes", "value": slug, "weight": 0}]
                continue
            blockers = sorted(entities & set(rule.exclude_entities))
            own_root = rule.parent or slug
            supporters = {e for e in entities if own_root in self.entity_categories.get(e, [])}
            if blockers and not supporters and scores[slug].score > 0:
                scores[slug].score = 0.0
                scores[slug].reasons = [{"signal": "excluded_by_entity", "value": blockers[0],
                                         "weight": 0}]  # fmt: skip

        return self._resolve_hierarchy(scores)

    def _resolve_hierarchy(self, scores: dict[str, CategoryScore]) -> dict[str, CategoryScore]:
        assigned: dict[str, CategoryScore] = {}
        for slug, sc in scores.items():
            rule = self.rules[slug].rule
            if sc.score < self.threshold:
                continue
            from_source = any(r["signal"] == "source_default" for r in sc.reasons)
            if (rule.parent and rule.requires_parent and not from_source
                    and scores[rule.parent].score < self.threshold):  # fmt: skip
                continue
            assigned[slug] = sc
        for slug in list(assigned):
            parent = self.rules[slug].rule.parent
            if parent and parent not in assigned:
                implied = CategoryScore(parent, assigned[slug].score)
                implied.reasons.append({"signal": "implied_by_child", "value": slug, "weight": 0})
                assigned[parent] = implied
        for sc in assigned.values():
            sc.score = round(sc.score, 2)
        return assigned


@lru_cache
def get_classifier() -> Classifier:
    entity_categories: dict[str, list[str]] = {e.id: e.categories for e in load_entities()}
    return Classifier(load_taxonomy(), entity_categories)
