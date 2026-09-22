from datetime import date
from outreach.core.clustering import normalize_theme, cluster_by_theme
from outreach.types import EvidenceItem, SourceClass


def item(eid: int, theme: str) -> EvidenceItem:
    return EvidenceItem(
        id=eid, company_id=1, source_document_id=eid, claim="c", quote="q",
        source_class=SourceClass.ENG_BLOG, publisher_domain="acme.example",
        published_at=date(2026, 9, 1), theme=theme,
    )


def test_normalize_theme_lowercases_and_hyphenates():
    assert normalize_theme("  Reconciliation Throughput ") == "reconciliation-throughput"
    assert normalize_theme("Onboarding_Speed") == "onboarding-speed"
    assert normalize_theme("data--quality") == "data-quality"


def test_cluster_groups_equivalent_labels():
    items = [item(1, "Reconciliation Throughput"), item(2, "reconciliation-throughput"),
             item(3, "Merchant Onboarding")]
    clusters = cluster_by_theme(items)
    assert set(clusters) == {"reconciliation-throughput", "merchant-onboarding"}
    assert [i.id for i in clusters["reconciliation-throughput"]] == [1, 2]


def test_empty_input_gives_empty_clusters():
    assert cluster_by_theme([]) == {}


def test_punctuation_only_themes_are_dropped_not_merged():
    """"", "—" and "..." all normalize to "". Two UNRELATED claims that
    both carry one of these must never land in the same cluster -- that
    would manufacture the independence/corroboration the gate exists to
    require."""
    items = [item(1, "—"), item(2, "..."), item(3, ""),
             item(4, "reconciliation-throughput")]
    clusters = cluster_by_theme(items)
    assert set(clusters) == {"reconciliation-throughput"}
    assert [i.id for i in clusters["reconciliation-throughput"]] == [4]
