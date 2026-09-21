import random

from outreach.config import RankingConfig
from outreach.core.ranking import score_title, rank_contacts
from outreach.types import PersonRef

CFG = RankingConfig(
    max_contacts_per_company=3,
    exclude_title_patterns=("recruit", "talent", "sourcer"),
)


def person(name: str, title: str) -> PersonRef:
    return PersonRef(full_name=name, title=title, profile_url=None,
                     email=None, email_status="not_found")


def test_recruiters_are_excluded():
    assert score_title("Technical Recruiter", 50, ["backend"], CFG) is None
    assert score_title("Head of Talent", 50, ["backend"], CFG) is None


def test_founder_at_small_company_outranks_founder_at_large_one():
    small = score_title("Co-founder & CTO", 48, ["backend"], CFG)
    large = score_title("Co-founder & CTO", 900, ["backend"], CFG)
    assert small.tier == 1 and large.tier == 1
    assert small.score > large.score


def test_tier_one_outranks_tier_three_at_same_size():
    vp = score_title("VP Engineering", 200, ["backend"], CFG)
    lead = score_title("Engineering Manager", 200, ["backend"], CFG)
    assert vp.score > lead.score


def test_role_relevance_breaks_ties_within_a_tier():
    relevant = score_title("Engineering Manager, Backend", 200, ["backend"], CFG)
    other = score_title("Engineering Manager, Design Systems", 200, ["backend"], CFG)
    assert relevant.score > other.score


def test_unknown_headcount_uses_neutral_factor_and_does_not_raise():
    s = score_title("Co-founder & CTO", None, ["backend"], CFG)
    assert s is not None
    assert s.tier == 1
    assert s.score > 0


def test_rank_contacts_sorts_and_truncates_to_cap():
    people = [
        person("A", "Staff Engineer"),
        person("B", "Co-founder & CTO"),
        person("C", "Technical Recruiter"),
        person("D", "VP Engineering"),
        person("E", "Engineering Manager"),
    ]
    ranked = rank_contacts(people, 60, ["backend"], CFG)
    assert len(ranked) == 3
    assert [p.full_name for p, _ in ranked] == ["B", "D", "E"]
    assert all(p.full_name != "C" for p, _ in ranked)


def test_explanation_mentions_tier_and_headcount():
    s = score_title("Head of Engineering", 75, ["backend"], CFG)
    assert "tier 1" in s.explanation
    assert "75" in s.explanation


def test_director_title_is_tier_two_not_tier_one():
    # "director" contains the substring "cto" (di-REC-TO-r); word-boundary
    # matching must not let that fall through to tier 1.
    director = score_title("Director of Engineering", 200, ["backend"], CFG)
    vp = score_title("VP Engineering", 200, ["backend"], CFG)
    assert director.tier == 2
    assert vp.tier == 1
    assert director.score < vp.score


def test_rank_contacts_is_stable_regardless_of_input_order():
    people = [
        person("A", "Staff Engineer"),
        person("B", "Co-founder & CTO"),
        person("C", "Technical Recruiter"),
        person("D", "VP Engineering"),
        person("E", "Engineering Manager"),
    ]
    expected = [p.full_name for p, _ in rank_contacts(people, 60, ["backend"], CFG)]

    shuffled = list(people)
    random.Random(0).shuffle(shuffled)
    actual = [p.full_name for p, _ in rank_contacts(shuffled, 60, ["backend"], CFG)]

    assert actual == expected == ["B", "D", "E"]
