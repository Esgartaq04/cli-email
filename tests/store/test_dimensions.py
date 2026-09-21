# tests/store/test_dimensions.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.types import PersonRef, SourceClass, SourceDocument


def repos(tmp_path):
    conn = connect(tmp_path / "t.db")
    return CompanyRepo(conn), ContactRepo(conn)


def test_company_upsert_is_stable_across_runs(tmp_path):
    companies, _ = repos(tmp_path)
    first = companies.upsert("foo.com", "Foo", 48, "careers page")
    second = companies.upsert("foo.com", "Foo Inc", 52, "careers page")
    assert first == second
    assert companies.get(first).headcount == 52


def test_contact_resolved_in_earlier_run_is_found_again(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("Marisol Okonkwo", "CTO", None,
                       "m@foo.com", "verified")
    contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    found = contacts.already_resolved(cid, "Marisol Okonkwo")
    assert found is not None
    assert found.email == "m@foo.com"
    assert found.email_status == "verified"


def test_unverified_contact_persists_without_email(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("Deepak Raman", "Eng Lead", "https://p/1", None, "unverified")
    contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    stored = contacts.for_company(cid)[0]
    assert stored.email is None
    assert stored.email_status == "unverified"


def test_mark_contacted_is_visible_on_next_read(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("A B", "CTO", None, "a@foo.com", "verified")
    contact_id = contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    contacts.mark_contacted(contact_id, datetime(2026, 9, 5))
    assert contacts.for_company(cid)[0].contacted_at is not None


def test_reresolving_as_unverified_clears_a_previously_verified_email(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    verified = PersonRef("Marisol Okonkwo", "CTO", None, "m@foo.com", "verified")
    contacts.upsert(cid, verified, "hunter", datetime(2026, 9, 1))

    downgraded = PersonRef("Marisol Okonkwo", "CTO", None, None, "unverified")
    contacts.upsert(cid, downgraded, "hunter", datetime(2026, 9, 8))

    stored = contacts.already_resolved(cid, "Marisol Okonkwo")
    assert stored is not None
    assert stored.email is None
    assert stored.email_status == "unverified"


def test_reresolving_as_verified_after_verified_keeps_the_address(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    first = PersonRef("Marisol Okonkwo", "CTO", None, "m@foo.com", "verified")
    contacts.upsert(cid, first, "hunter", datetime(2026, 9, 1))

    again = PersonRef("Marisol Okonkwo", "CTO", None, "m@foo.com", "verified")
    contacts.upsert(cid, again, "hunter", datetime(2026, 9, 8))

    stored = contacts.already_resolved(cid, "Marisol Okonkwo")
    assert stored is not None
    assert stored.email == "m@foo.com"
    assert stored.email_status == "verified"


def test_document_insert_dedupes_and_returns_original_id(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, docs = CompanyRepo(conn), DocumentRepo(conn)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    doc_a = SourceDocument(None, cid, "https://foo.com/a", SourceClass.CAREERS_PAGE,
                           "foo.com", None, datetime(2026, 9, 1), 200, "hash-a")
    doc_b = SourceDocument(None, cid, "https://foo.com/b", SourceClass.CAREERS_PAGE,
                           "foo.com", None, datetime(2026, 9, 1), 200, "hash-b")
    id_a = docs.insert(doc_a)
    id_b = docs.insert(doc_b)
    assert id_b != id_a
    # Re-inserting a document that already exists (same url + content_hash)
    # must return its original id, not a stale/unrelated rowid, even though
    # a different row was inserted more recently on this connection.
    assert docs.insert(doc_a) == id_a
    assert len(docs.for_company(cid)) == 2
