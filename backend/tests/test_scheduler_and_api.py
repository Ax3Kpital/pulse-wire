"""Single-flight scans (scheduler + Fetch now), and the HTTP API."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import IngestionRun
from app.scheduler.coordinator import ScanCoordinator
from app.scheduler.jobs import JOB_ID, build_scheduler, scan_all_sources_job
from app.services.ingestion import RunStats

# --------------------------------------------------------------------------- coordinator


def blocking_scan():  # type: ignore[no-untyped-def]
    started, release = threading.Event(), threading.Event()
    calls: list[int] = []

    def scan(run_id: int) -> RunStats:
        calls.append(run_id)
        started.set()
        release.wait(5)
        return RunStats()

    return scan, started, release, calls


def test_overlapping_requests_coalesce(session_factory) -> None:  # type: ignore[no-untyped-def]
    scan, started, release, calls = blocking_scan()
    coord = ScanCoordinator(session_factory, scan)

    t1 = coord.request_scan("scheduled")
    assert started.wait(2)
    t2 = coord.request_scan("manual")
    t3 = coord.request_scan("manual")
    assert not t1.coalesced and t2.coalesced and t3.coalesced
    assert t1.run_id == t2.run_id == t3.run_id
    release.set()
    coord.wait(5)

    assert calls == [t1.run_id]
    with session_factory() as s:
        runs = list(s.scalars(select(IngestionRun)))
        assert len(runs) == 1
        assert runs[0].status == "success" and runs[0].coalesced_requests == 2
        assert runs[0].kind == "scheduled"
    assert not coord.is_running()

    t4 = coord.request_scan("manual")  # after completion a new scan may start
    coord.wait(5)
    assert not t4.coalesced and t4.run_id != t1.run_id


def test_concurrent_burst_starts_exactly_one_scan(session_factory) -> None:  # type: ignore[no-untyped-def]
    scan, started, release, calls = blocking_scan()
    coord = ScanCoordinator(session_factory, scan)
    tickets = []
    threads = [threading.Thread(target=lambda: tickets.append(coord.request_scan("manual")))
               for _ in range(12)]  # fmt: skip
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    release.set()
    coord.wait(5)
    assert len(calls) == 1
    assert sum(not t.coalesced for t in tickets) == 1
    with session_factory() as s:
        assert s.scalar(select(func.count()).select_from(IngestionRun)) == 1


def test_crashing_scan_is_recorded_and_releases_lock(session_factory) -> None:  # type: ignore[no-untyped-def]
    def scan(run_id: int) -> RunStats:
        raise RuntimeError("boom")

    coord = ScanCoordinator(session_factory, scan)
    ticket = coord.request_scan("manual")
    coord.wait(5)
    with session_factory() as s:
        run = s.get(IngestionRun, ticket.run_id)
        assert run.status == "failed" and "boom" in run.error
    assert not coord.is_running()


def test_scheduler_job_uses_same_coordinator_every_15_minutes(session_factory) -> None:  # type: ignore[no-untyped-def]
    scan, started, release, calls = blocking_scan()
    coord = ScanCoordinator(session_factory, scan)
    scheduler = build_scheduler(coord, interval_minutes=15, run_on_startup=False)
    job = scheduler.get_job(JOB_ID)
    assert job.trigger.interval.total_seconds() == 15 * 60
    assert job.max_instances == 1 and job.coalesce

    scan_all_sources_job(coord)  # what APScheduler calls
    assert started.wait(2)
    manual = coord.request_scan("manual")
    assert manual.coalesced
    release.set()
    coord.wait(5)
    assert len(calls) == 1


# --------------------------------------------------------------------------- API


@pytest.fixture
def api(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    from app.config.loader import load_sources
    from app.main import create_app
    from app.services import ingestion

    demo_only = [c for c in load_sources() if c.demo]
    original_init = ingestion.IngestionService.__init__

    def init(self, *a, **kw):  # type: ignore[no-untyped-def]
        kw.setdefault("configs_loader", lambda: demo_only)  # no network in tests
        original_init(self, *a, **kw)

    monkeypatch.setattr(ingestion.IngestionService, "__init__", init)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'api.db'}", start_scheduler=False)
    with TestClient(app) as client:
        yield client


def scan_and_wait(client: TestClient) -> dict:
    ticket = client.post("/api/scan").json()
    for _ in range(100):
        status = client.get("/api/scan/status").json()
        if not status["running"]:
            return ticket
        time.sleep(0.05)
    raise AssertionError("scan did not finish")


def test_health_and_meta(api: TestClient) -> None:
    body = api.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert api.get("/api/meta").json()["interval_minutes"] == 15


def test_fetch_now_and_story_listing(api: TestClient) -> None:
    ticket = scan_and_wait(api)
    assert ticket["coalesced"] is False and "started" in ticket["message"]

    page = api.get("/api/stories", params={"period": "all", "limit": 100}).json()
    assert page["total"] == len(page["items"]) > 10
    titles = [s["title"] for s in page["items"]]
    assert len(titles) == len(set(titles))
    fed = next(s for s in page["items"] if s["title"].startswith("Fed cuts interest rates"))
    assert fed["source_count"] == 3 and len(fed["others"]) == 2
    assert fed["url"].startswith("https://") and fed["primary_source"]["name"]
    assert all(o["url"] and o["source"]["publisher"] for o in fed["others"])
    assert {"fed", "fed-rates"} <= {c["slug"] for c in fed["categories"]}

    detail = api.get(f"/api/stories/{fed['id']}").json()
    assert len(detail["reports"]) == 3
    assert detail["classification"] and detail["classification"][0]["reasons"]

    status = api.get("/api/scan/status").json()
    assert status["last_run"]["status"] == "success" and status["last_refresh_at"]


def test_filters_and_search(api: TestClient) -> None:
    scan_and_wait(api)
    geo = api.get("/api/stories", params={"category": "geo-russia", "period": "all"}).json()
    assert geo["total"] >= 2
    assert all(any(c["slug"] == "geo-russia" for c in s["categories"]) for s in geo["items"])

    src = api.get("/api/stories", params={"source": "demo-industry", "period": "all"}).json()
    assert src["total"] >= 1

    hits = api.get("/api/stories", params={"q": "rare earth", "period": "all"}).json()
    assert hits["total"] >= 1 and "rare earth" in hits["items"][0]["title"].lower()
    by_source_name = api.get("/api/stories", params={"q": "Demo Industry", "period": "all"}).json()
    assert by_source_name["total"] >= 1
    assert api.get("/api/stories", params={"q": '"); DROP TABLE x; --'}).status_code == 200
    assert api.get("/api/stories", params={"q": "zzzznotfound"}).json()["total"] == 0

    recent = api.get("/api/stories", params={"period": "24h"}).json()["total"]
    assert 0 < recent <= api.get("/api/stories", params={"period": "all"}).json()["total"]


def test_categories_tree_and_sources(api: TestClient) -> None:
    # As synced from sources.yaml at startup (the test scan below only loads demo sources).
    sources = {s["key"]: s for s in api.get("/api/sources").json()}
    assert sources["reuters-connect"]["enabled"] is False
    assert "REUTERS_CONNECT_API_URL" in sources["reuters-connect"]["disabled_reason"]
    assert sources["fed-monetary"]["enabled"] is True
    assert "NEWS_CONTACT" in sources["sec-press"]["disabled_reason"]

    scan_and_wait(api)
    tree = api.get("/api/categories", params={"period": "all"}).json()
    slugs = [c["slug"] for c in tree]
    assert slugs == [
        "geopolitics",
        "fed",
        "energy",
        "banking",
        "biotech",
        "pharma",
        "oil-gas",
        "tech",
    ]
    geo = tree[0]
    assert [c["name"] for c in geo["children"]] == ["US", "China", "Europe", "Russia"]
    assert [c["name"] for c in tree[1]["children"]] == ["Rates", "CPI", "PCE", "Jobs", "Speeches"]
    assert all(c["count"] > 0 for c in tree)

    sources = {s["key"]: s for s in api.get("/api/sources").json()}
    assert sources["demo-wire"]["status"] == "ok" and sources["demo-wire"]["recent"]


def test_settings_refuse_non_loopback_host(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import ValidationError

    from app.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(host="0.0.0.0")
    assert Settings(host="localhost").host == "127.0.0.1"


def test_category_counts_respect_source_filter(api: TestClient) -> None:
    scan_and_wait(api)
    all_counts = {
        c["slug"]: c["count"] for c in api.get("/api/categories", params={"period": "all"}).json()
    }
    only = {
        c["slug"]: c["count"]
        for c in api.get(
            "/api/categories", params={"period": "all", "source": "demo-industry"}
        ).json()
    }
    assert sum(only.values()) < sum(all_counts.values())
    for slug, count in only.items():  # the sidebar count must equal what the list returns
        listed = api.get(
            "/api/stories", params={"period": "all", "category": slug, "source": "demo-industry"}
        ).json()["total"]
        assert listed == count, slug


def test_free_only_hides_paywall_only_stories(api: TestClient) -> None:
    from sqlalchemy import update

    from app.db import session_scope
    from app.models import Source

    scan_and_wait(api)
    with session_scope() as s:  # pretend demo-policy is a paywalled publisher
        s.execute(
            update(Source)
            .where(Source.key == "demo-policy")
            .values(config={"paywalled": True, "priority": 50})
        )
    every = api.get("/api/stories", params={"period": "all", "limit": 100}).json()
    free = api.get("/api/stories", params={"period": "all", "limit": 100, "free_only": True}).json()
    assert free["total"] < every["total"]
    hidden = {s["id"] for s in every["items"]} - {s["id"] for s in free["items"]}
    for st in every["items"]:
        if st["id"] in hidden:  # only stories with no free report are hidden
            sources = [st["primary_source"]["key"]] + [o["source"]["key"] for o in st["others"]]
            assert set(sources) == {"demo-policy"}
    counts = {
        c["slug"]: c["count"]
        for c in api.get("/api/categories", params={"period": "all", "free_only": True}).json()
    }
    for slug, n in counts.items():
        assert (
            n
            == api.get(
                "/api/stories", params={"period": "all", "category": slug, "free_only": True}
            ).json()["total"]
        )
