"""Impact analysis: a 1-10 score and a colour level for every collected report and every story.

Deterministic and explainable, configured in impact.yaml (no external AI):

* a lexicon-based sentiment analysis gives each report a tone from -1 to +1; strong tone in
  either direction (negative weighs more) raises the impact;
* event severity, market-moving official sources, magnitude of figures and key actors add
  points; uncategorised (off-topic) reports are dampened;
* a story takes its best report and adds a bonus for the number of publishers carrying it.

Every awarded point is kept as {"signal", "value", "points"} so the UI can show *why* a
report got its score, and a wrong score can be fixed by editing one rule.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.config.settings import CONFIG_DIR

# --------------------------------------------------------------------------- config


class Level(BaseModel):
    name: str
    label: str
    min: int


class SentimentConfig(BaseModel):
    summary_weight: float = 0.5
    normalization: float = 15.0
    neutral_band: float = 0.2
    negative_points: float = 22.0
    positive_points: float = 14.0
    negators: list[str] = Field(default_factory=list)
    boosters: list[str] = Field(default_factory=list)
    booster_factor: float = 1.3
    lexicon: dict[int, list[str]] = Field(default_factory=dict)


class EventTier(BaseModel):
    points: int
    terms: list[str]


class Magnitude(BaseModel):
    money_bn_10: int = 12
    money_bn_1: int = 7
    pct_10: int = 8
    pct_5: int = 5


class KeyActors(BaseModel):
    points: int = 5
    max_points: int = 15
    entities: list[str] = Field(default_factory=list)


class Followup(BaseModel):
    factor: float = 0.75
    patterns: list[str] = Field(default_factory=list)


class ImpactConfig(BaseModel):
    score_bands: list[float] = Field(default_factory=lambda: [12.0, 20, 28, 36, 44, 52, 62, 74, 88])
    levels: list[Level] = Field(default_factory=list)
    off_topic_factor: float = 0.5
    relevance_points: float = 10.0
    followup: Followup = Field(default_factory=lambda: Followup())
    coverage_points: list[int] = Field(default_factory=lambda: [0, 0, 8, 14, 20, 24])
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    events: dict[str, EventTier] = Field(default_factory=dict)
    max_event_points: int = 30
    source_points: dict[str, int] = Field(default_factory=dict)
    max_source_points: int = 45
    magnitude: Magnitude = Field(default_factory=Magnitude)
    max_magnitude_points: int = 12
    key_actors: KeyActors = Field(default_factory=KeyActors)


@lru_cache
def load_impact_config(path: Path | None = None) -> ImpactConfig:
    path = path or CONFIG_DIR / "impact.yaml"
    return ImpactConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _pattern(term: str) -> re.Pattern[str]:
    word = term.rstrip("*")
    body = r"\s+".join(re.escape(w) for w in word.split())
    body += r"[\w'-]*" if term.endswith("*") else ""
    flags = 0 if not any(c.islower() for c in word) else re.I  # acronyms are case-sensitive
    return re.compile(rf"(?<![\w-]){body}(?![\w-])", flags)


_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")


# --------------------------------------------------------------------------- results


@dataclass
class ReportImpact:
    """Analysis of one collected report (stored in raw_items.metadata["impact"])."""

    points: float
    score: int
    level: str
    sentiment: float  # tone -1 .. +1
    tone: str  # negative | neutral | positive
    reasons: list[dict[str, Any]]
    # Sentiment terms found in the RSS description. Derived data only (no text): kept so a
    # later reclassification can reuse them for sources whose description is not stored.
    summary_terms: list[list[Any]] = field(default_factory=list)

    def to_meta(self) -> dict[str, Any]:
        return {
            "points": self.points, "score": self.score, "level": self.level,
            "sentiment": self.sentiment, "tone": self.tone, "reasons": self.reasons,
            "summary_terms": self.summary_terms,
        }  # fmt: skip

    @classmethod
    def from_meta(cls, data: dict[str, Any]) -> ReportImpact:
        return cls(
            points=float(data["points"]), score=int(data["score"]), level=str(data["level"]),
            sentiment=float(data["sentiment"]), tone=str(data["tone"]),
            reasons=list(data.get("reasons") or []),
            summary_terms=list(data.get("summary_terms") or []),
        )  # fmt: skip


@dataclass
class StoryImpact:
    score: int
    level: str
    sentiment: float
    tone: str
    reasons: list[dict[str, Any]]


# --------------------------------------------------------------------------- analyzer


class ImpactAnalyzer:
    def __init__(self, cfg: ImpactConfig) -> None:
        self.cfg = cfg
        s = cfg.sentiment
        terms = [(t, float(pol), _pattern(t)) for pol, ts in s.lexicon.items() for t in ts]
        # Longer (multi-word) terms first so "profit warning" wins over "profit".
        self.lexicon = sorted(terms, key=lambda x: -len(x[0].rstrip("*")))
        self.negators = {w.lower() for w in s.negators}
        self.boosters = {w.lower() for w in s.boosters}
        self.events = sorted(
            ((t.points, [(term, _pattern(term)) for term in t.terms]) for t in cfg.events.values()),
            key=lambda x: -x[0],
        )
        self.actors = set(cfg.key_actors.entities)
        self.followup = [re.compile(p, re.I) for p in cfg.followup.patterns]
        default = [Level(name="low", label="Normal", min=1)]
        self.levels = sorted(cfg.levels, key=lambda lv: lv.min) or default

    # ---- sentiment ---------------------------------------------------------------

    def sentiment_terms(self, text: str) -> list[list[Any]]:
        """[[term, signed weight], ...] found in `text`, with negation and boosters applied."""
        if not text:
            return []
        s = self.cfg.sentiment
        taken: list[tuple[int, int]] = []
        found: list[list[Any]] = []
        for _term, polarity, pat in self.lexicon:
            for m in pat.finditer(text):
                if any(m.start() < e and b < m.end() for b, e in taken):
                    continue  # part of a longer term already counted
                taken.append((m.start(), m.end()))
                window = text[max(0, m.start() - 48) : m.start()]
                before = [w.lower() for w in _WORD.findall(window)]
                weight = polarity
                if any(w in self.negators or w.endswith(("n't", "n’t")) for w in before[-3:]):
                    weight *= -0.5
                if any(w in self.boosters for w in before[-2:]):
                    weight *= s.booster_factor
                found.append([m.group(0).lower(), round(weight, 2)])
        return found

    def tone_of(self, total: float) -> tuple[float, str]:
        s = self.cfg.sentiment
        value = round(total / math.sqrt(total * total + s.normalization), 2) if total else 0.0
        if value <= -s.neutral_band:
            return value, "negative"
        if value >= s.neutral_band:
            return value, "positive"
        return value, "neutral"

    # ---- score helpers -------------------------------------------------------------

    def score_for(self, points: float) -> int:
        return 1 + sum(1 for band in self.cfg.score_bands if points >= band)

    def level_for(self, score: int) -> str:
        name = self.levels[0].name
        for lv in self.levels:
            if score >= lv.min:
                name = lv.name
        return name

    # ---- one report ------------------------------------------------------------------

    def analyze(
        self,
        *,
        title: str,
        summary: str | None,
        source_key: str,
        entities: list[str] | set[str],
        numbers: list[str],
        categorized: bool,
        prior_summary_terms: list[list[Any]] | None = None,
    ) -> ReportImpact:
        cfg = self.cfg
        reasons: list[dict[str, Any]] = []
        total = 0.0

        def award(signal: str, value: str, points: float) -> None:
            nonlocal total
            if points > 0:
                total += points
                reasons.append({"signal": signal, "value": value, "points": round(points, 1)})

        # 0. Relevance: the report matches one of the user's categories.
        if categorized:
            award("relevance", "matches a category", cfg.relevance_points)

        # 1. Tone (sentiment analysis of headline + description).
        title_terms = self.sentiment_terms(title)
        summary_terms = (
            self.sentiment_terms(summary) if summary else list(prior_summary_terms or [])
        )
        sw = cfg.sentiment.summary_weight
        raw = sum(w for _, w in title_terms) + sw * sum(w for _, w in summary_terms)
        sentiment, tone = self.tone_of(raw)
        if tone != "neutral":
            per = cfg.sentiment.negative_points if sentiment < 0 else cfg.sentiment.positive_points
            words = ", ".join(dict.fromkeys(t for t, _ in title_terms + summary_terms))
            award("tone", f"{tone} {sentiment:+.2f}: {words}"[:90], abs(sentiment) * per)

        # 2. Event severity (headline).
        hits: list[tuple[str, int]] = []
        for tier_points, terms in self.events:
            for term, pat in terms:
                if pat.search(title) and term not in {t for t, _ in hits}:
                    hits.append((term, tier_points))
        if hits:
            extra = 4 * (len(hits) - 1)
            award("event", ", ".join(t for t, _ in hits[:4]),
                  min(hits[0][1] + extra, cfg.max_event_points))  # fmt: skip

        # 3. Market-moving official source.
        src = min(cfg.source_points.get(source_key, 0), cfg.max_source_points)
        award("source", source_key, src)

        # 4. Magnitude of figures.
        money = max((float(n.split(":")[2]) for n in numbers if n.startswith("money:")), default=0)
        pct = max((abs(float(n.split(":")[1])) for n in numbers if n.startswith("pct:")), default=0)
        m = cfg.magnitude
        mag: float = (m.money_bn_10 if money >= 10e9 else m.money_bn_1 if money >= 1e9 else 0) + (
            m.pct_10 if pct >= 10 else m.pct_5 if pct >= 5 else 0
        )
        figures = [f"${money / 1e9:g}bn" if money >= 1e9 else "", f"{pct:g}%" if pct >= 5 else ""]
        award("magnitude", " ".join(f for f in figures if f), min(mag, cfg.max_magnitude_points))

        # 5. Key actors.
        actors = sorted(set(entities) & self.actors)
        award("actors", ", ".join(actors[:4]),
              min(len(actors) * cfg.key_actors.points, cfg.key_actors.max_points))  # fmt: skip

        # Follow-up formats (analysis, explainer, opinion, live blog) rank below the event.
        followup = next((p.pattern for p in self.followup if p.search(title.strip())), None)
        if followup and total > 0:
            cut = total * (1 - cfg.followup.factor)
            reasons.append({"signal": "followup", "value": "analysis / explainer",
                            "points": -round(cut, 1)})  # fmt: skip
            total -= cut

        # Off-topic dampening.
        if not categorized and total > 0:
            cut = total * (1 - cfg.off_topic_factor)
            reasons.append(
                {"signal": "off_topic", "value": "no category", "points": -round(cut, 1)}
            )
            total -= cut

        points = round(total, 1)
        score = self.score_for(points)
        return ReportImpact(
            points=points, score=score, level=self.level_for(score), sentiment=sentiment,
            tone=tone, reasons=reasons, summary_terms=summary_terms,
        )  # fmt: skip

    # ---- one story ----------------------------------------------------------------------

    def combine(self, reports: list[ReportImpact], publishers: int) -> StoryImpact:
        """Story impact = its strongest report + a bonus for independent coverage."""
        if not reports:
            return StoryImpact(1, self.level_for(1), 0.0, "neutral", [])
        best = max(reports, key=lambda r: r.points)
        cov = self.cfg.coverage_points[min(publishers, len(self.cfg.coverage_points) - 1)]
        reasons = list(best.reasons)
        if cov:
            reasons.insert(0, {"signal": "coverage", "value": f"{publishers} publishers",
                               "points": float(cov)})  # fmt: skip
        score = self.score_for(best.points + cov)
        avg = sum(r.sentiment for r in reports) / len(reports)
        sentiment = round(avg, 2)
        band = self.cfg.sentiment.neutral_band
        tone = "negative" if sentiment <= -band else "positive" if sentiment >= band else "neutral"
        return StoryImpact(score, self.level_for(score), sentiment, tone, reasons)


@lru_cache
def get_analyzer() -> ImpactAnalyzer:
    return ImpactAnalyzer(load_impact_config())


def report_impact(item: Any) -> ReportImpact:
    """Stored analysis of a RawItem; computed from the stored headline/text if missing."""
    data = (item.meta or {}).get("impact")
    if data:
        return ReportImpact.from_meta(data)
    summary = item.summary if item.source.policy != "metadata_only" else None
    return get_analyzer().analyze(
        title=item.title, summary=summary, source_key=item.source.key,
        entities=list(item.entities or []), numbers=list(item.numbers or []),
        categorized=bool((item.meta or {}).get("classification")),
    )  # fmt: skip
