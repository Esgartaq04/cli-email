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

from outreach.contacts.hunter import HunterProvider


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
             "position": "CTO", "linkedin_url": "https://linkedin.com/in/marisol",
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
    """`linkedin_url` reaches an `href` in the report -- a non-http scheme
    (e.g. `javascript:`) must never survive into a PersonRef."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin_url": "javascript:alert(1)"},
        ])

    provider = HunterProvider("key", client=_client(handler))
    people = provider.find("acme.example", [])
    assert people[0].profile_url is None


def test_find_keeps_a_real_https_profile_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return _domain_search_response([
            {"value": "m@acme.example", "first_name": "M", "last_name": "O",
             "linkedin_url": "https://linkedin.com/in/m"},
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
