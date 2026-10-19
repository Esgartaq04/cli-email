"""Hunter Discover company search, against recorded response shapes via MockTransport.

Discover is free, so what these tests pin is not a bill but the request shape
(one per sector x size, and never a stray filter), the sector and size carried
onto each company it finds, and that an HTTP failure names only the endpoint
and status -- the API key rides in the query string.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from outreach.contacts.hunter import HunterAPIError
from outreach.sources.companies import (CompanyQuery, FakeCompanySearch, FoundCompany,
                                        HunterCompanySearch)
from outreach.types import Location

FIXTURES = Path(__file__).parent.parent / "fixtures"
US = {"headquarters_location": {"include": [{"country": "US"}]}}


def _found(*companies: tuple[str, str]) -> dict:
    """A Discover response listing these (domain, organization) pairs."""
    return {"data": [{"domain": domain, "organization": name} for domain, name in companies]}


def _key(body: dict) -> str:
    return json.dumps(body, sort_keys=True)


def _hunter(respond) -> tuple[HunterCompanySearch, list[dict]]:
    """A search whose Discover calls are recorded and answered by `respond(body)`."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v2/discover"
        assert dict(request.url.params) == {"api_key": "key"}
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(200, json=respond(body))

    return HunterCompanySearch("key", client=httpx.Client(transport=httpx.MockTransport(handler))), bodies


def _answering(*pairs: tuple[dict, dict]):
    """`respond` for `_hunter`: the payload recorded for each body, and a failure for any other."""
    table = {_key(body): payload for body, payload in pairs}

    def respond(body: dict) -> dict:
        assert _key(body) in table, f"unexpected Discover body: {_key(body)}"
        return table[_key(body)]

    return respond


def _failing(status: int) -> HunterCompanySearch:
    return HunterCompanySearch("sk-hunter-SECRET-key", client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))))


# --- search --------------------------------------------------------------------

def test_search_sends_one_request_per_sector_and_size():
    search, bodies = _hunter(lambda body: _found())

    search.search(CompanyQuery(("Banking", "Software Development"), ("51-200",), Location()))

    assert bodies == [
        {"industry": {"include": ["Banking"]}, "headcount": ["51-200"], **US},
        {"industry": {"include": ["Software Development"]}, "headcount": ["51-200"], **US},
    ]


@pytest.mark.parametrize("query,expected", [
    (CompanyQuery(sizes=("1-10", "11-50")), [{"headcount": ["1-10"]}, {"headcount": ["11-50"]}]),
    (CompanyQuery(("Banking", "Insurance")),
     [{"industry": {"include": ["Banking"]}}, {"industry": {"include": ["Insurance"]}}]),
    # Sectors are the outer loop, sizes the inner one.
    (CompanyQuery(("Banking", "Insurance"), ("1-10", "11-50")), [
        {"industry": {"include": ["Banking"]}, "headcount": ["1-10"]},
        {"industry": {"include": ["Banking"]}, "headcount": ["11-50"]},
        {"industry": {"include": ["Insurance"]}, "headcount": ["1-10"]},
        {"industry": {"include": ["Insurance"]}, "headcount": ["11-50"]}]),
], ids=["sizes-only", "sectors-only", "every-pair-in-order"])
def test_search_asks_for_each_combination_in_the_order_given(query, expected):
    search, bodies = _hunter(lambda body: _found())

    search.search(query)

    assert bodies == expected


def test_search_dedupes_across_combinations_and_keeps_the_first_match():
    search, _ = _hunter(_answering(
        ({"industry": {"include": ["Banking"]}, "headcount": ["51-200"]},
         _found(("a.example", "A Bank"), ("b.example", "B Bank"), ("a.example", "A again"))),
        ({"industry": {"include": ["Insurance"]}, "headcount": ["51-200"]},
         _found(("https://www.B.example/", "B Insurance"), ("c.example", "C Insurance")))))

    found = search.search(CompanyQuery(("Banking", "Insurance"), ("51-200",)))

    assert found == [FoundCompany("a.example", "A Bank", "Banking", "51-200"),
                     FoundCompany("b.example", "B Bank", "Banking", "51-200"),
                     FoundCompany("c.example", "C Insurance", "Insurance", "51-200")]


def test_search_carries_the_matched_sector_and_size():
    search, _ = _hunter(_answering(
        ({"industry": {"include": ["Banking"]}, "headcount": ["1-10"]}, _found(("a.example", "A"))),
        ({"industry": {"include": ["Banking"]}, "headcount": ["11-50"]}, _found(("b.example", "B"))),
        ({"industry": {"include": ["Insurance"]}, "headcount": ["1-10"]}, _found(("c.example", "C"))),
        ({"industry": {"include": ["Insurance"]}, "headcount": ["11-50"]}, _found(("d.example", "D")))))

    found = search.search(CompanyQuery(("Banking", "Insurance"), ("1-10", "11-50")))

    assert [(c.domain, c.sector, c.size) for c in found] == [
        ("a.example", "Banking", "1-10"), ("b.example", "Banking", "11-50"),
        ("c.example", "Insurance", "1-10"), ("d.example", "Insurance", "11-50")]


def test_search_with_no_sector_or_size_leaves_them_unset():
    search, _ = _hunter(lambda body: _found(("a.example", "A")))

    assert search.search(CompanyQuery(location=Location())) == [FoundCompany("a.example", "A")]


def test_search_sends_the_headquarters_location():
    search, bodies = _hunter(lambda body: _found())

    search.search(CompanyQuery(location=Location("US", "TX", "Austin")))

    assert bodies == [{"headquarters_location": {
        "include": [{"country": "US", "state": "TX", "city": "Austin"}]}}]


def test_hiring_search_sends_job_titles():
    search, bodies = _hunter(lambda body: _found())

    search.search(CompanyQuery(hiring_titles=("backend engineer",)))

    assert bodies == [{"job_openings": {"job_title": {"include": ["backend engineer"]}}}]


def test_hiring_search_combines_with_the_other_filters():
    search, bodies = _hunter(lambda body: _found())

    search.search(CompanyQuery(("Banking",), ("51-200",), Location("DE"), ("backend engineer", "sre")))

    assert bodies == [{
        "industry": {"include": ["Banking"]}, "headcount": ["51-200"],
        "headquarters_location": {"include": [{"country": "DE"}]},
        "job_openings": {"job_title": {"include": ["backend engineer", "sre"]}}}]


def test_search_reads_the_documented_discover_response():
    payload = json.loads((FIXTURES / "hunter_discover.json").read_text(encoding="utf-8"))
    search, _ = _hunter(lambda body: payload)

    found = search.search(CompanyQuery(("Software Development",)))

    assert found == [FoundCompany("hunter.io", "Hunter", "Software Development", None)]


def test_search_canonicalizes_domains_and_skips_unreadable_ones():
    search, _ = _hunter(lambda body: {"data": [
        {"domain": "https://www.Acme.example/about", "organization": "Acme"},
        {"domain": "", "organization": "Blank"},
        {"domain": None, "organization": "Nothing"},
        {"domain": 42, "organization": "Number"},
        {"organization": "No domain"},
        "junk",
        {"domain": "noname.example", "organization": None},
        {"domain": "blankname.example", "organization": "  "},
    ]})

    found = search.search(CompanyQuery(("Banking",)))

    assert [(c.domain, c.name) for c in found] == [
        ("acme.example", "Acme"), ("noname.example", "noname.example"),
        ("blankname.example", "blankname.example")]


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": "x"}, {"data": {}}, {"data": ["junk", 3]}])
def test_search_with_nothing_in_the_response_finds_nobody(payload):
    search, _ = _hunter(lambda body: payload)

    assert search.search(CompanyQuery(("Banking",))) == []


def test_a_search_without_any_filter_is_refused():
    search, bodies = _hunter(lambda body: _found())

    with pytest.raises(ValueError, match="a company search needs at least one filter"):
        search.search(CompanyQuery())

    assert bodies == []


# --- check ---------------------------------------------------------------------

def _known(*domains: str) -> dict:
    return {"organization": {"domain": list(domains)}}


def test_check_separates_fits_misfits_and_unknown_domains():
    search, bodies = _hunter(_answering(
        (_known("a.example", "b.example", "c.example"), _found(("a.example", "A"), ("b.example", "B"))),
        ({**_known("a.example", "b.example"), "industry": {"include": ["Banking"]}},
         _found(("a.example", "A")))))

    result = search.check(["a.example", "b.example", "c.example"], CompanyQuery(("Banking",)))

    assert result == {"a.example": FoundCompany("a.example", "A", "Banking", None), "b.example": None}
    assert "c.example" not in result
    assert len(bodies) == 2


def test_check_carries_the_matched_sector_and_size_and_first_match_wins():
    search, _ = _hunter(_answering(
        (_known("a.example", "b.example"), _found(("a.example", "A"), ("b.example", "B"))),
        ({**_known("a.example", "b.example"), "industry": {"include": ["Banking"]}, "headcount": ["1-10"]},
         _found()),
        ({**_known("a.example", "b.example"), "industry": {"include": ["Banking"]}, "headcount": ["11-50"]},
         _found(("a.example", "A"))),
        ({**_known("a.example", "b.example"), "industry": {"include": ["Insurance"]}, "headcount": ["1-10"]},
         _found(("a.example", "A"), ("b.example", "B"))),
        ({**_known("a.example", "b.example"), "industry": {"include": ["Insurance"]}, "headcount": ["11-50"]},
         _found(("b.example", "B")))))

    result = search.check(["a.example", "b.example"], CompanyQuery(("Banking", "Insurance"), ("1-10", "11-50")))

    assert result == {"a.example": FoundCompany("a.example", "A", "Banking", "11-50"),
                      "b.example": FoundCompany("b.example", "B", "Insurance", "1-10")}


def test_check_never_sends_location_or_hiring_filters():
    search, bodies = _hunter(lambda body: _found(("a.example", "A")))

    search.check(["a.example"], CompanyQuery(("Banking", "Insurance"), ("1-10",), Location("US", "TX"),
                                             ("backend engineer",)))

    assert len(bodies) == 3  # the known-domains call, then one per sector and size
    assert bodies[0] == _known("a.example")
    for body in bodies:
        assert set(body) <= {"organization", "industry", "headcount"}


def test_check_with_no_sector_or_size_needs_only_the_known_domains_call():
    search, bodies = _hunter(lambda body: _found(("a.example", "A")))

    result = search.check(["a.example", "b.example"], CompanyQuery(location=Location(), hiring_titles=("sre",)))

    assert bodies == [_known("a.example", "b.example")]
    assert result == {"a.example": FoundCompany("a.example", "A")}


def test_check_canonicalizes_dedupes_and_ignores_domains_it_was_not_asked_about():
    search, bodies = _hunter(lambda body: _found(("https://www.A.example/", "A"), ("stranger.example", "S")))

    result = search.check(["https://www.A.example/", "a.example", "", "b.example"], CompanyQuery(location=Location()))

    assert bodies == [_known("a.example", "b.example")]
    assert result == {"a.example": FoundCompany("a.example", "A")}


def test_check_sends_domains_in_chunks_of_100():
    domains = [f"d{i}.example" for i in range(250)]
    search, bodies = _hunter(lambda body: _found(*((d, d) for d in body["organization"]["domain"])))

    result = search.check(domains, CompanyQuery(("Banking",)))

    asked = [body["organization"]["domain"] for body in bodies if "industry" not in body]
    narrowed = [body["organization"]["domain"] for body in bodies if "industry" in body]
    assert [len(chunk) for chunk in asked] == [100, 100, 50] and sum(asked, []) == domains
    assert [len(chunk) for chunk in narrowed] == [100, 100, 50] and sum(narrowed, []) == domains
    assert list(result) == domains  # in the order asked


def test_check_narrows_only_the_domains_hunter_knows():
    domains = [f"d{i}.example" for i in range(250)]
    # Hunter knows the first domain of each chunk it is asked about, and the narrowed call is
    # asked only about those three -- none of the other 247.
    search, bodies = _hunter(lambda body: _found(*((d, d) for d in body["organization"]["domain"][:1])))

    result = search.check(domains, CompanyQuery(("Banking",)))

    narrowed = [body["organization"]["domain"] for body in bodies if "industry" in body]
    assert narrowed == [["d0.example", "d100.example", "d200.example"]]
    # The stub answers a narrowed call with the first domain it is asked about, so only that fits.
    assert result == {"d0.example": FoundCompany("d0.example", "d0.example", "Banking"),
                      "d100.example": None, "d200.example": None}


def test_check_of_nothing_sends_nothing():
    search, bodies = _hunter(lambda body: _found())

    assert search.check([], CompanyQuery(("Banking",))) == {}
    assert search.check(["", "  "], CompanyQuery(("Banking",))) == {}
    assert bodies == []


def test_check_skips_the_filtered_calls_when_hunter_knows_none_of_them():
    search, bodies = _hunter(lambda body: _found())

    assert search.check(["a.example"], CompanyQuery(("Banking",), ("1-10",))) == {}
    assert bodies == [_known("a.example")]


# --- errors --------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda search: search.search(CompanyQuery(("Banking",))),
    lambda search: search.check(["a.example"], CompanyQuery(("Banking",))),
], ids=["search", "check"])
def test_discover_errors_name_only_path_and_status(call):
    """The key rides in the query string and the pipeline persists exception
    text, so a failure must name the endpoint and status and nothing of the URL."""
    with pytest.raises(HunterAPIError) as caught:
        call(_failing(403))

    exc = caught.value
    assert str(exc) == "Hunter discover returned HTTP 403"
    assert "sk-hunter-SECRET-key" not in str(exc) + repr(exc)
    assert "api_key" not in str(exc) and "?" not in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None


# --- the fake ------------------------------------------------------------------

BANK = FoundCompany("bank.example", "Bank", "Banking", "51-200")
SOFT = FoundCompany("soft.example", "Soft", "Software Development", "11-50")
HIRER = FoundCompany("hirer.example", "Hirer", "Banking", "51-200")


def test_fake_search_and_check_follow_the_same_rules():
    fake = FakeCompanySearch([BANK, SOFT], hiring=[HIRER],
                             known={"bank.example": BANK, "soft.example": SOFT, "hirer.example": HIRER})

    # Search: the plain pool narrowed by sector and size, each only when given.
    assert fake.search(CompanyQuery(location=Location())) == [BANK, SOFT]
    assert fake.search(CompanyQuery(("Banking",))) == [BANK]
    assert fake.search(CompanyQuery(("Banking", "Software Development"))) == [BANK, SOFT]
    assert fake.search(CompanyQuery(sizes=("11-50",))) == [SOFT]
    assert fake.search(CompanyQuery(("Banking",), ("11-50",))) == []
    # A hiring query reads the hiring pool instead.
    assert fake.search(CompanyQuery(hiring_titles=("engineer",))) == [HIRER]
    assert fake.search(CompanyQuery(("Software Development",), hiring_titles=("engineer",))) == []

    # Check: known and fitting is the company, known and not fitting is None, unknown is absent.
    query = CompanyQuery(("Banking",), ("51-200",))
    assert fake.check(["bank.example", "soft.example", "nobody.example"], query) == {
        "bank.example": BANK, "soft.example": None}
    assert fake.check(["hirer.example"], CompanyQuery(("Banking",), hiring_titles=("sre",))) == {
        "hirer.example": HIRER}
    assert fake.check(["bank.example", "soft.example"], CompanyQuery(location=Location())) == {
        "bank.example": BANK, "soft.example": SOFT}


def test_fake_records_every_search_and_check():
    fake = FakeCompanySearch(known={"bank.example": BANK})
    first, second = CompanyQuery(("Banking",)), CompanyQuery(sizes=("1-10",))

    fake.search(first)
    fake.search(second)
    fake.check(["bank.example", "x.example"], first)

    assert fake.queries == [first, second]
    assert fake.checks == [(("bank.example", "x.example"), first)]


def test_fake_search_raises_its_error_but_still_records_the_query():
    fake = FakeCompanySearch([BANK], error=HunterAPIError("Hunter discover returned HTTP 500"))
    query = CompanyQuery(("Banking",))

    with pytest.raises(HunterAPIError, match="HTTP 500"):
        fake.search(query)

    assert fake.queries == [query]


def test_an_empty_fake_finds_and_knows_nothing():
    fake = FakeCompanySearch()

    assert fake.search(CompanyQuery(("Banking",))) == []
    assert fake.check(["a.example"], CompanyQuery(("Banking",))) == {}
