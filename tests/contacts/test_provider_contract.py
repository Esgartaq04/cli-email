"""Every ContactProvider implementation satisfies the same contract."""
import pytest

from outreach.contacts.fake import FakeContactProvider
from outreach.types import PersonRef


@pytest.fixture
def provider():
    return FakeContactProvider(
        people_by_domain={"acme.example": [
            PersonRef("A B", "CTO", None, "a@acme.example", "verified"),
        ]},
        credits=5,
    )


def test_find_returns_person_refs(provider):
    people = provider.find("acme.example", ["backend"])
    assert all(isinstance(p, PersonRef) for p in people)


def test_unknown_domain_returns_empty_list(provider):
    assert provider.find("nobody.example", ["backend"]) == []


def test_remaining_credits_is_a_non_negative_int(provider):
    assert isinstance(provider.remaining_credits(), int)
    assert provider.remaining_credits() >= 0


def test_verify_returns_a_known_status(provider):
    assert provider.verify("a@acme.example") in {"verified", "unverified", "not_found"}
