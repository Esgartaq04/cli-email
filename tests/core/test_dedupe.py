import pytest
from outreach.core.dedupe import canonical_domain, dedupe_postings
from outreach.types import PostingRef


@pytest.mark.parametrize("raw", [
    "https://WWW.Foo.com/careers/",
    "http://foo.com",
    "foo.com",
    "  FOO.com  ",
    "https://foo.com:443/jobs?x=1",
    "//www.foo.com/",
])
def test_domain_variants_collapse_to_one_value(raw):
    assert canonical_domain(raw) == "foo.com"


def test_subdomains_are_preserved_when_not_www():
    assert canonical_domain("https://jobs.foo.com/x") == "jobs.foo.com"


def test_empty_value_raises():
    with pytest.raises(ValueError):
        canonical_domain("   ")


def test_dedupe_groups_postings_by_canonical_domain():
    postings = [
        PostingRef("Foo", "https://WWW.Foo.com/", "Backend Engineer", "u1", "Chicago"),
        PostingRef("Foo Inc", "foo.com", "Platform Engineer", "u2", "Remote"),
        PostingRef("Bar", "bar.example", "Backend Engineer", "u3", "NYC"),
    ]
    grouped = dedupe_postings(postings)
    assert set(grouped) == {"foo.com", "bar.example"}
    assert len(grouped["foo.com"]) == 2
