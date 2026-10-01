from dataclasses import replace
from datetime import date

import pytest

from outreach.core.hnparse import (ats_from_links, company_domain, hint_signals,
                                   parse_post, post_links, post_text, validate_llm_post)
from outreach.sources.jobboards.region import matches_region
from outreach.types import ParsedPost, SourceClass


def test_pipe_format_with_ashby_link_parses():
    html = ('Acme Robotics | Senior Backend Engineer | San Francisco, CA | ONSITE | Full-time'
            '<p>We build warehouse robots. Apply: <a href="https:&#x2F;&#x2F;jobs.ashbyhq.com&#x2F;acme&#x2F;123">'
            'https://jobs.ashbyhq.com/acme/123</a> More at <a href="https://acmerobotics.com">acmerobotics.com</a>')
    p = parse_post(html)
    assert (p.company, p.domain, p.ats) == ("Acme Robotics", "acmerobotics.com", ("ashby", "acme"))
    assert (p.role, p.location, p.work_mode, p.employment_type) == (
        "Senior Backend Engineer", "San Francisco, CA", "onsite", "full_time")


@pytest.mark.parametrize("href,expected", [
    ("https://boards.greenhouse.io/zeta/jobs/1", ("greenhouse", "zeta")),
    ("https://job-boards.greenhouse.io/Zeta/jobs/1?gh_src=x", ("greenhouse", "zeta")),
    ("https://jobs.lever.co/findigs/abc", ("lever", "findigs")),
    ("https://jobs.ashbyhq.com/stream?utm_source=x", ("ashby", "stream")),
    ("https://www.oysterhr.com/careers?ashby_jid=51d5", None),
])
def test_ats_from_links(href, expected):
    assert ats_from_links([href]) == expected


def test_entity_encoded_links_decode():
    assert post_links('<a href="https:&#x2F;&#x2F;jobs.lever.co&#x2F;x">y</a>') == ["https://jobs.lever.co/x"]


def test_shortener_links_are_never_the_company_domain():
    assert company_domain(["https://bit.ly/abc", "https://lnkd.in/x"], "Acme | Engineer | Remote") is None


def test_non_company_hosts_are_skipped_for_the_domain():
    links = ["https://github.com/acme", "https://www.linkedin.com/company/acme", "https://acme.dev/jobs"]
    assert company_domain(links, "Acme | Engineer") == "acme.dev"


def test_bare_domain_in_the_first_line_counts():
    assert company_domain([], "Acme (acme.io) | Engineer | REMOTE (US)") == "acme.io"


def test_code_names_in_the_first_line_are_not_domains():
    assert company_domain([], "Acme | Node.js Engineer | Next.js | Remote") is None


def test_salary_segments_are_not_locations():
    p = parse_post('Acme | Backend Engineer | Remote (US) | $150k-$190k | Full-time<p><a href="https://acme.io">x</a>')
    assert p.location == "Remote (US)" and p.work_mode == "remote"


def test_a_bare_remote_tag_segment_sets_the_work_mode():
    p = parse_post('Acme | Backend Engineer | New York, NY | REMOTE<p><a href="https://acme.io">x</a>')
    assert (p.location, p.work_mode) == ("New York, NY", "remote")


def test_employment_tag_segment_is_read_as_declared():
    p = parse_post('Acme | Backend Engineer | Austin, TX | ONSITE | Contract<p><a href="https://acme.io">x</a>')
    assert p.employment_type == "other"


def test_company_parenthetical_domain_is_stripped():
    p = parse_post("Acme (acme.io) | Backend Engineer | Remote (US)")
    assert (p.company, p.domain) == ("Acme", "acme.io")


def test_email_only_post_without_a_domain_is_unparsed():
    assert parse_post("Acme | Engineer | NYC<p>Email jobs at acme dot com") is None


def test_free_text_post_without_pipes_is_unparsed():
    assert parse_post('We are hiring engineers in Berlin! <a href="https://acme.de">acme.de</a>') is None


def test_post_text_turns_paragraphs_into_lines():
    assert post_text("First | line<p>Second &amp; more<p>Third").split("\n") == [
        "First | line", "Second & more", "Third"]


@pytest.mark.parametrize("location", ["NYC", "New York", "SF Bay Area", "San Francisco", "Seattle", "US", "Remote, US"])
def test_common_us_cities_without_a_state_code_count_as_us(location):
    assert matches_region(location, "US")


def test_validator_rejects_invented_fields():
    html = 'Acme is hiring a Backend Engineer in Austin, TX, remote ok <a href="https://acme.io">site</a>'
    good = ParsedPost("Acme", "acme.io", None, "Backend Engineer", "Austin, TX", "remote", "unknown")
    assert validate_llm_post(good, html) == good
    for bad in (replace(good, domain="other.io"), replace(good, company="Globex"),
                replace(good, role="Staff ML Engineer"), replace(good, location="Denver, CO"),
                replace(good, work_mode="hybrid")):
        assert validate_llm_post(bad, html) is None


def test_validator_takes_the_board_from_the_links_not_the_model():
    html = 'Acme hiring Backend Engineer, remote <a href="https://jobs.lever.co/acme/1">apply</a> <a href="https://acme.io">x</a>'
    claimed = ParsedPost("Acme", "acme.io", ("ashby", "other"), "Backend Engineer", "", "remote", "unknown")
    assert validate_llm_post(claimed, html).ats == ("lever", "acme")


def test_validator_rejects_overlong_fields():
    html = "A" * 130 + ' Backend Engineer remote <a href="https://acme.io">x</a>'
    p = ParsedPost("A" * 130, "acme.io", None, "Backend Engineer", "", "remote", "unknown")
    assert validate_llm_post(p, html) is None


def test_hint_signals_find_round_and_headcount():
    hints = hint_signals("We raised a $30M Series B last spring. We're a team of 120 people.", date(2026, 9, 1))
    assert sorted(h.theme for h in hints) == ["funding-round", "headcount-statement"]
    assert all(h.source_class is SourceClass.HN_POST and h.published_at == date(2026, 9, 1) for h in hints)
