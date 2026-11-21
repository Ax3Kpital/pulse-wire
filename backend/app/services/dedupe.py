"""Story grouping (dedupe stage 2): candidate selection + deterministic, explainable decision.

MinHash/LSH (datasketch) is used *only* to shortlist candidates when the time window holds many
stories. Whether two reports describe the same event is always decided by `compare()`, whose
output (score + per-signal breakdown + rule that fired) is persisted on `story_members`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from datasketch import MinHash, MinHashLSH

from app.services.entities import EntityMatcher, get_matcher
from app.services.text import content_tokens, extract_dates, fold, stem

# Phrase-level synonyms applied before tokenising, so different wordings of one event align.
_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p, re.I), r)
    for p, r in [
        (r"\bfederal reserve\b|\bfomc\b|\bthe fed\b", "fed"),
        (r"\bquarter[\s-]point\b|\bquarter[\s-]percentage[\s-]point\b", "25 bp"),
        (r"\bhalf[\s-]point\b|\bhalf[\s-]percentage[\s-]point\b", "50 bp"),
        (r"\bbasis[\s-]points?\b|\bbps\b", "bp"),
        (r"\binterest rates?\b|\bborrowing costs?\b|\bpolicy rates?\b", "rate"),
        (r"\bconsumer price index\b|\bconsumer prices\b", "cpi"),
        (r"\bnon-?farm payrolls?\b|\bjobs report\b", "payroll"),
        (r"\bthird[\s-]quarter\b", "q3"), (r"\bsecond[\s-]quarter\b", "q2"),
        (r"\bfirst[\s-]quarter\b", "q1"), (r"\bfourth[\s-]quarter\b", "q4"),
        (r"\bper ?cent\b", "%"),
    ]
]  # fmt: skip

_WORD_SYNONYMS = {
    "lower": "cut", "reduce": "cut", "slash": "cut", "trim": "cut", "ease": "cut",
    "raise": "hike", "increase": "hike", "lift": "hike",
    "profit": "earning", "result": "earning", "income": "earning",
    "acquisition": "acquire", "buy": "acquire", "purchase": "acquire", "takeover": "acquire",
    "approval": "approve", "clear": "approve", "authorize": "approve",
    "top": "beat", "exceed": "beat", "surpass": "beat",
    "barrel": "bbl", "output": "production", "job": "payroll",
}  # fmt: skip
_WORD_SYNONYMS = {stem(k): stem(v) for k, v in _WORD_SYNONYMS.items()}

W_TITLE, W_TEXT, W_ENTITY, W_NUMBER, W_TIME = 0.40, 0.15, 0.20, 0.20, 0.05
MERGE_SCORE = 0.55
MIN_TITLE_SIM = 0.30
NEAR_IDENTICAL_TITLE = 0.80
CORROBORATING_TITLE = 0.50  # title similarity that corroborates on its own
MIN_IDF_CORPUS = 30
CORROBORATING_KINDS = frozenset(
    {"company", "person"}
)  # headlines needed before IDF weighting is used
STRONG_ENTITY_WEIGHT = 1.0


def similarity_tokens(text: str) -> set[str]:
    folded = fold(text)
    for pat, repl in _PHRASES:
        folded = pat.sub(repl, folded)
    return {_WORD_SYNONYMS.get(t, t) for t in content_tokens(folded)}


@dataclass
class ItemFeatures:
    key: int  # raw_item id (or temporary negative id for unsaved items)
    story_id: int | None
    source_id: int
    published_at: datetime
    title_tokens: set[str]
    text_tokens: set[str]  # title + permitted summary
    has_summary: bool
    entities: set[str]
    numbers: set[str]
    dates: set[str] = field(default_factory=set)  # calendar dates named in the title

    @property
    def shingles(self) -> set[str]:
        return self.text_tokens | {f"ent:{e}" for e in self.entities}


def build_features(
    *,
    key: int,
    story_id: int | None,
    source_id: int,
    published_at: datetime,
    title: str,
    summary: str | None,
    entities: set[str] | list[str],
    numbers: set[str] | list[str],
) -> ItemFeatures:
    # Each entity mention collapses to ONE token ("Beijing"/"China" -> ent:china), so a shared
    # company name counts once instead of inflating similarity word by word.
    remainder, title_ents = get_matcher().replace(title)
    title_tokens = similarity_tokens(remainder) | {f"ent:{e}" for e in title_ents}
    summary_tokens = similarity_tokens(summary) if summary else set()
    return ItemFeatures(key, story_id, source_id, published_at, title_tokens,
                        title_tokens | summary_tokens, bool(summary), set(entities),
                        set(numbers), extract_dates(title))  # fmt: skip


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _blend(a: set[str], b: set[str], weight: Callable[[str], float] | None = None) -> float:
    """Average of Jaccard and overlap coefficient: robust to one headline being longer.

    With `weight` (IDF over the recent corpus), common words like "approval" or "minutes"
    count little and rare, specific tokens (a drug or company name) count a lot."""
    if weight is None:
        return 0.5 * _jaccard(a, b) + 0.5 * _overlap(a, b)
    if not a or not b:
        return 0.0
    shared = sum(weight(t) for t in a & b)
    union = sum(weight(t) for t in a | b)
    smaller = min(sum(weight(t) for t in a), sum(weight(t) for t in b))
    return 0.5 * shared / union + 0.5 * shared / smaller


# Mutually exclusive subjects: if each headline names one and they differ, the reports are
# about different events (e.g. "25% tariffs on steel" vs "25% tariffs on autos").
_MONTHS = "january february march april may june july august september october november december"
_PRODUCTS = (
    "steel aluminum aluminium auto autos car cars copper lumber soybean soybeans wheat corn "
    "semiconductor chip chips solar lithium pharmaceuticals drug drugs gold silver coal"
)
CONFLICT_GROUPS: dict[str, frozenset[str]] = {
    "month": frozenset(stem(m) for m in _MONTHS.split()),
    "quarter": frozenset({"q1", "q2", "q3", "q4"}),
    "product": frozenset(stem(p) for p in _PRODUCTS.split()),
}


def _subject_conflict(a: set[str], b: set[str]) -> list[str]:
    conflicts = []
    for name, group in CONFLICT_GROUPS.items():
        ga, gb = a & group, b & group
        if ga and gb and not ga & gb:
            conflicts.append(name)
    return conflicts


def _number_conflict(a: set[str], b: set[str]) -> list[str]:
    """Same kind of figure reported with disjoint values (e.g. pct:3.1 vs pct:2.7)."""
    conflicts: list[str] = []
    for kind in ("pct", "bp"):
        va = {n for n in a if n.startswith(kind + ":")}
        vb = {n for n in b if n.startswith(kind + ":")}
        if va and vb and not va & vb:
            conflicts.append(kind)
    return conflicts


@dataclass
class MatchDecision:
    merge: bool
    score: float
    rule: str
    signals: dict[str, Any] = field(default_factory=dict)

    def as_reason(self) -> dict[str, Any]:
        return {"rule": self.rule, "score": round(self.score, 3), **self.signals}


def compare(
    a: ItemFeatures,
    b: ItemFeatures,
    matcher: EntityMatcher,
    window_hours: int = 72,
    idf: Callable[[str], float] | None = None,
) -> MatchDecision:
    hours = abs((a.published_at - b.published_at).total_seconds()) / 3600
    title_sim = _blend(a.title_tokens, b.title_tokens, idf)
    text_sim = (
        _blend(a.text_tokens, b.text_tokens, idf) if a.has_summary and b.has_summary else title_sim
    )
    date_conflict = bool(a.dates and b.dates and not a.dates & b.dates)
    shared_entities = a.entities & b.entities
    union_entities = a.entities | b.entities
    ent_score = (
        sum(matcher.weight(e) for e in shared_entities)
        / sum(matcher.weight(e) for e in union_entities)
        if union_entities
        else 0.5  # no entities on either side: neutral
    )
    # Only a shared company/person corroborates: institutions and countries issue many
    # unrelated announcements, so sharing "FDA" or "China" proves nothing on its own.
    strong_shared = sorted(
        e for e in shared_entities
        if matcher.weight(e) >= STRONG_ENTITY_WEIGHT and matcher.kind(e) in CORROBORATING_KINDS
    )  # fmt: skip
    shared_numbers = sorted(a.numbers & b.numbers)
    conflicts = _number_conflict(a.numbers, b.numbers)
    subject_conflicts = _subject_conflict(a.title_tokens, b.title_tokens)
    num_score = 1.0 if shared_numbers else (0.0 if conflicts else 0.5)
    time_score = max(0.0, 1 - hours / window_hours)

    score = (W_TITLE * title_sim + W_TEXT * text_sim + W_ENTITY * ent_score
             + W_NUMBER * num_score + W_TIME * time_score)  # fmt: skip
    signals = {
        "title_sim": round(title_sim, 3),
        "text_sim": round(text_sim, 3),
        "entity_score": round(ent_score, 3),
        "shared_entities": sorted(shared_entities),
        "shared_numbers": shared_numbers,
        "number_conflicts": conflicts,
        "subject_conflicts": subject_conflicts,
        "hours_apart": round(hours, 1),
        "weighting": "idf" if idf else "uniform",
    }

    if a.source_id == b.source_id:
        # One publisher's separate items are separate stories; its replays are stage 1's job.
        return MatchDecision(False, score, "same_source", signals)
    if hours > window_hours:
        return MatchDecision(False, score, "outside_window", signals)
    if conflicts:
        return MatchDecision(False, score, "number_conflict", signals)
    if subject_conflicts:
        return MatchDecision(False, score, "subject_conflict", signals)
    if date_conflict:
        signals["dates"] = [sorted(a.dates), sorted(b.dates)]
        return MatchDecision(False, score, "date_conflict", signals)
    if title_sim >= NEAR_IDENTICAL_TITLE:
        return MatchDecision(True, score, "near_identical_title", signals)
    if title_sim < MIN_TITLE_SIM:
        return MatchDecision(False, score, "title_too_different", signals)
    corroborated = bool(strong_shared) or bool(shared_numbers) or title_sim >= CORROBORATING_TITLE
    if score >= MERGE_SCORE and corroborated:
        return MatchDecision(True, score, "weighted_match", signals)
    return MatchDecision(False, score, "below_threshold" if corroborated else "uncorroborated",
                         signals)  # fmt: skip


class ClusterIndex:
    """In-memory view of recent story members for one ingestion run."""

    def __init__(
        self,
        matcher: EntityMatcher,
        *,
        window_hours: int = 72,
        lsh_threshold: int = 40,
        num_perm: int = 64,
    ) -> None:
        self.matcher = matcher
        self.window = timedelta(hours=window_hours)
        self.window_hours = window_hours
        self.lsh_threshold = lsh_threshold
        self.num_perm = num_perm
        self.items: dict[int, ItemFeatures] = {}
        self._df: Counter[str] = Counter()
        self._lsh = MinHashLSH(threshold=0.2, num_perm=num_perm)

    def _minhash(self, feats: ItemFeatures) -> MinHash:
        mh = MinHash(num_perm=self.num_perm, seed=7)
        for token in sorted(feats.shingles):
            mh.update(token.encode("utf-8"))
        return mh

    def add(self, feats: ItemFeatures) -> None:
        self.remove(feats.key)
        self.items[feats.key] = feats
        self._df.update(feats.title_tokens)
        if feats.shingles:
            self._lsh.insert(str(feats.key), self._minhash(feats))

    def remove(self, key: int) -> None:
        old = self.items.pop(key, None)
        if old is not None:
            self._df.subtract(old.title_tokens)
        if str(key) in self._lsh:
            self._lsh.remove(str(key))

    def idf(self) -> Callable[[str], float] | None:
        """Smoothed IDF over recent headlines; None while the corpus is too small to trust."""
        n = len(self.items)
        if n < MIN_IDF_CORPUS:
            return None
        return lambda t: math.log((n + 1) / (self._df.get(t, 0) + 1)) + 1.0

    def candidates(self, feats: ItemFeatures) -> tuple[list[ItemFeatures], str]:
        in_window = [
            f for f in self.items.values()
            if f.story_id is not None and abs(f.published_at - feats.published_at) <= self.window
        ]  # fmt: skip
        if len(in_window) <= self.lsh_threshold or not feats.shingles:
            return in_window, "exhaustive"
        keys = {int(k) for k in self._lsh.query(self._minhash(feats))}
        return [f for f in in_window if f.key in keys], "minhash_lsh"

    def best_match(self, feats: ItemFeatures) -> tuple[int, MatchDecision, str] | None:
        """Best story to join, or None. Ties break on the lowest story id (deterministic)."""
        candidates, method = self.candidates(feats)
        idf = self.idf()
        best: tuple[float, int, MatchDecision] | None = None
        for other in candidates:
            decision = compare(feats, other, self.matcher, self.window_hours, idf)
            if not decision.merge or other.story_id is None:
                continue
            rank = (decision.score, -other.story_id)
            if best is None or rank > (best[0], -best[1]):
                best = (decision.score, other.story_id, decision)
        if best is None:
            return None
        return best[1], best[2], method
