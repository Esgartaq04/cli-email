"""The contact report: which section a company lands in, what a card shows, and the page itself."""
import re
from datetime import date, datetime

import pytest

from outreach.core.filters import describe_filters
from outreach.pipeline.summary import RunSummary
from outreach.render.contact_report import (CompanyCard, ContactReportView, PersonLine, PostingLine,
                                            build_contact_view, render_contact_report,
                                            write_contact_report)
from outreach.types import (ALL_WORK_MODES, CompanyInfo, HNPostRecord, Location, PersonRef,
                            PostingRef, RunFilters, Spend)
from tests.pipeline.factories import build_context, start_run

WHEN = datetime(2026, 9, 21, 9)
BO = PersonRef("Bo Diaz", "CTO", "https://www.linkedin.com/in/bodiaz", "bo@a.example", "verified")
CY = PersonRef("Cy Park", "VP Engineering", None, None, "unverified")


def _discover(ctx, run_id, domain, status="ok", *, source="hunter", sector="Software Development",
              size="51-200", fit="fits", position=None):
    """A company the run recorded, named after its domain ("a.example" is "A")."""
    cid = ctx.companies.upsert(domain, domain.split(".")[0].title(), None, None)
    if position is None:
        position = len(ctx.company_info.for_run(run_id))
    ctx.company_info.insert(run_id, cid, CompanyInfo(position, source, sector, size, fit))
    ctx.runs.set_stage(run_id, cid, "discover", status)
    return cid


def _bought(ctx, run_id, domain, people, **kwargs):
    cid = _discover(ctx, run_id, domain, **kwargs)
    for position, person in enumerate(people):
        contact_id = ctx.contacts.upsert(cid, person, "hunter", WHEN)
        ctx.run_contacts.add(run_id, cid, contact_id, position)
    ctx.runs.set_stage(run_id, cid, "contacts", "bought")
    return cid


def _stopped(ctx, run_id, domain, contacts_status, error=None, **kwargs):
    cid = _discover(ctx, run_id, domain, **kwargs)
    ctx.runs.set_stage(run_id, cid, "contacts", contacts_status, error=error)
    return cid


def _reasons(entries):
    return {entry.name: entry.reason for entry in entries}


# --- sections ---------------------------------------------------------------------

def test_sections_follow_the_contacts_status():
    ctx = build_context()
    earlier = start_run(ctx)
    _bought(ctx, earlier, "c.example", [BO])
    run_id = start_run(ctx, people="hiring")
    _bought(ctx, run_id, "a.example", [BO])
    _stopped(ctx, run_id, "b.example", "empty")
    _stopped(ctx, run_id, "c.example", "skipped_earlier_list")
    _stopped(ctx, run_id, "d.example", "over_cap")
    _stopped(ctx, run_id, "e.example", "no_matching_people")

    view = build_contact_view(ctx, run_id)

    assert [card.name for card in view.with_contacts] == ["A"]
    assert _reasons(view.no_one_found) == {
        "B": 'Hunter has no one matching "hiring"',
        "E": 'Hunter has no one matching "hiring" — cost nothing'}
    assert _reasons(view.earlier) == {"C": "on run 1, 21 Sep 2026 — not bought again"}
    assert _reasons(view.left_out) == {"D": "over this run's cap of 10 companies"}
    assert [entry.domain for entry in view.left_out] == ["d.example"]


@pytest.mark.parametrize("discover, contacts, error, reason", [
    ("ok", "out_of_credits", None, "not bought — out of credits"),
    ("ok", "domain_unconfirmed", None,
     "its website didn't answer, so its domain couldn't be confirmed"),
    ("ok", "failed", "Hunter /account returned HTTP 401", "failed — Hunter /account returned HTTP 401"),
    ("ok", "failed", None, "failed"),
    ("left_out_filters", None, None, "doesn't match your sector or size"),
    ("excluded_no_matching_posting", None, None,
     "no full-time posting in the requested work mode or location"),
    ("skipped_cap", None, None, "over this run's HN cap of 15"),
    ("ok", None, None, "not reached"),
])
def test_every_left_out_status_says_why(discover, contacts, error, reason):
    ctx = build_context(); run_id = start_run(ctx)
    cid = _discover(ctx, run_id, "z.example", discover)
    if contacts:
        ctx.runs.set_stage(run_id, cid, "contacts", contacts, error=error)

    view = build_contact_view(ctx, run_id)

    assert _reasons(view.left_out) == {"Z": reason}
    assert not (view.with_contacts or view.no_one_found or view.earlier)


def test_a_company_you_emailed_is_listed_with_the_address_and_date():
    ctx = build_context(); run_id = start_run(ctx)
    _stopped(ctx, run_id, "d.example", "skipped_emailed")
    ctx.emailed.add(["dana@d.example"], date(2026, 9, 12), WHEN)

    view = build_contact_view(ctx, run_id)

    assert _reasons(view.earlier) == {"D": "you emailed dana@d.example on 12 Sep 2026"}


def test_a_skip_whose_reason_has_since_gone_still_lists_the_company():
    ctx = build_context()
    run_id = start_run(ctx)
    _stopped(ctx, run_id, "d.example", "skipped_emailed")  # the mark was removed afterwards
    _stopped(ctx, run_id, "e.example", "skipped_earlier_list")

    view = build_contact_view(ctx, run_id)

    assert _reasons(view.earlier) == {"D": "you've emailed someone here",
                                      "E": "already on an earlier list — not bought again"}


def test_companies_follow_the_position_the_run_gave_them():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "late.example", [BO], position=2)
    _bought(ctx, run_id, "first.example", [BO], position=0)
    _bought(ctx, run_id, "middle.example", [BO], position=1)
    _stopped(ctx, run_id, "gone.example", "over_cap", position=4)
    _stopped(ctx, run_id, "away.example", "over_cap", position=3)

    view = build_contact_view(ctx, run_id)

    assert [card.name for card in view.with_contacts] == ["First", "Middle", "Late"]
    assert [entry.name for entry in view.left_out] == ["Away", "Gone"]


def test_empty_sections_say_none_and_every_section_has_its_id():
    ctx = build_context(); run_id = start_run(ctx)
    html = render_contact_report(build_contact_view(ctx, run_id))
    for section in ("with-contacts", "no-one-found", "earlier", "left-out", "run-details"):
        assert f'id="{section}"' in html
    assert html.count("None.") >= 4


# --- cards ------------------------------------------------------------------------

def test_only_confirmed_addresses_are_shown():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO, CY])

    [card] = build_contact_view(ctx, run_id).with_contacts
    html = render_contact_report(build_contact_view(ctx, run_id))

    assert [(p.full_name, p.email) for p in card.people] == [("Bo Diaz", "bo@a.example"),
                                                             ("Cy Park", None)]
    assert 'href="mailto:bo@a.example"' in html and 'data-copy="bo@a.example"' in html
    assert ('<a href="mailto:bo@a.example">bo@a.example</a> '
            '<button class="copy" data-copy="bo@a.example">Copy</button>') in html
    assert html.count("mailto:") == 1
    assert "address not confirmed" in html


def test_an_address_that_is_not_confirmed_is_never_in_the_page_even_if_one_is_stored():
    ctx = build_context(); run_id = start_run(ctx)
    cid = _bought(ctx, run_id, "a.example", [CY])
    ctx.contacts.conn.execute("UPDATE contacts SET email = 'cy@a.example' WHERE company_id = ?", (cid,))

    [card] = build_contact_view(ctx, run_id).with_contacts
    html = render_contact_report(build_contact_view(ctx, run_id))

    assert card.people[0].email is None
    assert "cy@a.example" not in html and "mailto" not in html
    assert "address not confirmed" in html


def test_emailed_people_carry_the_date():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO, CY])
    ctx.emailed.add(["bo@a.example"], date(2026, 9, 12), WHEN)

    [card] = build_contact_view(ctx, run_id).with_contacts

    assert [p.emailed_on for p in card.people] == [date(2026, 9, 12), None]
    assert "emailed 12 Sep" in render_contact_report(build_contact_view(ctx, run_id))


def test_the_emailed_list_is_read_in_the_form_it_is_stored():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [PersonRef("Bo Diaz", "CTO", None, "Bo@A.example", "verified")])
    ctx.emailed.add(["bo@a.example"], date(2026, 9, 12), WHEN)

    [card] = build_contact_view(ctx, run_id).with_contacts

    assert card.people[0].emailed_on == date(2026, 9, 12)


def test_linkedin_is_the_profile_when_known_and_a_search_when_not():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO, CY])

    [card] = build_contact_view(ctx, run_id).with_contacts
    html = render_contact_report(build_contact_view(ctx, run_id))

    bo, cy = card.people
    assert (bo.profile_url, bo.linkedin_search) == ("https://www.linkedin.com/in/bodiaz", None)
    assert cy.profile_url is None and cy.linkedin_search.endswith("keywords=Cy+Park+A")
    assert '<a class="p-li" href="https://www.linkedin.com/in/bodiaz">LinkedIn</a>' in html
    assert ">Search LinkedIn</a>" in html and html.count(">LinkedIn</a>") == 1


def test_a_profile_link_that_is_not_http_becomes_a_search():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example",
            [PersonRef("Bo Diaz", "CTO", "javascript:alert(1)", "bo@a.example", "verified")])

    html = render_contact_report(build_contact_view(ctx, run_id))

    assert "javascript" not in html
    assert ">Search LinkedIn</a>" in html and ">LinkedIn</a>" not in html


def test_a_card_says_what_the_company_is_and_where():
    ctx = build_context(); run_id = ctx.runs.start(
        "companies", "", RunFilters(location=Location("US", "TX")), "sponsors", WHEN)
    _bought(ctx, run_id, "a.example", [BO], sector="Banking", size="11-50")
    _bought(ctx, run_id, "b.example", [BO], source="board", fit="unknown", sector=None, size=None)

    view = build_contact_view(ctx, run_id)
    html = render_contact_report(view)

    a, b = view.with_contacts
    assert (a.sector, a.size, a.hq, a.fit_unknown) == ("Banking", "11-50", "Texas", False)
    assert (b.sector, b.size, b.hq, b.fit_unknown) == (None, None, None, True)
    assert "Banking" in html and "11-50" in html and "HQ Texas" in html
    assert html.count("sector unknown") == 1


def test_an_unknown_fit_says_sector_unknown_whatever_else_is_known():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO], sector="Banking", size=None, fit="unknown")
    _bought(ctx, run_id, "b.example", [BO], sector="Banking", size="11-50", fit="fits")

    view = build_contact_view(ctx, run_id)
    html = render_contact_report(view)

    a, b = view.with_contacts
    assert (a.sector, a.size, a.fit_unknown) == ("Banking", None, True)
    assert (b.sector, b.size, b.fit_unknown) == ("Banking", "11-50", False)
    assert html.count("sector unknown") == 1  # only the card with the unknown fit
    assert html.count("Banking") == 2 and "11-50 employees" in html


def test_a_company_with_nobody_left_after_the_exclusions_does_not_claim_it_cost_nothing():
    ctx = build_context(); run_id = start_run(ctx)
    _stopped(ctx, run_id, "b.example", "empty")  # paid for, then every title was excluded
    _stopped(ctx, run_id, "e.example", "no_matching_people")  # the free count was zero

    html = render_contact_report(build_contact_view(ctx, run_id))

    assert html.count("cost nothing") == 1
    assert html.count('has no one matching &#34;hiring&#34;') == 2


def test_a_hunter_hiring_company_says_so_without_a_link():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    _bought(ctx, run_id, "a.example", [BO], source="hunter_hiring")

    [card] = build_contact_view(ctx, run_id).with_contacts

    assert card.hiring_per_hunter == "Backend Engineer" and card.postings == []
    assert card.hq == "United States"
    assert "Hiring Backend Engineer, per Hunter · no posting link" in render_contact_report(
        build_contact_view(ctx, run_id))


def test_an_hn_company_links_its_post():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    cid = _bought(ctx, run_id, "a.example", [BO], source="hn")
    post_url = "https://news.ycombinator.com/item?id=302"
    ctx.postings.insert(run_id, cid, PostingRef("A", "a.example", "Backend Engineer", post_url,
                                                "Austin, TX", "onsite"))
    ctx.hn_posts.insert(run_id, cid, HNPostRecord(
        302, "Ask HN: Who is hiring? (September 2026)", date(2026, 9, 1), "parsed", "kept"))

    [card] = build_contact_view(ctx, run_id).with_contacts
    html = render_contact_report(build_contact_view(ctx, run_id))

    [posting] = card.postings
    assert (posting.title, posting.url, posting.work_mode, posting.via) == (
        "Backend Engineer", post_url, "onsite", "via HN Who is hiring? (September 2026)")
    assert f'<a href="{post_url}">Backend Engineer</a>' in html
    assert "via HN Who is hiring? (September 2026)" in html
    assert card.hiring_per_hunter is None and card.hq is None


def test_a_board_posting_says_it_came_from_a_job_board():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    cid = _bought(ctx, run_id, "a.example", [BO], source="board")
    ctx.postings.insert(run_id, cid, PostingRef("A", "a.example", "Backend Engineer",
                                                "https://boards.example/a/1", "Remote", "remote"))

    [card] = build_contact_view(ctx, run_id).with_contacts

    assert [p.via for p in card.postings] == ["via job board"]


def test_non_http_links_render_as_text():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    cid = _bought(ctx, run_id, "a.example", [BO], source="board")
    ctx.postings.insert(run_id, cid, PostingRef("A", "a.example", "Backend Engineer",
                                                "javascript:alert(1)", "Remote", "remote"))

    html = render_contact_report(build_contact_view(ctx, run_id))

    assert "Backend Engineer" in html
    assert not re.search(r'href="javascript', html, re.IGNORECASE)
    assert "javascript:alert(1)" not in html


def test_the_template_itself_refuses_a_link_that_is_not_http():
    card = CompanyCard(
        "A", "a.example", None, None, None, False,
        [PostingLine("Backend Engineer", "javascript:alert(1)", "remote", "via job board")], None,
        [PersonLine("Bo Diaz", "CTO", "bo@a.example", "javascript:alert(2)", "javascript:alert(3)", None)])
    view = ContactReportView(1, "companies", "Company run", "any sector", "sponsors",
                             date(2026, 9, 21), "not recorded", [card], [], [], [], {}, [])

    html = render_contact_report(view)

    assert "javascript" not in html and "Backend Engineer" in html and "Bo Diaz" in html


def test_what_the_data_says_is_escaped():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example",
            [PersonRef("<i>Bo</i>", "<b>CTO</b>", None, "bo@a.example", "verified")])
    ctx.companies.upsert("a.example", "<script>alert(1)</script>", None, None)
    _stopped(ctx, run_id, "z.example", "failed", error='<img src=x onerror="boom">')

    html = render_contact_report(build_contact_view(ctx, run_id))

    assert "<script>alert" not in html and "<i>Bo" not in html and "<b>CTO" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&lt;i&gt;Bo&lt;/i&gt;" in html
    assert "&lt;img src=x onerror=&#34;boom&#34;&gt;" in html


def test_the_page_has_one_script_that_copies():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO])

    html = render_contact_report(build_contact_view(ctx, run_id))

    assert html.count("<script>") == 1 and "navigator.clipboard.writeText" in html
    assert not re.search(r"<script[^>]+src=", html)


# --- header and details -----------------------------------------------------------

def test_the_header_names_the_run():
    ctx = build_context()
    jobs = ctx.runs.start("jobs", "Backend Engineer",
                          RunFilters(("Software Development",), ("51-200",), Location("US", "TX")),
                          "hiring", WHEN)
    companies = ctx.runs.start("companies", "", RunFilters(), "sponsors", datetime(2026, 9, 22, 8))

    job_view = build_contact_view(ctx, jobs)
    company_view = build_contact_view(ctx, companies)

    assert (job_view.run_id, job_view.kind, job_view.heading, job_view.people, job_view.run_date) == (
        jobs, "jobs", "Backend Engineer · job run", "hiring", date(2026, 9, 21))
    assert job_view.filters_line == "Software Development · 51-200 · Texas"
    assert (company_view.kind, company_view.heading, company_view.people, company_view.run_date) == (
        "companies", "Company run", "sponsors", date(2026, 9, 22))
    assert company_view.filters_line == "any sector · any size · United States"
    html = render_contact_report(job_view)
    assert "Backend Engineer · job run" in html and "Software Development · 51-200 · Texas" in html


@pytest.mark.parametrize("spend, line", [
    (Spend(500, 3, 0, 30), "3 credits used · 47 left"),
    (Spend(500, 3, 4, 50), "5 credits used · 45 left"),
    (Spend(None, 1, 0, 15), "1.5 credits used"),
    (None, "not recorded"),
])
def test_the_credits_line(spend, line):
    ctx = build_context(); run_id = start_run(ctx)
    if spend is not None:
        ctx.runs.record_spend(run_id, spend)

    view = build_contact_view(ctx, run_id)

    assert view.credits_line == line and view.details["Credits"] == line
    assert line in render_contact_report(view)


def test_a_company_run_counts_what_it_found_and_what_it_spent():
    ctx = build_context(); run_id = start_run(ctx)
    _bought(ctx, run_id, "a.example", [BO])
    _stopped(ctx, run_id, "b.example", "empty")
    _discover(ctx, run_id, "c.example", "left_out_filters")  # left out, so not found
    ctx.runs.record_spend(run_id, Spend(500, 3, 1, 35))

    view = build_contact_view(ctx, run_id)

    assert view.details == {"Companies found": "2 Hunter", "Emails revealed": "3",
                            "Addresses checked": "1", "Credits": "3.5 credits used · 46.5 left"}
    assert view.errors == ["not recorded"]


def test_a_jobs_run_counts_each_source_and_reads_the_hn_funnel_from_its_summary():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    _bought(ctx, run_id, "a.example", [BO], source="board")
    _bought(ctx, run_id, "b.example", [BO], source="hn")
    _bought(ctx, run_id, "c.example", [BO], source="hn")
    _stopped(ctx, run_id, "d.example", "empty", source="hunter_hiring")
    summary = RunSummary(
        run_id, errors=["hn: algolia down"], hn_thread_title="Ask HN: Who is hiring? (September 2026)",
        hn_posts_read=11, hn_role_matched=8, hn_parsed=5, hn_llm_parsed=2, hn_llm_rejected=1,
        hn_filtered=3, hn_kept=4, hn_skipped_cap=2)

    view = build_contact_view(ctx, run_id, summary)

    assert view.details["Companies found"] == "1 job boards · 2 HN · 1 Hunter hiring search"
    assert [view.details[key] for key in (
        "HN thread", "HN posts read / matching role", "HN parsed / LLM-parsed / LLM-rejected",
        "HN filtered out", "HN kept / over the cap")] == [
        "Ask HN: Who is hiring? (September 2026)", "11 / 8", "5 / 2 / 1", "3", "4 / 2"]
    assert view.errors == ["hn: algolia down"]
    assert "hn: algolia down" in render_contact_report(view)


def test_a_rerendered_view_says_not_recorded_for_the_hn_funnel_but_keeps_credits():
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", WHEN)
    ctx.runs.record_spend(run_id, Spend(500, 3, 0, 30))

    view = build_contact_view(ctx, run_id, summary=None)

    assert view.credits_line == "3 credits used · 47 left"
    assert view.details["HN thread"] == "not recorded"
    hn_lines = [key for key in view.details if key.startswith("HN ")]
    assert len(hn_lines) == 5 and all(view.details[key] == "not recorded" for key in hn_lines)
    assert view.errors == ["not recorded"]


def test_a_company_run_has_no_hn_lines():
    ctx = build_context(); run_id = start_run(ctx)
    assert not [key for key in build_contact_view(ctx, run_id).details if key.startswith("HN")]


def test_a_run_without_errors_says_none():
    ctx = build_context(); run_id = start_run(ctx)
    view = build_contact_view(ctx, run_id, RunSummary(run_id))
    assert view.errors == []
    assert re.search(r"Errors.*?None\.", render_contact_report(view), re.DOTALL)


def test_a_run_that_is_not_there_or_predates_the_report_cannot_be_built():
    ctx = build_context()
    with pytest.raises(ValueError, match="run 7"):
        build_contact_view(ctx, 7)
    old = ctx.runs.conn.execute(
        "INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max, started_at,"
        " status, schema_version) VALUES ('r', 's', 'US', 0, 0, ?, 'done', 3)", (WHEN.isoformat(),))
    with pytest.raises(ValueError, match=f"run {old.lastrowid}"):
        build_contact_view(ctx, old.lastrowid)


# --- filters and file name --------------------------------------------------------

def test_describe_filters():
    filters = RunFilters(("Software Development",), ("51-200",), Location("US", "TX"))
    assert describe_filters(filters) == "Software Development · 51-200 · Texas"
    assert describe_filters(RunFilters(work_modes=("remote",))) == (
        "any sector · any size · United States · remote")
    assert describe_filters(RunFilters(
        ("Banking", "Health, Wellness and Fitness"), ("11-50", "51-200"), Location("CA"),
        ("hybrid", "remote"))) == (
        "Banking; Health, Wellness and Fitness · 11-50, 51-200 · Canada · hybrid, remote")


def test_describe_filters_leaves_out_work_modes_that_are_not_a_restriction():
    every_mode = tuple(sorted(ALL_WORK_MODES))
    assert describe_filters(RunFilters(work_modes=every_mode)) == (
        "any sector · any size · United States")
    assert describe_filters(RunFilters()) == "any sector · any size · United States"


def test_report_file_name_has_date_run_kind_and_slug(tmp_path):
    ctx = build_context()
    run_id = ctx.runs.start("jobs", "Backend Engineer", RunFilters(), "hiring", datetime(2026, 9, 21, 9))

    path = write_contact_report(build_contact_view(ctx, run_id), tmp_path / "reports")

    assert path == tmp_path / "reports" / "2026-09-21-run1-jobs-backend-engineer.html"
    assert path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_a_company_run_file_is_named_after_its_first_sector(tmp_path):
    ctx = build_context()
    named = ctx.runs.start("companies", "", RunFilters(("Health, Wellness and Fitness", "Banking")),
                           "sponsors", datetime(2026, 9, 21, 9))
    anything = ctx.runs.start("companies", "", RunFilters(), "sponsors", datetime(2026, 9, 22, 9))

    assert write_contact_report(build_contact_view(ctx, named), tmp_path).name == (
        "2026-09-21-run1-companies-health-wellness-and-fitness.html")
    assert write_contact_report(build_contact_view(ctx, anything), tmp_path).name == (
        "2026-09-22-run2-companies-any-sector.html")


def test_a_hand_built_view_renders_too():
    view = ContactReportView(
        1, "companies", "Company run", "any sector", "sponsors", date(2026, 9, 21), "not recorded",
        [], [], [], [], {}, [])
    assert "Company run" in render_contact_report(view)
