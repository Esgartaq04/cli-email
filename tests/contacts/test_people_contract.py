"""What every `PeopleFinder` must do, run against the fake and against
`HunterProvider` serving the recorded Hunter shapes; then the fake's own knobs,
which the contact buyer's tests depend on."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from outreach.contacts.base import PeopleFinder
from outreach.contacts.fake import FakePeopleFinder
from outreach.contacts.hunter import HunterProvider
from outreach.types import FoundPerson

FIXTURES = Path(__file__).parent.parent / "fixtures"
DOMAIN = "acme.example"
TITLES = ("support engineer", "co-founder", "engineering manager")

PEOPLE = [
    FoundPerson("Ciaran Lee", "Support Engineer", "ciaran@acme.example"),
    FoundPerson("Maria Santos", "Co-founder & CTO", "maria@acme.example"),
    FoundPerson("Tom Becker", "Engineering Manager", "tom@acme.example"),
]


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fake() -> FakePeopleFinder:
    return FakePeopleFinder({DOMAIN: PEOPLE})


def _hunter() -> HunterProvider:
    """Hunter knows only DOMAIN; every other domain has nobody."""
    def handler(request: httpx.Request) -> httpx.Response:
        known = request.url.params.get("domain") == DOMAIN
        path = request.url.path
        if path == "/v2/domain-search":
            return httpx.Response(200, json=_fixture("hunter_domain_search.json") if known
                                  else {"data": {"domain": "x", "emails": []}})
        if path == "/v2/email-count":
            return httpx.Response(200, json=_fixture("hunter_email_count.json") if known
                                  else {"data": {"total": 0, "personal_emails": 0}})
        if path == "/v2/email-verifier":
            return httpx.Response(200, json={"data": {"status": "valid"}})
        if path == "/v2/account":
            return httpx.Response(200, json=_fixture("hunter_account.json"))
        return httpx.Response(404)
    return HunterProvider("key", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(params=[_fake, _hunter], ids=["fake", "hunter"])
def finder(request) -> PeopleFinder:
    return request.param()


def test_search_people_returns_found_people_up_to_the_limit(finder):
    found = finder.search_people(DOMAIN, TITLES, 2)

    assert len(found) == 2
    assert all(isinstance(p, FoundPerson) and p.email for p in found)
    assert len(finder.search_people(DOMAIN, TITLES, 1)) == 1


def test_an_unknown_domain_has_nobody(finder):
    assert finder.search_people("nobody.example", TITLES, 3) == []
    assert finder.count_people("nobody.example") == 0


def test_count_people_is_a_whole_number(finder):
    count = finder.count_people(DOMAIN)
    assert type(count) is int and count >= 0


def test_verify_answers_with_one_of_the_three_statuses(finder):
    assert finder.verify("maria@acme.example") in ("verified", "unverified", "not_found")


def test_balance_is_a_whole_number_of_tenths(finder):
    balance = finder.balance_tenths()
    assert type(balance) is int and balance >= 0


# --- FakePeopleFinder's own knobs ----------------------------------------------

def test_the_fake_counts_the_people_it_holds_unless_told_otherwise():
    fake = FakePeopleFinder({DOMAIN: PEOPLE}, counts={"big.example": 40})
    assert [fake.count_people(d) for d in (DOMAIN, "big.example", "none.example")] == [3, 40, 0]
    assert fake.count_calls == [DOMAIN, "big.example", "none.example"]


def test_the_fake_searches_by_title_in_list_order_up_to_the_limit():
    fake = FakePeopleFinder({DOMAIN: PEOPLE})

    assert [p.email for p in fake.search_people(DOMAIN, ("engineering manager", "cto"), 5)] == [
        "maria@acme.example", "tom@acme.example"]
    assert [p.email for p in fake.search_people(DOMAIN, ("engineering manager", "cto"), 1)] == [
        "maria@acme.example"]
    assert fake.search_people(DOMAIN, ("designer",), 5) == []
    assert fake.search_people("none.example", ("cto",), 5) == []
    # A limit of nothing (or less) is nobody, not "all but the last".
    assert fake.search_people(DOMAIN, ("engineering manager", "cto"), 0) == []
    assert fake.search_people(DOMAIN, ("engineering manager", "cto"), -1) == []


def test_the_fake_records_every_search_with_its_titles_and_limit():
    fake = FakePeopleFinder({DOMAIN: PEOPLE})
    fake.search_people(DOMAIN, ["founder", "cto"], 2)
    fake.search_people("none.example", ("tech lead",), 1)
    assert fake.search_calls == [(DOMAIN, ("founder", "cto"), 2), ("none.example", ("tech lead",), 1)]


def test_the_fake_fails_a_search_that_asks_for_a_failing_title_and_still_records_it():
    fake = FakePeopleFinder({DOMAIN: PEOPLE}, failing_titles=frozenset({"tech lead"}))

    assert [p.email for p in fake.search_people(DOMAIN, ("cto",), 3)] == ["maria@acme.example"]
    with pytest.raises(RuntimeError, match="search failed for acme.example"):
        fake.search_people(DOMAIN, ("cto", "tech lead"), 3)
    assert fake.search_calls[-1] == (DOMAIN, ("cto", "tech lead"), 3)


def test_the_fake_verifies_everyone_unless_given_a_status():
    fake = FakePeopleFinder(statuses={"bounce@acme.example": "not_found", "maybe@acme.example": "unverified"})
    assert [fake.verify(e) for e in ("a@acme.example", "bounce@acme.example", "maybe@acme.example")] == [
        "verified", "not_found", "unverified"]
    assert fake.verify_calls == ["a@acme.example", "bounce@acme.example", "maybe@acme.example"]


def test_the_fake_reports_its_balance_or_the_error_it_was_given():
    assert FakePeopleFinder().balance_tenths() == 500
    assert FakePeopleFinder(balance=0).balance_tenths() == 0
    broken = FakePeopleFinder(balance_error=ValueError("Hunter account response has no credit balance"))
    with pytest.raises(ValueError, match="no credit balance"):
        broken.balance_tenths()


def test_fakes_do_not_share_call_logs():
    first, second = FakePeopleFinder(), FakePeopleFinder()
    first.count_people(DOMAIN)
    first.verify("a@acme.example")
    first.search_people(DOMAIN, ("cto",), 1)
    assert (second.count_calls, second.verify_calls, second.search_calls) == ([], [], [])
