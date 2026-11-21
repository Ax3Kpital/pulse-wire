"""Impact analysis: per-report sentiment + severity -> 1..10 score -> colour level."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import RawItem, Story
from app.services.impact import ImpactAnalyzer, ReportImpact, get_analyzer
from tests.test_scheduler_and_api import api, scan_and_wait  # noqa: F401  (fixture re-export)

BACKEND = Path(__file__).resolve().parents[1]


def analyze(title: str, *, key: str = "x", ents=(), nums=(), categorized=True, summary=None,  # type: ignore[no-untyped-def]
            prior=None) -> ReportImpact:  # fmt: skip
    return get_analyzer().analyze(
        title=title, summary=summary, source_key=key, entities=list(ents), numbers=list(nums),
        categorized=categorized, prior_summary_terms=prior,
    )  # fmt: skip


# --------------------------------------------------------------------------- sentiment


def test_tone_negative_positive_neutral() -> None:
    assert analyze("Stocks plunge as recession fears grow").tone == "negative"
    assert analyze("Shares surge to record high after strong earnings").tone == "positive"
    assert analyze("Company opens new office in Austin").tone == "neutral"
    assert analyze("Stocks plunge as recession fears grow").sentiment < -0.6


def test_negation_flips_and_boosters_amplify() -> None:
    a = get_analyzer()
    assert dict(map(tuple, a.sentiment_terms("Economy avoids recession")))["recession"] > 0
    assert dict(map(tuple, a.sentiment_terms("Inflation doesn't fall")))["fall"] > 0
    plain = dict(map(tuple, a.sentiment_terms("Oil prices drop")))["drop"]
    boosted = dict(map(tuple, a.sentiment_terms("Oil prices sharply drop")))["drop"]
    assert boosted < plain < 0


def test_multiword_terms_win_over_single_words() -> None:
    terms = get_analyzer().sentiment_terms("Retailer issues profit warning")
    assert terms == [["profit warning", -3.0]]  # not "profit" (+1)
    assert get_analyzer().sentiment_terms("Consumer prices rise again")[0] == ["prices rise", -1.0]


def test_description_counts_half_and_survives_reclassification() -> None:
    title = "Central bank publishes quarterly report"
    with_summary = analyze(title, summary="Warns of a deep recession and market turmoil.")
    assert with_summary.tone == "negative" and with_summary.summary_terms
    # Description not stored (metadata_only): its derived terms are reused, not the text.
    again = analyze(title, prior=with_summary.summary_terms)
    assert again.sentiment == with_summary.sentiment and again.score == with_summary.score
    assert analyze(title).tone == "neutral"


# --------------------------------------------------------------------------- score + level


def test_routine_report_is_green() -> None:
    imp = analyze("Company opens new office in Austin")
    assert imp.score <= 3 and imp.level == "low"


def test_off_topic_is_lower_than_on_topic() -> None:
    title = "Star striker resigns as club captain after crash"
    assert analyze(title, categorized=False).score < analyze(title).score
    assert analyze(title, categorized=False).reasons[-1]["signal"] == "off_topic"


def test_explainers_rank_below_the_event() -> None:
    event = analyze("Iran launches missile attack on US base", ents=["iran", "united-states"])
    explainer = analyze("Why Iran launched a missile attack on a US base",
                        ents=["iran", "united-states"])  # fmt: skip
    assert explainer.score < event.score
    assert any(r["signal"] == "followup" for r in explainer.reasons)


def test_market_moving_releases_are_critical() -> None:
    fomc = analyze("Federal Reserve issues FOMC statement", key="fed-monetary",
                   ents=["federal-reserve"])  # fmt: skip
    assert fomc.level == "critical" and fomc.score >= 8
    for key, title in (
        ("bls-cpi", "Consumer Price Index – October 2026"),
        ("bls-jobs", "The Employment Situation – October 2026"),
    ):
        assert analyze(title, key=key).level == "critical", key
    notice = analyze("Federal Reserve announces revised tentative schedule of FOMC meetings",
                     key="fed-monetary", ents=["federal-reserve"])  # fmt: skip
    assert notice.score < fomc.score and notice.level != "critical"


def test_magnitude_and_reasons_are_explained() -> None:
    imp = analyze("Pfizer to acquire biotech for $12 billion", ents=["pfizer"],
                  nums=["money:$:1.2e+10"])  # fmt: skip
    by = {r["signal"]: r for r in imp.reasons}
    assert by["magnitude"]["points"] == 12 and "acquire*" in by["event"]["value"]
    assert {"relevance", "event", "magnitude", "actors"} <= set(by)
    assert 4 <= imp.score <= 7


def test_score_bands_and_colour_levels() -> None:
    a = get_analyzer()
    assert a.score_for(0) == 1 and a.score_for(10_000) == 10
    scores = [a.score_for(p) for p in range(0, 120)]
    assert scores == sorted(scores) and set(scores) == set(range(1, 11))
    assert [a.level_for(s) for s in range(1, 11)] == (
        ["low"] * 3 + ["moderate"] * 2 + ["important"] * 2 + ["critical"] * 3
    )


def test_story_combines_best_report_and_coverage() -> None:
    a: ImpactAnalyzer = get_analyzer()
    reports = [
        analyze("Fed cuts interest rates by 25 basis points", ents=["federal-reserve"]),
        analyze("Federal Reserve lowers rates a quarter point", ents=["federal-reserve"]),
        analyze("FOMC delivers rate cut", ents=["federal-reserve"]),
    ]
    alone = a.combine(reports[:1], publishers=1)
    covered = a.combine(reports, publishers=3)
    assert covered.score > alone.score and covered.reasons[0]["signal"] == "coverage"
    assert covered.level in {"important", "critical"}
    assert a.combine([], publishers=0).score == 1


# --------------------------------------------------------------------------- pipeline + API


def test_ingested_reports_and_stories_get_scores(make_service, run_scan, session_factory) -> None:  # type: ignore[no-untyped-def]
    run_scan(make_service())
    with session_factory() as s:
        items = list(s.scalars(select(RawItem)))
        assert items and all(1 <= i.meta["impact"]["score"] <= 10 for i in items)
        fed = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        quiet = s.scalar(select(Story).where(Story.title.like("US LNG exports hit record%")))
        assert fed.impact_score >= 6 and fed.impact_level in {"important", "critical"}
        assert fed.impact_reasons and quiet.impact_score < fed.impact_score


def test_api_exposes_filters_and_sorts_by_impact(api: TestClient) -> None:  # noqa: F811
    scan_and_wait(api)
    base = {"period": "all", "limit": 100}
    every = api.get("/api/stories", params=base).json()
    story = every["items"][0]
    assert {"impact_score", "impact_level", "sentiment", "tone", "impact_reasons"} <= set(story)
    for o in (r for s in every["items"] for r in s["others"]):
        assert 1 <= o["impact_score"] <= 10 and o["impact_level"]

    hot = api.get("/api/stories", params={**base, "min_impact": 6}).json()
    assert 0 < hot["total"] < every["total"]
    assert all(s["impact_score"] >= 6 for s in hot["items"])

    ranked = api.get("/api/stories", params={**base, "sort": "impact"}).json()["items"]
    scores = [s["impact_score"] for s in ranked]
    assert scores == sorted(scores, reverse=True)

    counts = {
        c["slug"]: c["count"]
        for c in api.get("/api/categories", params={"period": "all", "min_impact": 6}).json()
    }
    for slug, n in counts.items():
        got = api.get("/api/stories", params={"period": "all", "category": slug, "min_impact": 6})
        assert n == got.json()["total"]
    assert api.get("/api/stories", params={"min_impact": 11}).status_code == 422


def test_migration_keeps_story_members(tmp_path: Path) -> None:
    """Rebuilding the stories table (batch migration) must not cascade-delete its children."""
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "0002")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.exec_driver_sql("INSERT INTO sources (id, key, name, publisher, connector, enabled, "
                          "is_demo, config, policy, status, created_at, updated_at) VALUES "
                          "(1, 's', 'S', 'S', 'rss', 1, 0, '{}', 'metadata_only', 'ok', "
                          "'2026-11-14', '2026-11-14')")  # fmt: skip
        c.exec_driver_sql("INSERT INTO raw_items (id, source_id, external_id, url, canonical_url, "
                          "title, normalized_title, published_at, fetched_at, last_seen_at, "
                          "metadata, title_hash, entities, numbers) VALUES (1, 1, 'e', 'u', 'u', "
                          "'t', 't', '2026-11-14', '2026-11-14', '2026-11-14', '{}', 'h', '[]', "
                          "'[]')")  # fmt: skip
        c.exec_driver_sql("INSERT INTO stories (id, title, published_at, last_member_at, "
                          "member_count, source_count, created_at, updated_at) VALUES "
                          "(1, 't', '2026-11-14', '2026-11-14', 1, 1, '2026-11-14', '2026-11-14')")  # fmt: skip
        c.exec_driver_sql("INSERT INTO story_members (story_id, raw_item_id, is_primary, method, "
                          "match_reason, joined_at) VALUES (1, 1, 1, 'seed', '{}', '2026-11-14')")  # fmt: skip
    engine.dispose()
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    with engine.connect() as c:
        assert c.exec_driver_sql("SELECT count(*) FROM story_members").scalar() == 1
        cols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(stories)")}
        assert {"impact_score", "impact_level", "sentiment"} <= cols and "importance" not in cols
    engine.dispose()
