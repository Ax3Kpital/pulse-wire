"""Normalisation helpers and the explainable rule-based classifier."""

from __future__ import annotations

from app.services.classifier import get_classifier
from app.services.entities import get_matcher
from app.services.text import canonicalize_url, extract_numbers, normalize_title, strip_html


def classify(title: str, summary: str | None = None, section: str | None = None, **kw):  # type: ignore[no-untyped-def]
    m = get_matcher()
    return get_classifier().classify(
        title=title, summary=summary, section=section, title_entities=m.find(title),
        summary_entities=m.find(summary or ""), **kw,
    )  # fmt: skip


# --------------------------------------------------------------------------- text


def test_canonical_url_strips_tracking_and_noise() -> None:
    a = canonicalize_url("http://www.Example.com/a/b/?utm_source=x&id=2&fbclid=z#frag")
    b = canonicalize_url("https://example.com/a/b?id=2")
    assert a == b == "https://example.com/a/b?id=2"


def test_normalize_title_removes_publisher_suffix_and_accents() -> None:
    assert normalize_title("Café prices soar — Demo Wire", "Demo Wire") == "cafe prices soar"
    assert normalize_title("Fed cuts 0.25% <b>today</b>") == "fed cuts 0.25% today"


def test_extract_numbers() -> None:
    nums = extract_numbers(
        "Fed cut 25 basis points; CPI 3.1%; deal $2.3 billion; 142,000 jobs in 2026"
    )
    assert {"bp:25", "pct:3.1", "money:$:2.3e+09", "num:142000"} <= set(nums)
    assert "num:2026" not in nums  # years are ignored
    assert "bp:25" in extract_numbers("a quarter-point cut")


def test_strip_html() -> None:
    assert strip_html("<p>Hello&nbsp;<script>x()</script><b>world</b></p>") == "Hello world"


# --------------------------------------------------------------------------- classifier


def test_fed_rate_decision() -> None:
    cats = classify("Fed cuts interest rates by 25 basis points")
    assert {"fed", "fed-rates"} <= set(cats)
    reasons = cats["fed-rates"].reasons
    assert any(r["signal"] == "keyword" for r in reasons)  # explainable


def test_ecb_story_is_not_fed() -> None:
    cats = classify("ECB holds interest rates steady as inflation eases",
                    "The European Central Bank kept its monetary policy unchanged.")  # fmt: skip
    assert "fed" not in cats and "fed-rates" not in cats


def test_source_exclusion() -> None:
    cats = classify("Monetary policy decisions", "Interest rates unchanged; rate cut ruled out.",
                    exclude_categories=["fed"])  # fmt: skip
    assert not any(slug.startswith("fed") for slug in cats)


def test_geopolitics_with_regional_subfilters_and_multi_label() -> None:
    cats = classify(
        "China tightens export controls on rare earths used in chipmaking",
        "Beijing escalated trade tensions with Washington; semiconductor supply hit.",
    )
    assert {"geopolitics", "geo-china", "tech"} <= set(cats)


def test_country_mention_alone_is_not_geopolitics() -> None:
    cats = classify("China's largest bank reports higher deposits")
    assert "geo-china" not in cats
    assert "banking" in cats


def test_lowercase_us_pronoun_does_not_trigger_us() -> None:
    cats = classify("Sanctions hit exporters, analysts tell us", "Let us see the trade war impact")
    assert "geo-us" not in cats


def test_section_rule_matches_whole_words_only() -> None:
    cats = classify("Helixa Bio gene therapy meets main goal in phase 3 trial", section="Biotech")
    assert "biotech" in cats and "tech" not in cats


def test_source_default_and_child_implies_parent() -> None:
    cats = classify("Consumer Price Index – October 2026", default_categories=["fed-cpi"])
    assert "fed-cpi" in cats and "fed" in cats
    assert cats["fed"].reasons[0]["signal"] == "implied_by_child"


def test_every_sidebar_category_reachable() -> None:
    samples = {
        "energy": "Power grid operators add battery storage as renewables surge",
        "banking": "Banks face tougher capital requirements after stress tests",
        "biotech": "Biotech's CRISPR gene therapy shows durable response",
        "pharma": "FDA approves drugmaker's new obesity drug",
        "oil-gas": "Crude oil prices jump as OPEC trims output",
        "tech": "Chipmaker unveils AI semiconductor for data centers",
        "geo-europe": "EU sanctions target Russian oil exports, Brussels says",
        "geo-russia": "Kremlin warns of retaliation over new sanctions",
        "geo-us": "White House imposes tariffs in trade war escalation",
        "fed-pce": "PCE price index rises 2.8%",
        "fed-jobs": "Nonfarm payrolls rise 142,000; unemployment rate steady",
        "fed-speeches": "Powell speech: Federal Reserve remains data dependent",
    }
    for slug, title in samples.items():
        assert slug in classify(title), slug


def test_entity_names_are_not_keywords() -> None:
    cats = classify("Bank of England announces the animals on its new banknotes")
    assert "banking" not in cats
    cats = classify("Bank of America profit rises as lending grows")
    assert "banking" in cats  # via the company entity
    assert any(r["signal"] == "entity" for r in cats["banking"].reasons)
