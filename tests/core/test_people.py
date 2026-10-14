from __future__ import annotations

import pytest

from outreach.config import DEFAULT_PEOPLE, PeopleList
from outreach.core.people import is_excluded, order_people, tier_of, title_matches
from outreach.types import FoundPerson

HIRING = DEFAULT_PEOPLE["hiring"]


def _p(title: str, email: str = "p@acme.example") -> FoundPerson:
    return FoundPerson("Pat Doe", title, email)


@pytest.mark.parametrize("title,wanted,expected", [
    ("Co-founder & CTO", "cto", True), ("Co-Founder", "founder", True), ("VP of Engineering", "vp engineering", True),
    ("Senior Engineering Manager", "engineering manager", True), ("Factory Manager", "cto", False),
    ("Senior Software Engineer", "engineering manager", False), ("Director", "cto", False)])
def test_title_matches(title, wanted, expected): assert title_matches(title, wanted) is expected


@pytest.mark.parametrize("title,wanted,expected", [
    # Whole words only: "cto" inside "Factory" or "Director" is not a CTO.
    ("Directory of Operations", "cto", False), ("Vice-President", "president", True),
    # Any order, and extra words in the title do not matter.
    ("Engineering, VP", "vp engineering", True), ("Engineering Intern", "engineering manager", False),
    # Case never matters, on either side.
    ("HEAD OF ENGINEERING", "Head Of Engineering", True),
    # Nothing is wanted, nothing matches: an empty phrase must not match everyone.
    ("CTO", "", False), ("CTO", " & ", False), ("", "cto", False)])
def test_title_matches_edges(title, wanted, expected): assert title_matches(title, wanted) is expected


def test_tier_of():
    assert [tier_of(t, HIRING) for t in ("CTO", "Head of Engineering", "Engineering Manager", "Designer")] == [
        (0, 2), (1, 2), (2, 0), None]


def test_tier_of_takes_the_first_tier_that_matches():
    # A "VP of Engineering & Co-founder" fits tier 0 (founder) and tier 1; tier 0 wins.
    assert tier_of("VP of Engineering & Co-founder", HIRING) == (0, 0)


def test_is_excluded():
    assert is_excluded("Technical Recruiter", HIRING) and is_excluded("Talent Partner", HIRING)
    assert not is_excluded("CTO", HIRING) and not is_excluded("Campus Recruiter", DEFAULT_PEOPLE["sponsors"])


def test_is_excluded_ignores_capitalisation_on_both_sides():
    """config.toml promises exclude_titles match in any capitalisation, but the
    config keeps them as typed: so a capitalised pattern must still match."""
    people = PeopleList("custom", (("cto",),), ("Recruiter", "TALENT"))
    assert is_excluded("technical recruiter", people)
    assert is_excluded("Senior Talent Partner", people)
    assert is_excluded("RECRUITING LEAD", PeopleList("custom", (("cto",),), ("recruit",)))
    assert not is_excluded("CTO", people)


def test_nothing_is_excluded_by_a_list_without_patterns():
    assert not is_excluded("Recruiter", PeopleList("custom", (("cto",),)))


def test_order_people_drops_excluded_and_sorts_by_tier():
    found = [_p("Engineering Manager"), _p("Technical Recruiter"), _p("CTO"), _p("Designer")]
    assert [p.title for p in order_people(found, HIRING)] == ["CTO", "Engineering Manager", "Designer"]


def test_order_people_is_stable_within_a_tier_and_among_the_unranked():
    found = [_p("Designer", "d1@acme.example"), _p("Tech Lead", "t1@acme.example"),
             _p("Accountant", "d2@acme.example"), _p("Tech Lead", "t2@acme.example")]
    assert [p.email for p in order_people(found, HIRING)] == [
        "t1@acme.example", "t2@acme.example", "d1@acme.example", "d2@acme.example"]


def test_order_people_does_not_touch_its_input():
    found = [_p("Designer"), _p("CTO")]
    order_people(found, HIRING)
    assert [p.title for p in found] == ["Designer", "CTO"]
