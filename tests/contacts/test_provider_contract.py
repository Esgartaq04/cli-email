"""Every ContactProvider implementation satisfies the same contract."""
import httpx
import pytest

from outreach.contacts.fake import FakeContactProvider
from outreach.contacts.hunter import HunterProvider
from outreach.types import PersonRef


def _hunter() -> HunterProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/domain-search"):
            known = request.url.params["domain"] == "acme.example"
            return httpx.Response(200, json={"data": {"emails": [
                {"value": "a@acme.example", "first_name": "A", "last_name": "B",
                 "position": "CTO", "linkedin": "https://www.linkedin.com/in/ab"}
            ] if known else []}})
        if path.endswith("/email-verifier"):
            return httpx.Response(200, json={"data": {"status": "valid"}})
        if path.endswith("/email-finder"):
            return httpx.Response(200, json={"data": {
                "linkedin_url": "https://www.linkedin.com/in/ab"}})
        return httpx.Response(200, json={"data": {
            "requests": {"searches": {"available": 5, "used": 0}}}})

    return HunterProvider("key", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(params=["fake", "hunter"])
def provider(request):
    if request.param == "hunter":
        return _hunter()
    return FakeContactProvider(
        people_by_domain={"acme.example": [
            PersonRef("A B", "CTO", None, "a@acme.example", "verified"),
        ]},
        credits=5,
        profiles={"A B": "https://www.linkedin.com/in/ab"},
    )


def test_find_returns_person_refs(provider):
    people = provider.find("acme.example", ["backend"])
    assert all(isinstance(p, PersonRef) for p in people)


def test_find_profile_returns_a_url_or_none(provider):
    profile = provider.find_profile("acme.example", "A", "B")
    assert profile is None or (isinstance(profile, str) and profile.startswith("https://"))


def test_unknown_domain_returns_empty_list(provider):
    assert provider.find("nobody.example", ["backend"]) == []


def test_remaining_credits_is_a_non_negative_int(provider):
    assert isinstance(provider.remaining_credits(), int)
    assert provider.remaining_credits() >= 0


def test_verify_returns_a_known_status(provider):
    assert provider.verify("a@acme.example") in {"verified", "unverified", "not_found"}
