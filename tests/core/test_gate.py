from datetime import date, timedelta
import pytest

from outreach.config import GateConfig
from outreach.core.gate import evaluate
from outreach.types import EvidenceItem, SourceClass

TODAY = date(2026, 9, 21)
FIRST_PARTY = frozenset({
    "job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page",
})
CFG = GateConfig(
    min_independent_sources=2,
    require_first_party=True,
    recency_days=180,
    first_party_classes=FIRST_PARTY,
)


def item(eid: int, cls: SourceClass, domain: str, age_days: int) -> EvidenceItem:
    return EvidenceItem(
        id=eid, company_id=1, source_document_id=eid,
        claim="c", quote="q", source_class=cls, publisher_domain=domain,
        published_at=TODAY - timedelta(days=age_days), theme="t",
    )


def test_no_evidence_returns_no_evidence():
    v = evaluate([], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "no_evidence"
    assert v.evidence_ids == ()


def test_two_postings_same_board_count_as_one_source():
    items = [
        item(1, SourceClass.JOB_POSTING, "acme.example", 5),
        item(2, SourceClass.JOB_POSTING, "acme.example", 9),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "insufficient_independent_sources"


def test_same_class_different_publishers_are_independent():
    items = [
        item(1, SourceClass.NEWS, "wire-a.example", 5),
        item(2, SourceClass.NEWS, "wire-b.example", 9),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "no_first_party_source"


def test_two_independent_but_all_stale_fails():
    items = [
        item(1, SourceClass.ENG_BLOG, "acme.example", 400),
        item(2, SourceClass.NEWS, "wire.example", 500),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "all_evidence_stale"


def test_two_independent_fresh_with_first_party_passes():
    items = [
        item(1, SourceClass.ENG_BLOG, "acme.example", 19),
        item(2, SourceClass.NEWS, "wire.example", 40),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is True
    assert v.reason == "passed"
    assert v.evidence_ids == (1, 2)


def test_single_fresh_first_party_source_fails():
    v = evaluate([item(1, SourceClass.CHANGELOG, "acme.example", 3)], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "insufficient_independent_sources"


def test_missing_published_date_never_counts_as_fresh():
    stale_but_dated = item(1, SourceClass.ENG_BLOG, "acme.example", 400)
    undated = EvidenceItem(
        id=2, company_id=1, source_document_id=2, claim="c", quote="q",
        source_class=SourceClass.NEWS, publisher_domain="wire.example",
        published_at=None, theme="t",
    )
    v = evaluate([stale_but_dated, undated], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "all_evidence_stale"
