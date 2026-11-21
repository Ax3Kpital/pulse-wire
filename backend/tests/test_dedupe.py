"""Story grouping: labelled false-positive / false-negative pairs + index behaviour."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.dedupe import ClusterIndex, ItemFeatures, build_features, compare
from app.services.entities import get_matcher
from app.services.text import extract_numbers

PAIRS = json.loads((Path(__file__).parent / "fixtures" / "dedupe_pairs.json").read_text())["pairs"]
T0 = datetime(2026, 11, 14, 12, tzinfo=UTC)


def feats(key: int, title: str, summary: str | None, at: datetime, story_id: int | None = None
          ) -> ItemFeatures:  # fmt: skip
    m = get_matcher()
    ents = m.find(title) | m.find(summary or "")
    return build_features(key=key, story_id=story_id, source_id=key, published_at=at, title=title,
                          summary=summary, entities=ents,
                          numbers=extract_numbers(f"{title} {summary or ''}"))  # fmt: skip


@pytest.mark.parametrize("pair", PAIRS, ids=[p["name"] for p in PAIRS])
def test_labelled_pairs(pair: dict) -> None:
    a = feats(1, pair["a"]["title"], pair["a"]["summary"], T0)
    b = feats(2, pair["b"]["title"], pair["b"]["summary"], T0 + timedelta(hours=pair["hours"]))
    decision = compare(a, b, get_matcher())
    assert decision.merge is pair["same"], decision.as_reason()
    # symmetric and explainable
    assert compare(b, a, get_matcher()).merge is pair["same"]
    assert decision.rule and "title_sim" in decision.signals


def test_same_company_alone_never_merges() -> None:
    a = feats(1, "Northwind Bank names new chief financial officer", None, T0)
    b = feats(2, "Northwind Bank opens branches in Texas", None, T0)
    d = compare(a, b, get_matcher())
    assert not d.merge
    assert d.signals["shared_entities"] == ["northwind-bank"]


def test_index_picks_best_story_deterministically() -> None:
    index = ClusterIndex(get_matcher(), lsh_threshold=1000)
    index.add(feats(1, "OPEC+ agrees to extend oil production cuts through March", None, T0, 10))
    index.add(feats(2, "Northwind Bank to acquire Bayfront for $2.3 billion", None, T0, 11))
    match = index.best_match(feats(3, "OPEC+ extends production cuts through March", None, T0))
    assert match is not None
    assert match[0] == 10
    assert match[2] == "exhaustive"


def test_lsh_shortlist_used_when_window_is_crowded() -> None:
    index = ClusterIndex(get_matcher(), lsh_threshold=5)
    for i in range(30):
        index.add(feats(i + 1, f"Unrelated headline number {i} about topic{i} widgets", None, T0,
                        100 + i))  # fmt: skip
    index.add(feats(99, "OPEC+ agrees to extend oil production cuts through March", None, T0, 7))
    candidates, method = index.candidates(
        feats(1000, "OPEC+ extends oil production cuts through March", None, T0)
    )
    assert method == "minhash_lsh"
    assert any(c.story_id == 7 for c in candidates)
    assert len(candidates) < 31
    match = index.best_match(
        feats(1000, "OPEC+ extends oil production cuts through March", None, T0)
    )
    assert match is not None and match[0] == 7


def test_index_remove_and_readd() -> None:
    index = ClusterIndex(get_matcher())
    f = feats(5, "OPEC+ extends production cuts", None, T0, 1)
    index.add(f)
    index.add(f)  # re-adding the same key must not raise
    index.remove(5)
    assert 5 not in index.items


def test_same_source_items_never_cluster() -> None:
    a = feats(1, "Presidential Message on National Diabetes Month", None, T0)
    b = feats(1, "Presidential Message on National Entrepreneurship Month", None, T0)
    d = compare(a, b, get_matcher())
    assert not d.merge and d.rule == "same_source"


def test_different_dates_in_title_never_cluster() -> None:
    a = feats(1, "Minutes of the London FXJSC Main Committee Meeting – 16 June 2026", None, T0)
    b = feats(2, "Minutes of the London FXJSC Main Committee Meeting – 24 June 2026", None, T0)
    d = compare(a, b, get_matcher())
    assert not d.merge and d.rule == "date_conflict"


def test_idf_separates_templated_headlines_about_different_subjects() -> None:
    """Two outlets, two different FDA approvals: shared boilerplate must not merge them."""
    index = ClusterIndex(get_matcher(), lsh_threshold=1000)
    drugs = [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "omicron",
        "pi",
        "rho",
        "sigma",
        "tau",
        "upsilon",
        "phi",
        "chi",
        "psi",
        "omega",
        "aleph",
        "bet",
        "gimel",
        "dalet",
        "he",
        "vav",
    ]
    for i, d in enumerate(drugs):
        index.add(
            feats(
                100 + i, f"FDA grants accelerated approval to {d}mab for cancer", None, T0, 500 + i
            )
        )
    assert index.idf() is not None
    index.add(
        feats(
            1,
            "FDA grants accelerated approval to zanidatamab for biliary tract cancer",
            None,
            T0,
            1,
        )
    )
    new = feats(
        2,
        "FDA grants accelerated approval to tucatinib with trastuzumab for colorectal cancer",
        None,
        T0 + timedelta(hours=1),
    )
    assert index.best_match(new) is None
    # ...while a genuine second report on the same drug still joins
    same = feats(
        3,
        "Zanidatamab wins FDA accelerated approval in biliary tract cancer",
        None,
        T0 + timedelta(hours=2),
    )
    match = index.best_match(same)
    assert match is not None and match[0] == 1
    assert match[1].signals["weighting"] == "idf"
