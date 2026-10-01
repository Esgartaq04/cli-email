from datetime import date
from outreach.types import SourceClass, EvidenceItem, Contact, PostingRef, Company


def test_source_class_values_are_stable_strings():
    assert SourceClass.JOB_POSTING.value == "job_posting"
    assert SourceClass.NEWS.value == "news"


def test_evidence_item_carries_quote_and_provenance():
    item = EvidenceItem(
        id=1, company_id=7, source_document_id=3,
        claim="reconciliation job exceeds its window",
        quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
        source_class=SourceClass.ENG_BLOG,
        publisher_domain="ledgerline.example",
        published_at=date(2026, 9, 2),
        theme="reconciliation-throughput",
    )
    assert item.source_class is SourceClass.ENG_BLOG
    assert item.published_at.year == 2026


def test_unverified_contact_has_no_email():
    c = Contact(
        id=None, company_id=7, full_name="Deepak Raman",
        title="Engineering Lead, Payments", profile_url="https://example.com/p/1",
        email=None, email_status="unverified", provider="hunter",
        looked_up_at=None, contacted_at=None,
    )
    assert c.email is None
    assert c.email_status == "unverified"


def test_posting_ref_defaults_to_unknown_mode_and_type():
    p = PostingRef("Co", "co.example", "Engineer", "u", "Remote")
    assert p.work_mode == "unknown" and p.employment_type == "unknown"


def test_company_positional_construction_still_works():
    c = Company(1, "co.example", "Co", None, None)
    assert c.funding_rounds == () and c.github_org is None


def test_hn_post_source_class_value():
    from outreach.types import SourceClass
    assert SourceClass("hn_post") is SourceClass.HN_POST
