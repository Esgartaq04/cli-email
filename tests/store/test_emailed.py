# tests/store/test_emailed.py
from datetime import date, datetime

from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.emailed import EmailedRepo
from outreach.types import EmailedEntry, PersonRef

NOW = datetime(2026, 10, 12, 9)


def repos(tmp_path):
    conn = connect(tmp_path / "t.db")
    return conn, EmailedRepo(conn), CompanyRepo(conn), ContactRepo(conn)


def test_add_get_and_remove(tmp_path):
    _, emailed, _, _ = repos(tmp_path)
    emailed.add(["a@x.example", "b@y.example"], date(2026, 10, 1), NOW)
    assert emailed.get("a@x.example") == date(2026, 10, 1)
    assert emailed.get("b@y.example") == date(2026, 10, 1)
    assert emailed.get("c@z.example") is None
    # Only the addresses that were on the list come back.
    assert emailed.remove(["a@x.example", "c@z.example"]) == ["a@x.example"]
    assert emailed.get("a@x.example") is None
    assert emailed.get("b@y.example") == date(2026, 10, 1)
    assert emailed.remove(["a@x.example"]) == []


def test_removing_an_address_listed_twice_reports_it_once(tmp_path):
    _, emailed, _, _ = repos(tmp_path)
    emailed.add(["a@x.example"], date(2026, 10, 1), NOW)
    assert emailed.remove(["a@x.example", "a@x.example"]) == ["a@x.example"]


def test_adding_again_updates_the_date(tmp_path):
    _, emailed, _, _ = repos(tmp_path)
    emailed.add(["a@x.example"], date(2026, 10, 1), NOW)
    emailed.add(["a@x.example"], date(2026, 10, 9), NOW)
    assert emailed.get("a@x.example") == date(2026, 10, 9)
    assert [e.address for e in emailed.all()] == ["a@x.example"]


def test_all_is_empty_for_a_new_list(tmp_path):
    _, emailed, _, _ = repos(tmp_path)
    assert emailed.all() == []


def test_all_lists_newest_first_with_what_is_known(tmp_path):
    _, emailed, companies, contacts = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    contacts.upsert(cid, PersonRef("Dana Whit", "CTO", None, "dana@kestrel.example", "verified"),
                    "hunter", datetime(2026, 9, 1))
    emailed.add(["zed@w.example", "ann@w.example"], date(2026, 10, 1), NOW)
    emailed.add(["dana@kestrel.example"], date(2026, 10, 5), NOW)
    emailed.add(["old@v.example"], date(2026, 9, 1), NOW)
    assert emailed.all() == [
        EmailedEntry("dana@kestrel.example", date(2026, 10, 5), "Dana Whit", "CTO", "Kestrel"),
        EmailedEntry("ann@w.example", date(2026, 10, 1)),
        EmailedEntry("zed@w.example", date(2026, 10, 1)),
        EmailedEntry("old@v.example", date(2026, 9, 1)),
    ]


def test_all_does_not_repeat_an_address_two_stored_contacts_share(tmp_path):
    _, emailed, companies, contacts = repos(tmp_path)
    a = companies.upsert("a.example", "A", None, None)
    b = companies.upsert("b.example", "B", None, None)
    for cid in (a, b):
        contacts.upsert(cid, PersonRef("Dana Whit", "CTO", None, "dana@shared.example", "verified"),
                        "hunter", datetime(2026, 9, 1))
    emailed.add(["dana@shared.example"], date(2026, 10, 5), NOW)
    assert [e.address for e in emailed.all()] == ["dana@shared.example"]


def test_mark_for_company_matches_the_address_domain(tmp_path):
    _, emailed, companies, _ = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    emailed.add(["someone@kestrel.example", "else@other.example"], date(2026, 10, 1), NOW)
    mark = emailed.mark_for_company(cid, "kestrel.example")
    assert mark == EmailedEntry("someone@kestrel.example", date(2026, 10, 1))
    # `www.` and a full URL both reduce to the same domain.
    assert emailed.mark_for_company(cid, "www.kestrel.example") == mark
    assert emailed.mark_for_company(cid, "https://www.kestrel.example/careers") == mark
    assert emailed.mark_for_company(None, "kestrel.example") == mark
    assert emailed.mark_for_company(cid, "kestrel.other") is None


def test_mark_for_company_does_not_match_a_longer_or_sub_domain(tmp_path):
    _, emailed, companies, _ = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    emailed.add(["a@notkestrel.example", "b@mail.kestrel.example", "c@kestrel.example.org"],
                date(2026, 10, 1), NOW)
    assert emailed.mark_for_company(cid, "kestrel.example") is None


def test_mark_for_company_returns_the_newest_entry(tmp_path):
    _, emailed, companies, _ = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    emailed.add(["old@kestrel.example"], date(2026, 9, 1), NOW)
    emailed.add(["new@kestrel.example"], date(2026, 10, 1), NOW)
    assert emailed.mark_for_company(cid, "kestrel.example").address == "new@kestrel.example"


def test_mark_for_company_matches_a_contact_at_another_domain(tmp_path):
    _, emailed, companies, contacts = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    other = companies.upsert("other.example", "Other", None, None)
    contacts.upsert(cid, PersonRef("Dana Whit", "CTO", None, "dana@gmail.example", "verified"),
                    "hunter", datetime(2026, 9, 1))
    emailed.add(["dana@gmail.example"], date(2026, 10, 1), NOW)
    assert emailed.mark_for_company(cid, "kestrel.example") == EmailedEntry(
        "dana@gmail.example", date(2026, 10, 1), "Dana Whit", "CTO", "Kestrel")
    # The address belongs to Kestrel's contact, not to every company.
    assert emailed.mark_for_company(other, "other.example") is None


def test_mark_for_company_matches_case_insensitively(tmp_path):
    _, emailed, companies, contacts = repos(tmp_path)
    cid = companies.upsert("kestrel.example", "Kestrel", None, None)
    contacts.upsert(cid, PersonRef("Dana Whit", "CTO", None, "Dana@Kestrel.example", "verified"),
                    "hunter", datetime(2026, 9, 1))
    emailed.add(["dana@kestrel.example"], date(2026, 10, 1), NOW)
    assert emailed.mark_for_company(cid, "Kestrel.Example").address == "dana@kestrel.example"
    # Matched through the contact alone, the domain being unrelated.
    assert emailed.mark_for_company(cid, "unrelated.example").address == "dana@kestrel.example"
