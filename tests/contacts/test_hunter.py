"""HunterProvider against recorded Hunter response shapes, via MockTransport.

`HunterProvider` is the layer closest to the data for the verified-email
rule: `find()` must never itself return "verified", and `verify()`'s status
mapping is the one place a single mis-mapped branch (e.g. `accept_all` ->
"verified") would ship guessed addresses as verified with nothing to catch
it. Injectable client, no live API touched -- same pattern as
`tests/test_fetcher.py`.
"""
from __future__ import annotations

import httpx
import pytest

from outreach.contacts.facts import HunterFactsProvider
from outreach.contacts.hunter import HunterAPIError, HunterProvider, _safe_linkedin_url


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _domain_search_response(emails):
    return httpx.Response(200, json={"data": {"domain": "acme.example", "emails": emails}})


def test_find_never_returns_verified_even_when_hunter_is_confident():
    """Domain search only reports Hunter's confidence, never a confirmed
    deliverability -- see the class docstring. Every person from `find()`
    must come back "unverified", full stop."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/domain-search"
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "Marisol", "last_name": "Okonkwo",
             "position": "CTO", "linkedin": "https://linkedin.com/in/marisol",
             "confidence": 97},
        ])

    provider = HunterProvider("key", client=_client(handler))
    people = provider.find("acme.example", ["backend"])
    assert len(people) == 1
    assert people[0].email_status == "unverified"
    assert people[0].email == "m@acme.example"
    assert people[0].full_name == "Marisol Okonkwo"


def test_find_skips_entries_with_no_email_value():
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([{"first_name": "No", "last_name": "Email"}])

    provider = HunterProvider("key", client=_client(handler))
    assert provider.find("acme.example", []) == []


def test_find_drops_a_non_http_profile_url():
    """`linkedin` reaches an `href` in the report -- a non-http scheme
    (e.g. `javascript:`) must never survive into a PersonRef."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin": "javascript:alert(1)"},
        ])

    provider = HunterProvider("key", client=_client(handler))
    people = provider.find("acme.example", [])
    assert people[0].profile_url is None


def test_find_keeps_a_real_https_profile_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin": "https://linkedin.com/in/m"},
        ])

    provider = HunterProvider("key", client=_client(handler))
    people = provider.find("acme.example", [])
    assert people[0].profile_url == "https://linkedin.com/in/m"


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


def test_remaining_credits_computes_available_minus_used():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/account"
        return httpx.Response(200, json={
            "data": {"requests": {"searches": {"available": 1000, "used": 240}}}})

    provider = HunterProvider("key", client=_client(handler))
    assert provider.remaining_credits() == 760


def test_remaining_credits_defaults_to_zero_on_a_malformed_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {}})

    provider = HunterProvider("key", client=_client(handler))
    assert provider.remaining_credits() == 0


def test_domain_search_reads_the_linkedin_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin": "https://www.linkedin.com/in/marisol"},
        ])

    people = HunterProvider("key", client=_client(handler)).find("acme.example", [])
    assert people[0].profile_url == "https://www.linkedin.com/in/marisol"


def test_domain_search_falls_back_to_linkedin_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin_url": "https://www.linkedin.com/in/marisol"},
        ])

    people = HunterProvider("key", client=_client(handler)).find("acme.example", [])
    assert people[0].profile_url == "https://www.linkedin.com/in/marisol"


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


def test_find_profile_uses_email_finder():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {
            "email": "m@acme.example", "linkedin_url": "https://www.linkedin.com/in/marisol"}})

    provider = HunterProvider("key", client=_client(handler))
    assert provider.find_profile("acme.com", "Marisol", "Okonkwo") == (
        "https://www.linkedin.com/in/marisol")
    assert seen[0].url.path == "/v2/email-finder"
    assert dict(seen[0].url.params) == {
        "domain": "acme.com", "first_name": "Marisol", "last_name": "Okonkwo", "api_key": "key"}


def test_find_profile_is_none_when_hunter_has_no_match():
    provider = HunterProvider(
        "key", client=_client(lambda request: httpx.Response(404, json={"errors": []})))
    assert provider.find_profile("acme.com", "No", "Body") is None


def test_find_profile_is_none_without_a_usable_linkedin_url():
    provider = HunterProvider("key", client=_client(
        lambda request: httpx.Response(200, json={"data": {"linkedin_url": None}})))
    assert provider.find_profile("acme.com", "No", "Body") is None


def test_find_profile_propagates_other_http_errors():
    provider = HunterProvider("key", client=_client(lambda request: httpx.Response(429)))
    with pytest.raises(HunterAPIError, match="email-finder returned HTTP 429"):
        provider.find_profile("acme.com", "M", "O")


SECRET = "sk-hunter-SECRET-key"


@pytest.mark.parametrize("call", [
    lambda c: HunterProvider(SECRET, client=c).find("acme.com", []),
    lambda c: HunterProvider(SECRET, client=c).verify("m@acme.com"),
    lambda c: HunterProvider(SECRET, client=c).remaining_credits(),
    lambda c: HunterProvider(SECRET, client=c).find_profile("acme.com", "M", "O"),
    lambda c: HunterFactsProvider(SECRET, client=c).company_facts("acme.com"),
], ids=["find", "verify", "credits", "find_profile", "company_facts"])
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
