from __future__ import annotations

import pytest

from outreach.core.workmode import (
    classify_employment,
    classify_work_mode,
    parse_work_modes,
    posting_matches,
)
from outreach.types import ALL_WORK_MODES, PostingRef


def ref(**kw) -> PostingRef:
    return PostingRef(
        company_name="Acme",
        company_domain="acme.com",
        title="Engineer",
        url="https://acme.com/jobs/1",
        location="",
        **kw,
    )


@pytest.mark.parametrize("location,title,content,expected", [
    ("Remote - US", "Backend Engineer", "", "remote"),
    ("San Francisco, CA (Hybrid)", "Backend Engineer", "", "hybrid"),
    ("Remote or Hybrid - NYC", "Engineer", "", "hybrid"),
    ("New York, NY", "Engineer", "", "unknown"),
    ("New York, NY", "Engineer", "<p>This role is on-site in our NYC office.</p>", "onsite"),
    ("New York, NY", "Engineer", "We work 3 days a week in the office.", "hybrid"),
    ("Chicago, IL", "Engineer", "Our remote monitoring product", "unknown"),  # bare 'remote' in content is noise
    ("", "Staff Engineer (Remote)", "", "remote"),
    # Remaining location/title vocabulary.
    ("Anywhere (WFH)", "Engineer", "", "remote"),
    ("Work from home", "Engineer", "", "remote"),
    ("Distributed", "Engineer", "", "remote"),
    ("Austin, TX (On-site)", "Engineer", "", "onsite"),
    ("Austin, TX", "Engineer (Onsite)", "", "onsite"),
    ("Austin, TX - in office", "Engineer", "", "onsite"),
    ("", "Engineer", "", "unknown"),
    # 'Distributed' as a technical field is not a work mode.
    ("New York, NY", "Distributed Systems Engineer", "", "unknown"),
    # Content-only signals, stricter patterns.
    ("Denver, CO", "Engineer", "We are a fully remote company.", "remote"),
    ("Denver, CO", "Engineer", "100% remote, async-first.", "remote"),
    ("Denver, CO", "Engineer", "We are remote-first.", "remote"),
    ("Denver, CO", "Engineer", "This is a hybrid role.", "hybrid"),
    ("Denver, CO", "Engineer", "Expect to be in office 5 days a week.", "onsite"),
    # Hybrid means some days in the office, not all of them: five is onsite.
    ("Denver, CO", "Engineer", "We work 5 days a week in the office.", "onsite"),
    ("Denver, CO", "Engineer", "You'll be 5 days per week in our office.", "onsite"),
    ("Denver, CO", "Engineer", "We work 1 day a week in the office.", "hybrid"),
    ("Denver, CO", "Engineer", "We work 4 days per week in our office.", "hybrid"),
    ("Denver, CO", "Engineer", "Up to 15 days a week in the office.", "unknown"),
    # Hybrid beats remote beats onsite, in content as in location.
    ("Denver, CO", "Engineer", "Fully remote, or hybrid if you prefer.", "hybrid"),
    # Location/title conclusive => content is not consulted.
    ("Remote", "Engineer", "This role is on-site.", "remote"),
    # Tags are stripped to whitespace, so adjacent elements don't glue together.
    ("Denver, CO", "Engineer", "<li>Fully</li><li>remote</li>", "remote"),
])
def test_classify_work_mode(location, title, content, expected):
    assert classify_work_mode(location, title, content) == expected


@pytest.mark.parametrize("title,content,declared,expected", [
    ("Engineer", "", "FullTime", "full_time"),
    ("Engineer", "", "Full-time", "full_time"),
    ("Engineer", "", "Contract", "other"),
    ("Software Engineering Intern", "", None, "other"),
    ("Engineer (Contract)", "", None, "other"),
    ("Engineer", "This is a full-time position.", None, "full_time"),
    ("Engineer", "", None, "unknown"),
    # Declared vocabulary (Ashby / Lever), case and separators ignored.
    ("Engineer", "", "FULL_TIME", "full_time"),
    ("Engineer", "", "Part time", "other"),
    ("Engineer", "", "PartTime", "other"),
    ("Engineer", "", "Internship", "other"),
    ("Engineer", "", "Contractor", "other"),
    ("Engineer", "", "Temporary", "other"),
    ("Engineer", "", "temp", "other"),
    # Declared value wins over title text; unrecognised values fall through.
    ("Engineer (Contract)", "", "FullTime", "full_time"),
    ("Engineer", "", "Seasonal", "unknown"),
    ("Engineer", "This is a full-time position.", "Seasonal", "full_time"),
    # Title hints, with word boundaries.
    ("Part-Time Designer", "", None, "other"),
    ("Internal Tools Engineer", "", None, "unknown"),
    ("Contracts Manager", "", None, "unknown"),
    # Non-full-time in the title beats a boilerplate 'full-time' in content.
    ("Engineering Intern", "Full-time hours during the summer.", None, "other"),
    # "Contract" / "Temp" only as a qualifier on the role, never as part of
    # what the role works on.
    ("Smart Contract Engineer", "", None, "unknown"),
    ("Contract Lifecycle Engineer", "", None, "unknown"),
    ("Senior Engineer - Smart Contract", "", None, "unknown"),
    ("Template Engineer", "", None, "unknown"),
    ("Temperature Controls Engineer", "", None, "unknown"),
    ("Backend Engineer (Contract)", "", None, "other"),
    ("Backend Engineer (Contractor)", "", None, "other"),
    ("Data Engineer - Contract", "", None, "other"),
    ("Data Engineer - Contract - Remote", "", None, "other"),
    ("Engineer, Contractor", "", None, "other"),
    ("Backend Engineer Contract", "", None, "other"),
    ("Engineer (Contract-to-hire)", "", None, "other"),
    ("Contract to Hire Backend Engineer", "", None, "other"),
    ("Temporary Data Analyst", "", None, "other"),
    ("Data Analyst (Temp)", "", None, "other"),
    ("Data Analyst - Temp", "", None, "other"),
    # 'contract' in content is noise (customer contracts, etc.).
    ("Engineer", "We build contract-management software.", None, "unknown"),
])
def test_classify_employment(title, content, declared, expected):
    assert classify_employment(title, content, declared) == expected


def test_parse_work_modes():
    assert parse_work_modes(None) == ALL_WORK_MODES
    assert parse_work_modes("remote") == frozenset({"remote"})
    assert parse_work_modes("hybrid, on-site") == frozenset({"hybrid", "onsite"})
    with pytest.raises(ValueError, match="sometimes"):
        parse_work_modes("remote,sometimes")


@pytest.mark.parametrize("flag", ["", "  ", " , "])
def test_parse_work_modes_blank_means_all(flag):
    assert parse_work_modes(flag) == ALL_WORK_MODES


def test_parse_work_modes_is_case_and_separator_tolerant():
    assert parse_work_modes("Remote, ON_SITE,Hybrid") == ALL_WORK_MODES


def test_parse_work_modes_rejects_unknown_as_a_filter_value():
    # "unknown" is a classification result, not something a user filters for.
    with pytest.raises(ValueError, match="unknown"):
        parse_work_modes("unknown")


def test_posting_matches_keeps_unknown_mode_and_drops_non_full_time():
    wanted = frozenset({"remote"})
    assert posting_matches(ref(work_mode="remote", employment_type="full_time"), wanted)
    assert posting_matches(ref(work_mode="unknown", employment_type="unknown"), wanted)
    assert not posting_matches(ref(work_mode="onsite", employment_type="full_time"), wanted)
    assert not posting_matches(ref(work_mode="remote", employment_type="other"), wanted)
