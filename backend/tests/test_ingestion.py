"""End-to-end ingestion over demo fixtures: uniqueness, grouping, policies, isolation, retention."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text

from app.config.loader import SourceConfig
from app.models import Category, DedupeKey, RawItem, Source, SourceHealth, Story, StoryCategory
from app.services import retention
from tests.conftest import NOW


def stories_by_title(session) -> dict[str, Story]:  # type: ignore[no-untyped-def]
    return {s.title: s for s in session.scalars(select(Story))}


def test_demo_groups_reports_into_single_stories(make_service, run_scan, session_factory) -> None:
    stats = run_scan(make_service())
    assert stats.as_dict()["errors"] == 0
    with session_factory() as s:
        fed = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        assert fed is not None and fed.source_count == 3
        keys = {m.item.source.key for m in fed.members}
        assert keys == {"demo-wire", "demo-markets", "demo-policy"}
        for m in fed.members:
            if m.method != "seed":
                assert m.match_reason["rule"] and m.match_score is not None  # explainable

        sanctions = s.scalar(select(Story).where(Story.title.like("EU adopts new sanctions%")))
        assert sanctions is not None and sanctions.source_count == 2
        assert any(m.method == "identical_title" for m in sanctions.members)


def test_same_company_different_events_stay_separate(make_service, run_scan, session_factory):  # type: ignore[no-untyped-def]
    svc = make_service()
    run_scan(svc)
    run_scan(svc)  # batch 1 adds the Northwind acquisition
    with session_factory() as s:
        northwind = [st for st in s.scalars(select(Story)) if "Northwind" in st.title]
        assert len(northwind) == 2
        corvalis = [st for st in s.scalars(select(Story)) if "Corvalis" in st.title]
        assert len(corvalis) == 2


def test_replays_never_duplicate_and_late_reports_join(make_service, run_scan, session_factory):  # type: ignore[no-untyped-def]
    svc = make_service()
    first = run_scan(svc).as_dict()["totals"]
    with session_factory() as s:
        n_items = s.scalar(select(func.count()).select_from(RawItem))
    assert n_items == first["new"]

    second = run_scan(svc).as_dict()["totals"]
    assert second["replays"] >= first["new"]  # everything from batch 0 replayed and dropped
    with session_factory() as s:
        fed = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        assert fed.source_count == 4  # demo-industry's batch-1 report joined the story
        # tracking-param variant of an existing URL was treated as a replay
        assert s.scalar(select(func.count()).select_from(RawItem)
                        .where(RawItem.external_id == "m-001-replay")) == 0  # fmt: skip

    third = run_scan(svc).as_dict()["totals"]
    fourth = run_scan(svc).as_dict()["totals"]
    assert fourth["new"] == 0 and fourth["replays"] == fourth["fetched"]
    assert third["new"] > 0
    with session_factory() as s:
        titles = [st.title for st in s.scalars(select(Story))]
        assert len(titles) == len(set(titles))
        # every raw item belongs to exactly one story
        assert s.scalar(select(func.count()).select_from(RawItem)) == sum(
            st.member_count for st in s.scalars(select(Story))
        )


def test_demo_feeds_every_category(make_service, run_scan, session_factory) -> None:
    svc = make_service()
    for _ in range(3):
        run_scan(svc)
    with session_factory() as s:
        used = set(s.scalars(select(Category.slug).join(
            StoryCategory, StoryCategory.category_id == Category.id)))  # fmt: skip
        assert set(s.scalars(select(Category.slug))) <= used


def test_policies_control_what_is_stored(make_service, run_scan, session_factory) -> None:
    run_scan(make_service())
    with session_factory() as s:
        items = {i.external_id: i for i in s.scalars(select(RawItem))}
        assert items["p-001"].summary is None and items["p-001"].content is None  # metadata_only
        assert items["w-001"].summary and items["w-001"].content is None  # temporary_excerpt
        assert items["w-001"].expires_at == NOW + timedelta(days=30)
        assert items["i-002"].content  # indexable_content keeps text
        for item in items.values():  # attribution + original link always kept
            assert item.url.startswith("https://") and item.source.publisher


def test_failing_source_does_not_stop_others(make_service, run_scan, session_factory, demo_configs):  # type: ignore[no-untyped-def]
    flaky = SourceConfig(key="demo-flaky", name="Flaky", connector="demo", demo=True, enabled=True,
                         options={"feed": "demo-flaky", "simulate_error": "upstream 503"})  # fmt: skip
    configs = [demo_configs[0], flaky, *demo_configs[1:]]
    stats = run_scan(make_service(configs=configs)).as_dict()
    assert stats["errors"] == 1
    assert stats["sources"]["demo-markets"]["new"] > 0
    with session_factory() as s:
        src = s.scalar(select(Source).where(Source.key == "demo-flaky"))
        assert src.status == "error" and "upstream 503" in src.last_error
        health = s.scalar(select(SourceHealth).where(SourceHealth.source_id == src.id))
        assert health is not None and not health.ok
        ok = s.scalar(select(Source).where(Source.key == "demo-wire"))
        assert ok.status == "ok" and ok.cursor


def test_crash_mid_source_rolls_back_that_source_only(
    make_service, run_scan, session_factory, demo_configs
):  # type: ignore[no-untyped-def]
    from app.connectors.demo import DemoConnector

    class Exploding(DemoConnector):
        def fetch_since(self, cursor):  # type: ignore[no-untyped-def]
            result = super().fetch_since(cursor)
            result.items.append(None)  # type: ignore[arg-type]  # malformed item -> crash
            return result

    def factory(cfg):  # type: ignore[no-untyped-def]
        return (Exploding if cfg.key == "demo-markets" else DemoConnector)(cfg, now=NOW)

    stats = run_scan(make_service(connector_factory=factory)).as_dict()
    assert stats["sources"]["demo-markets"].get("error")
    with session_factory() as s:
        markets = s.scalar(select(Source).where(Source.key == "demo-markets"))
        assert s.scalar(select(func.count()).select_from(RawItem)
                        .where(RawItem.source_id == markets.id)) == 0  # fmt: skip
        assert markets.cursor is None  # cursor not advanced -> retried next run
        assert s.scalar(select(func.count()).select_from(RawItem)) > 0


def test_retention_purges_text_then_metadata(make_service, run_scan, session_factory) -> None:
    run_scan(make_service())
    with session_factory() as s:
        cat_ids = {c.slug: c.id for c in s.scalars(select(Category))}
        later = NOW + timedelta(days=31)
        result = retention.purge(s, later, cat_ids)
        s.commit()
        assert result["text_purged"] > 0
        w = s.scalar(select(RawItem).where(RawItem.external_id == "w-001"))
        assert w.summary is None and w.text_purged_at is not None
        assert w.title and w.url  # metadata + attribution survive

        # metadata retention: delete whole rows and orphaned stories
        w.delete_after = later
        s.commit()
        story_count = s.scalar(select(func.count()).select_from(Story))
        result = retention.purge(s, later, cat_ids)
        s.commit()
        assert result["items_deleted"] == 1
        assert s.scalar(select(func.count()).select_from(DedupeKey)
                        .where(DedupeKey.raw_item_id == w.id)) == 0  # fmt: skip
        assert (
            s.scalar(select(func.count()).select_from(Story)) == story_count
        )  # story kept (others)


def test_reclassify_applies_current_rules(make_service, run_scan, session_factory) -> None:
    from app.services.reclassify import reclassify_all

    run_scan(make_service())
    with session_factory() as s:
        story = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        s.execute(StoryCategory.__table__.delete().where(StoryCategory.story_id == story.id))
        for m in story.members:
            m.item.meta = {**m.item.meta, "classification": {}}
        s.commit()
    with session_factory() as s:
        result = reclassify_all(s)
        s.commit()
        assert result["items"] > 0
        story = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        assert {"fed", "fed-rates"} <= {c.category.slug for c in story.categories}


def test_demo_data_is_purged_when_demo_mode_is_off(
    make_service, run_scan, session_factory, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from app.config.settings import get_settings

    run_scan(make_service())  # demo enabled (test default)
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Story)) > 0

    monkeypatch.setenv("NEWS_DEMO_ENABLED", "false")
    get_settings.cache_clear()
    stats = run_scan(make_service()).as_dict()
    assert stats["sources"] == {}  # demo sources are not fetched any more
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(RawItem)) == 0
        assert s.scalar(select(func.count()).select_from(Story)) == 0
        assert s.scalar(select(func.count()).select_from(DedupeKey)) == 0
        fts = s.execute(text("SELECT count(*) FROM stories_fts")).scalar()
        assert fts == 0


def test_demo_is_off_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config.settings import Settings

    monkeypatch.delenv("NEWS_DEMO_ENABLED", raising=False)
    assert Settings(_env_file=None).demo_enabled is False


def test_purge_source_removes_items_and_rebuilds_shared_stories(
    make_service, run_scan, session_factory
) -> None:  # type: ignore[no-untyped-def]
    from app.services.retention import purge_source

    run_scan(make_service())
    with session_factory() as s:
        cat_ids = {c.slug: c.id for c in s.scalars(select(Category))}
        removed = purge_source(s, "demo-policy", cat_ids)
        s.commit()
        assert removed > 0
        src = s.scalar(select(Source).where(Source.key == "demo-policy"))
        assert (
            s.scalar(select(func.count()).select_from(RawItem).where(RawItem.source_id == src.id))
            == 0
        )
        fed = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        assert fed.source_count == 2  # story kept, rebuilt without the purged report
        assert s.execute(text("SELECT count(*) FROM stories_fts")).scalar() == s.scalar(
            select(func.count()).select_from(Story)
        )


def test_source_count_counts_publishers_not_feeds(
    make_service, run_scan, session_factory, demo_configs
) -> None:  # type: ignore[no-untyped-def]
    wire2 = demo_configs[0].model_copy(
        update={
            "key": "demo-wire-2",
            "name": "Demo Wire (feed 2)",
            "publisher": "Demo Wire",
            "options": {"feed": "demo-markets"},
        }
    )
    configs = [demo_configs[0].model_copy(update={"publisher": "Demo Wire"}), wire2]
    run_scan(make_service(configs=configs))
    with session_factory() as s:
        fed = s.scalar(select(Story).where(Story.title.like("Fed cuts interest rates%")))
        assert fed.member_count == 2 and fed.source_count == 1


def test_free_report_is_primary_over_paywalled(
    make_service, run_scan, session_factory, demo_configs
) -> None:  # type: ignore[no-untyped-def]
    wire = demo_configs[0].model_copy(update={"paywalled": True, "priority": 99})
    markets = demo_configs[1].model_copy(update={"priority": 10})
    run_scan(make_service(configs=[wire, markets]))
    with session_factory() as s:
        fed = s.scalar(
            select(Story)
            .where(Story.source_count == 2)
            .where(Story.title.like("%Fed%") | Story.title.like("%Federal Reserve%"))
        )
        primary = next(m for m in fed.members if m.is_primary)
        assert primary.item.source.key == "demo-markets"  # free beats paywalled despite priority
