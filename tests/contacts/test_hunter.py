"""HunterProvider against recorded Hunter response shapes, via MockTransport.

`HunterProvider` is the layer closest to the data for the verified-email
rule, and `verify()`'s status mapping is the one place a single mis-mapped
branch (e.g. `accept_all` -> "verified") would ship guessed addresses as
verified with nothing to catch it. Injectable client, no live API touched --
same pattern as `tests/test_fetcher.py`.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from outreach.contacts.hunter import HunterAPIError, HunterProvider, _safe_linkedin_url, hunter_post
from outreach.sources.companies import CompanyQuery, HunterCompanySearch
from outreach.types import FoundPerson

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _verifier(status: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/email-verifier"
        return httpx.Response(200, json={"data": {"status": status}})
    return handler


def test_verify_maps_valid_to_verified():
    provider = HunterProvider("key", client=_client(_verifier("valid")))
    assert provider.verify("m@acme.example") == "verified"


def test_verify_maps_accept_all_to_unverified():
    """The load-bearing branch: `accept_all` is common at small companies
    and must NOT map to "verified" -- Hunter did not confirm this specific
    address, only that the domain accepts anything. A one-character edit
    here (`accept_all` treated as valid) would ship guessed addresses as
    verified, and this is the test that must catch it."""
    provider = HunterProvider("key", client=_client(_verifier("accept_all")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_invalid_to_not_found():
    provider = HunterProvider("key", client=_client(_verifier("invalid")))
    assert provider.verify("m@acme.example") == "not_found"


def test_verify_maps_disposable_to_not_found():
    provider = HunterProvider("key", client=_client(_verifier("disposable")))
    assert provider.verify("m@acme.example") == "not_found"


def test_verify_maps_unknown_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("unknown")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_webmail_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("webmail")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_risky_to_unverified():
    provider = HunterProvider("key", client=_client(_verifier("risky")))
    assert provider.verify("m@acme.example") == "unverified"


def test_verify_maps_an_unrecognized_status_to_unverified():
    """Forward-compatible default: anything Hunter adds later that this
    pipeline doesn't recognize must fail closed, not open."""
    provider = HunterProvider("key", client=_client(_verifier("some_new_status")))
    assert provider.verify("m@acme.example") == "unverified"


@pytest.mark.parametrize("value,expected", [
    ("marisol-okonkwo", "https://www.linkedin.com/in/marisol-okonkwo"),
    ("https://evil.example/in/m", None),
    ("https://notlinkedin.com/in/m", None),
    ("https://linkedin.com.evil.example/in/m", None),
    ("javascript:alert(1)", None),
    ("https://uk.linkedin.com/in/m", "https://uk.linkedin.com/in/m"),
    ("https://linkedin.com/in/m", "https://linkedin.com/in/m"),
    ("https://evil.example\\@linkedin.com/in/x", None),
    ("https://evil.example\\@www.linkedin.com/in/x", None),
    ("https://x@linkedin.com/in/m", None),
    ("https://user:pw@www.linkedin.com/in/m", None),
    ("https://linkedin.com:8443/in/m", None),
    ("https://linkedin.com/in/m x", None),
    ("https://linkedin.com/in/\tm", None),
    ("https://www.linkedin.com/in/m?utm=1#frag", "https://www.linkedin.com/in/m"),
    ("ab", None),
    ("has space", None),
    (None, None),
    (42, None),
])
def test_linkedin_handle_is_expanded_and_foreign_hosts_rejected(value, expected):
    assert _safe_linkedin_url(value) == expected


SECRET = "sk-hunter-SECRET-key"


# Every Hunter call the pipeline makes: (call, the endpoint path it uses). The first four
# go through `hunter_get`, the rest through `hunter_post`.
_HUNTER_CALLS = [
    (lambda c: HunterProvider(SECRET, client=c).verify("m@acme.com"), "email-verifier"),
    (lambda c: HunterProvider(SECRET, client=c).count_people("acme.com"), "email-count"),
    (lambda c: HunterProvider(SECRET, client=c).search_people("acme.com", ["cto"], 3), "domain-search"),
    (lambda c: HunterProvider(SECRET, client=c).balance_tenths(), "account"),
    (lambda c: HunterCompanySearch(SECRET, client=c).search(CompanyQuery(("Banking",))), "discover"),
    (lambda c: HunterCompanySearch(SECRET, client=c).check(["acme.com"], CompanyQuery(("Banking",))),
     "discover"),
    (lambda c: hunter_post(c, "discover", SECRET, {"headcount": ["1-10"]}), "discover"),
]
_HUNTER_CALL_IDS = ["verify", "count_people", "search_people", "balance_tenths", "company_search",
                    "company_check", "hunter_post"]


@pytest.mark.parametrize("call", [call for call, _ in _HUNTER_CALLS], ids=_HUNTER_CALL_IDS)
def test_http_errors_never_carry_the_api_key(call):
    """The key rides in the query string, and httpx's own HTTPStatusError
    message embeds the full URL. The pipeline persists exception text, so the
    adapter must replace it with one naming only status and endpoint."""
    with pytest.raises(HunterAPIError) as caught:
        call(_client(lambda request: httpx.Response(500)))
    exc = caught.value
    assert SECRET not in str(exc) and SECRET not in repr(exc)
    assert "api_key" not in str(exc) and "?" not in str(exc)
    assert "HTTP 500" in str(exc)
    assert exc.__cause__ is None and exc.__context__ is None


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
@pytest.mark.parametrize(("call", "path"), _HUNTER_CALLS, ids=_HUNTER_CALL_IDS)
def test_unreachable_hunter_is_a_hunter_error_that_never_carries_the_api_key(call, path, failure):
    """Offline, a DNS failure or a timeout raises an httpx error whose text and repr carry the
    request URL, and so the key. It must reach the caller as a `HunterAPIError` naming only
    the endpoint and the kind of failure, with the original error attached nowhere."""
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise failure(f"cannot reach {request.url}", request=request)

    with pytest.raises(HunterAPIError) as caught:
        call(_client(unreachable))
    exc = caught.value
    assert str(exc) == f"Hunter {path} could not be reached ({failure.__name__})"
    assert SECRET not in str(exc) and SECRET not in repr(exc)
    assert "api_key" not in str(exc) and "?" not in str(exc) and "?" not in repr(exc)
    assert exc.__cause__ is None and exc.__context__ is None


def test_hunter_post_sends_the_body_as_json_and_the_key_only_in_the_query():
    seen: list[httpx.Request] = []
    body = {"organization": {"domain": ["acme.example"]}, "headcount": ["1-10"]}

    payload = hunter_post(_client(_serving({"data": []}, seen)), "discover", "key", body)

    assert payload == {"data": []}
    assert seen[0].method == "POST" and seen[0].url.path == "/v2/discover"
    assert dict(seen[0].url.params) == {"api_key": "key"}
    assert json.loads(seen[0].content) == body  # the key is not in the body


@pytest.mark.parametrize("raw", [b"[]", b"3", b"null"])
def test_hunter_post_reads_a_response_that_is_not_an_object_as_empty(raw):
    client = _client(lambda request: httpx.Response(200, content=raw))

    assert hunter_post(client, "discover", "key", {}) == {}


# --- people search -----------------------------------------------------------

def _serving(payload: dict, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)
    return handler


def _search(payload: dict | None = None, *, titles=("cto",), limit=10) -> list[FoundPerson]:
    provider = HunterProvider("key", client=_client(_serving(
        payload if payload is not None else _fixture("hunter_domain_search.json"))))
    return provider.search_people("acme.example", titles, limit)


def _search_payload(*emails: dict) -> dict:
    return {"data": {"domain": "acme.example", "emails": list(emails)}}


def test_search_people_sends_titles_type_and_limit():
    seen: list[httpx.Request] = []
    provider = HunterProvider("key", client=_client(
        _serving(_fixture("hunter_domain_search.json"), seen)))

    provider.search_people("acme.example", ["founder", "cto"], 2)

    assert len(seen) == 1 and seen[0].method == "GET"
    assert seen[0].url.path == "/v2/domain-search"
    assert dict(seen[0].url.params) == {
        "domain": "acme.example", "job_titles": "founder,cto", "type": "personal",
        "limit": "2", "api_key": "key"}


def test_search_people_reads_the_person_out_of_each_entry():
    ciaran, maria, tom = _search()
    assert ciaran == FoundPerson(
        "Ciaran Lee", "Support Engineer", "ciaran@acme.example", None, "valid", date(2019, 12, 6))
    assert (maria.full_name, maria.title, maria.email) == (
        "Maria Santos", "Co-founder & CTO", "maria@acme.example")
    assert maria.profile_url == "https://www.linkedin.com/in/maria-santos"
    # A bare handle becomes a profile URL.
    assert tom.profile_url == "https://www.linkedin.com/in/tom-becker-eng"


def test_search_people_reads_hunters_own_verification():
    ciaran, maria, tom = _search()
    assert (ciaran.hunter_status, ciaran.hunter_checked_on) == ("valid", date(2019, 12, 6))
    assert (maria.hunter_status, maria.hunter_checked_on) == ("valid", date(2026, 9, 30))
    # Hunter has not checked this one: nothing is claimed for it.
    assert (tom.hunter_status, tom.hunter_checked_on) == (None, None)


def test_search_people_reads_a_timestamp_as_its_date_and_ignores_a_bad_one():
    payload = _fixture("hunter_domain_search.json")
    payload["data"]["emails"][0]["verification"] = {"date": "2026-09-30T08:15:00+00:00", "status": "valid"}
    payload["data"]["emails"][1]["verification"] = {"date": "last week", "status": "valid"}
    first, second, _ = _search(payload)
    assert first.hunter_checked_on == date(2026, 9, 30)
    assert (second.hunter_status, second.hunter_checked_on) == ("valid", None)


def test_search_people_never_returns_more_than_limit():
    assert len(_fixture("hunter_domain_search.json")["data"]["emails"]) == 3
    assert [p.email for p in _search(limit=2)] == ["ciaran@acme.example", "maria@acme.example"]
    assert len(_search(limit=10)) == 3


@pytest.mark.parametrize("titles,limit", [(["cto"], 0), (["cto"], -1), ([], 3)])
def test_search_people_with_nothing_to_ask_for_sends_no_request(titles, limit):
    """No titles would be an unfiltered search -- paid-for strangers -- and a
    limit of 0 asks Hunter for something it may reject. Neither is sent."""
    seen: list[httpx.Request] = []
    provider = HunterProvider("key", client=_client(_serving(_fixture("hunter_domain_search.json"), seen)))

    assert provider.search_people("acme.example", titles, limit) == []
    assert seen == []


def test_search_people_drops_an_unsafe_linkedin():
    payload = _fixture("hunter_domain_search.json")
    payload["data"]["emails"][0]["linkedin"] = "javascript:alert(1)"
    payload["data"]["emails"][1]["linkedin"] = "https://evil.example/in/maria"
    payload["data"]["emails"][2]["linkedin"] = "https://linkedin.com.evil.example/in/tom"
    assert [p.profile_url for p in _search(payload)] == [None, None, None]


def test_search_people_falls_back_to_linkedin_url():
    payload = _search_payload(
        {"value": "a@acme.example", "linkedin": None, "linkedin_url": "https://www.linkedin.com/in/ann-lee"},
        {"value": "b@acme.example", "linkedin": "bo-lee", "linkedin_url": "https://www.linkedin.com/in/other"})
    assert [p.profile_url for p in _search(payload)] == [
        "https://www.linkedin.com/in/ann-lee", "https://www.linkedin.com/in/bo-lee"]


def test_search_people_names_a_person_by_address_when_hunter_has_no_name():
    payload = _search_payload(
        {"value": "info@acme.example", "first_name": None, "last_name": None, "position": None},
        {"value": "cher@acme.example", "first_name": "Cher", "last_name": None, "position": "CTO"})
    nameless, mononym = _search(payload)
    assert (nameless.full_name, nameless.title) == ("info@acme.example", "")
    assert mononym.full_name == "Cher"


def test_search_people_skips_entries_without_an_address():
    payload = _search_payload(
        {"first_name": "No", "last_name": "Address", "position": "CTO"},
        {"value": "", "first_name": "Blank"},
        {"value": "ok@acme.example", "first_name": "Ok"})
    assert [p.email for p in _search(payload)] == ["ok@acme.example"]


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, {"data": {"emails": None}},
                                     {"data": {"emails": []}}, {"data": {"emails": ["junk", 3]}}])
def test_search_people_with_nothing_in_the_response_finds_nobody(payload):
    assert _search(payload) == []


def test_count_people_reads_personal_emails():
    seen: list[httpx.Request] = []
    provider = HunterProvider("key", client=_client(_serving(_fixture("hunter_email_count.json"), seen)))

    assert provider.count_people("acme.example") == 65
    assert seen[0].url.path == "/v2/email-count"
    assert dict(seen[0].url.params) == {"domain": "acme.example", "api_key": "key"}


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": None}, {"data": {"personal_emails": None}},
                                     {"data": {"personal_emails": "12"}}, {"data": {"personal_emails": True}},
                                     {"data": {"personal_emails": -3}}])
def test_count_people_is_zero_unless_hunter_gives_a_count(payload):
    assert HunterProvider("key", client=_client(_serving(payload))).count_people("acme.example") == 0


def _account(available, used) -> dict:
    return {"data": {"requests": {"credits": {"available": available, "used": used}}}}


def test_balance_reads_the_credit_counter_in_tenths():
    seen: list[httpx.Request] = []
    provider = HunterProvider("key", client=_client(_serving(_fixture("hunter_account.json"), seen)))

    assert provider.balance_tenths() == 94500  # 10000.0 available - 550.0 used = 9450 credits
    assert seen[0].url.path == "/v2/account"
    assert dict(seen[0].url.params) == {"api_key": "key"}


def test_balance_reads_fractional_credits():
    provider = HunterProvider("key", client=_client(_serving(_account(49.5, 0))))
    assert provider.balance_tenths() == 495


def test_balance_is_never_negative():
    assert HunterProvider("key", client=_client(_serving(_account(50, 52.5)))).balance_tenths() == 0


@pytest.mark.parametrize("payload", [
    {}, {"data": {}}, {"data": None}, {"data": {"requests": {}}}, {"data": {"requests": None}},
    # A plan without a unified credit bucket reports searches/verifications instead.
    {"data": {"requests": {"searches": {"available": 100, "used": 1},
                           "verifications": {"available": 100, "used": 1}}}},
    {"data": {"requests": {"credits": {}}}}, {"data": {"requests": {"credits": {"available": 10}}}},
    {"data": {"requests": {"credits": {"used": 10}}}}, _account("100", 0), _account(100, None),
    _account(True, 0), _account(100, 0.05), _account(-5, 0)],
    ids=["empty", "no-requests-key", "null-data", "no-credit-counter", "null-requests", "searches-only",
         "empty-credits", "no-used", "no-available", "text-available", "null-used", "bool-available",
         "finer-than-a-tenth", "negative"])
def test_balance_without_a_credit_counter_is_an_error(payload):
    """A run that cannot read its balance must buy nothing, so a missing or
    malformed counter is an error rather than a zero or a guess."""
    provider = HunterProvider("key", client=_client(_serving(payload)))
    with pytest.raises(HunterAPIError, match="Hunter account response has no credit balance"):
        provider.balance_tenths()
