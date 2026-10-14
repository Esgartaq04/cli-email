"""A job run: companies from job boards, HN and Hunter's hiring search, then contacts bought at them."""
import json
from dataclasses import replace
from datetime import date

import pytest

from outreach.config import DEFAULT_PEOPLE, HNConfig, RunConfig
from outreach.contacts.fake import FakePeopleFinder
from outreach.contacts.hunter import HunterAPIError
from outreach.pipeline.jobs import find_job_prospects, run_jobs
from outreach.pipeline.summary import RunSummary
from outreach.sources.companies import CompanyQuery, FakeCompanySearch, FoundCompany
from outreach.sources.hn import FakeHNSource
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.types import ALL_WORK_MODES, CompanyInfo, Location, PostingRef, RunFilters
from tests.pipeline.factories import build_context, company_id, hn_thread, person, start_run

HIRING = DEFAULT_PEOPLE["hiring"]
SPONSORS = DEFAULT_PEOPLE["sponsors"]
TERMS = ["backend engineer"]

ACME_POST = (301, 'Acme | Backend Engineer | Austin, TX | ONSITE | Full-time<p>We build streaming '
                  'payroll for restaurants. <a href="https://jobs.ashbyhq.com/acmehq">jobs</a> '
                  '<a href="https://acme.io">acme.io</a>')
DESIGN_POST = (302, 'Designco | Backend Engineer | Austin, TX | ONSITE | Full-time<p>'
                    '<a href="https://jobs.ashbyhq.com/designco">jobs</a> <a href="https://designco.dev">x</a>')
SOLO_POST = (303, 'Solo Labs | Backend Engineer | Remote (US) | REMOTE<p>We build streaming payroll '
                  'for restaurants. <a href="https://sololabs.ai">sololabs.ai</a>')
GOOD_POST = (304, 'Good Co | Backend Engineer | Chicago, IL | ONSITE<p>We build reconciliation '
                  'software. <a href="https://good.example">good.example</a>')
ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/acmehq"


def _ashby(title):
    return json.dumps({"jobs": [{
        "title": title, "location": "Austin, TX", "isListed": True, "workplaceType": "OnSite",
        "employmentType": "FullTime", "jobUrl": f"https://jobs.ashbyhq.com/x/{title[:4]}",
        "address": {"postalAddress": {"addressCountry": "United States"}}}]})


ROUTES = {
    ASHBY_URL: _ashby("Backend Engineer"),
    "https://api.ashbyhq.com/posting-api/job-board/designco": _ashby("Product Designer"),
    "https://acme.io/": "<html><body>Acme</body></html>",
    "https://designco.dev/": "<html><body>Designco</body></html>",
}
HOMEPAGES = {f"https://{d}/": "<html></html>" for d in ("good.example", "ghost.example", "thin.example")}


def _posting(domain, name=None, location="Chicago, IL", mode="onsite"):
    return PostingRef(name or domain.split(".")[0].title(), domain, "Backend Engineer",
                      f"https://boards.example/{domain}/1", location, mode, "full_time")


GOOD = _posting("good.example", "Good Co")


def _finder(*domains):
    return FakePeopleFinder({d: [person("Bo Diaz", "CTO", domain=d)] for d in domains})


def _ctx(*, postings=(), posts=(), fixture_ids=(), routes=None, people=None, company_search=None, **hn):
    """A context whose HN thread holds `posts` (and the recorded posts `fixture_ids`)
    and whose job board returns `postings`. `hn` sets HNConfig fields."""
    ctx = build_context(postings=postings, routes=ROUTES if routes is None else routes,
                        people=people, company_search=company_search)
    ctx.hn = FakeHNSource(hn_thread(item_ids=list(fixture_ids), extra=list(posts)))
    ctx.config = replace(ctx.config, hn=replace(HNConfig(), **hn))
    return ctx


def _run(ctx, filters=RunFilters(), work_modes=ALL_WORK_MODES, people=HIRING, **kw):
    return run_jobs(ctx, "Backend Engineer", filters, work_modes, people, **kw)


def _status(ctx, run_id, domain, stage="contacts"):
    return ctx.runs.stage_status(run_id, company_id(ctx, domain), stage)


def _urls(ctx, run_id, domain):
    return [p.url for p in ctx.postings.for_company(run_id, company_id(ctx, domain))]


def _sources(ctx, run_id):
    """(domain, source) for each company the run recorded, in the order it considered them."""
    return [(ctx.companies.get(cid).canonical_domain, info.source)
            for cid, info in ctx.company_info.for_run(run_id)]


def _count(ctx, table):
    return ctx.runs.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _spy_on_fetches(ctx):
    urls = []
    get = ctx.fetcher.get

    def spy(url, *args, **kwargs):
        urls.append(url)
        return get(url, *args, **kwargs)
    ctx.fetcher.get = spy
    return urls


# --- where the companies come from -----------------------------------------------

def test_a_board_company_is_a_guessed_domain_that_needs_its_homepage():
    unrouted = _ctx(postings=[GOOD], people=_finder("good.example"))
    summary = _run(unrouted)
    assert _status(unrouted, summary.run_id, "good.example") == "domain_unconfirmed"
    assert unrouted.company_info.get(summary.run_id, company_id(unrouted, "good.example")).guessed_domain

    routed = _ctx(postings=[GOOD], people=_finder("good.example"),
                  routes={**ROUTES, "https://good.example/": "<html>Good</html>"})
    summary = _run(routed)
    assert _status(routed, summary.run_id, "good.example") == "bought"
    [attempt] = routed.fetch_attempts.for_company(summary.run_id, company_id(routed, "good.example"))
    assert (attempt.url, attempt.outcome) == ("https://good.example/", "ok")
    assert _urls(routed, summary.run_id, "good.example") == [GOOD.url]


def test_hn_company_with_an_ats_board_uses_the_posts_domain():
    ctx = _ctx(posts=[ACME_POST], people=_finder("acme.io"))
    summary = _run(ctx)
    assert _urls(ctx, summary.run_id, "acme.io") == ["https://jobs.ashbyhq.com/x/Back"]
    assert ctx.companies.find("acmehq.com") is None
    # The domain came from the post's own link, so no homepage check stands in the way.
    assert _status(ctx, summary.run_id, "acme.io") == "bought"
    assert ctx.fetch_attempts.for_company(summary.run_id, company_id(ctx, "acme.io")) == []


def test_an_ats_board_without_the_role_falls_back_to_the_post():
    ctx = _ctx(posts=[DESIGN_POST], people=_finder("designco.dev"))
    summary = _run(ctx)
    assert _urls(ctx, summary.run_id, "designco.dev") == ["https://news.ycombinator.com/item?id=302"]


def test_hn_companies_over_the_cap_spend_nothing():
    people = _finder("acme.io", "sololabs.ai")
    ctx = _ctx(posts=[ACME_POST, SOLO_POST], people=people, max_new_companies=1)
    summary = _run(ctx)
    assert _status(ctx, summary.run_id, "sololabs.ai", "discover") == "skipped_cap"
    assert _status(ctx, summary.run_id, "sololabs.ai") is None
    assert "sololabs.ai" not in people.count_calls
    record = ctx.hn_posts.for_company(summary.run_id, company_id(ctx, "sololabs.ai"))
    assert (record.status, record.item_id, record.parsed.domain) == ("skipped_cap", 303, "sololabs.ai")
    assert (record.thread_title, record.posted_at) == (
        "Ask HN: Who is hiring? (September 2026)", date(2026, 9, 1))
    kept = ctx.hn_posts.for_company(summary.run_id, company_id(ctx, "acme.io"))
    assert (kept.status, kept.parse_method) == ("kept", "parsed")
    assert (summary.hn_kept, summary.hn_skipped_cap, summary.found["hn"]) == (1, 1, 1)
    assert summary.left_out == 0  # the cap is not a filter of the user's


def test_a_board_company_in_the_hn_thread_is_not_counted_twice():
    ctx = _ctx(postings=[GOOD], posts=[GOOD_POST], people=_finder("good.example"),
               routes={**ROUTES, **HOMEPAGES})
    summary = _run(ctx)
    assert _sources(ctx, summary.run_id) == [("good.example", "board")]
    assert ctx.hn_posts.for_company(summary.run_id, company_id(ctx, "good.example")) is None
    assert (summary.found["board"], summary.found["hn"], summary.hn_kept) == (1, 0, 0)


def test_an_hn_outage_keeps_board_companies():
    ctx = _ctx(postings=[GOOD], people=_finder("good.example"), routes={**ROUTES, **HOMEPAGES})
    ctx.hn = FakeHNSource(error=RuntimeError("algolia down"))
    summary = _run(ctx)
    assert "hn: algolia down" in summary.errors
    assert _status(ctx, summary.run_id, "good.example") == "bought"


@pytest.mark.parametrize("switch_off", ["flag", "config"])
def test_hn_can_be_switched_off(switch_off):
    ctx = _ctx(posts=[ACME_POST], enabled=switch_off != "config")
    _run(ctx, use_hn=switch_off != "flag")
    assert ctx.hn.calls == 0
    assert ctx.companies.find("acme.io") is None


def test_hunter_hiring_companies_come_after_posting_companies():
    hired = FoundCompany("hired.example", "Hired Inc", "Banking", "51-200")
    search = FakeCompanySearch(hiring=[hired])
    ctx = _ctx(postings=[GOOD], posts=[SOLO_POST], routes={**ROUTES, **HOMEPAGES}, company_search=search,
               people=_finder("good.example", "sololabs.ai", "hired.example"))
    summary = _run(ctx)
    assert ctx.company_info.for_run(summary.run_id) == [
        (company_id(ctx, "good.example"), CompanyInfo(0, "board", None, None, "fits", True)),
        (company_id(ctx, "sololabs.ai"), CompanyInfo(1, "hn", None, None, "fits", False)),
        (company_id(ctx, "hired.example"), CompanyInfo(2, "hunter_hiring", "Banking", "51-200", "fits", False))]
    assert search.queries == [CompanyQuery((), (), Location(), hiring_titles=("backend engineer",))]
    assert summary.found == {"board": 1, "hn": 1, "hunter_hiring": 1}
    assert [_status(ctx, summary.run_id, d) for d in ("good.example", "sololabs.ai", "hired.example")] == [
        "bought", "bought", "bought"]


def test_a_company_found_twice_keeps_its_posting():
    search = FakeCompanySearch(hiring=[FoundCompany("good.example", "Good Hiring"),
                                       FoundCompany("acme.io", "Acme Hiring"),
                                       FoundCompany("new.example", "New Co")])
    ctx = _ctx(postings=[GOOD], posts=[ACME_POST], routes={**ROUTES, **HOMEPAGES}, company_search=search,
               people=_finder("good.example", "acme.io", "new.example"))
    summary = _run(ctx)
    assert _sources(ctx, summary.run_id) == [
        ("good.example", "board"), ("acme.io", "hn"), ("new.example", "hunter_hiring")]
    assert _urls(ctx, summary.run_id, "good.example") == [GOOD.url]
    assert _urls(ctx, summary.run_id, "acme.io") == ["https://jobs.ashbyhq.com/x/Back"]
    assert summary.found == {"board": 1, "hn": 1, "hunter_hiring": 1}


def test_no_search_terms_means_no_hiring_search_and_nothing_bought():
    # An empty expansion must not turn the hiring search into a plain company
    # search: Hunter would answer with arbitrary companies in the location.
    search = FakeCompanySearch([FoundCompany("arbitrary.example", "Arbitrary Inc")],
                               hiring=[FoundCompany("hired.example", "Hired Inc")])
    people = _finder("arbitrary.example", "hired.example")
    ctx = _ctx(postings=[GOOD], posts=[SOLO_POST], company_search=search, people=people)
    ctx.llm.expand_titles = lambda role: []
    summary = _run(ctx)
    assert search.queries == []
    assert summary.found == {"board": 0, "hn": 0, "hunter_hiring": 0}
    assert ctx.company_info.for_run(summary.run_id) == []
    assert (people.count_calls, people.search_calls, summary.bought, summary.spent) == ([], [], 0, 0)

    dry = find_job_prospects(ctx, [], RunFilters(), ALL_WORK_MODES, RunSummary(0), dry_run=True)
    assert dry == [] and search.queries == []


def test_a_hunter_error_in_the_hiring_search_does_not_stop_the_run():
    search = FakeCompanySearch(error=HunterAPIError("Hunter discover returned HTTP 429"))
    ctx = _ctx(postings=[GOOD], people=_finder("good.example"), routes={**ROUTES, **HOMEPAGES},
               company_search=search)
    summary = _run(ctx)
    assert "hunter: Hunter discover returned HTTP 429" in summary.errors
    assert _status(ctx, summary.run_id, "good.example") == "bought"


def test_only_the_errors_this_board_search_added_are_reported():
    class Flaky(FakeJobBoardSource):
        def __init__(self, postings):
            super().__init__(postings)
            self.errors = ["from an earlier search"]

        def search(self, role_terms, region):
            self.errors.append("AshbyBoardSource: boom")
            return super().search(role_terms, region)

    ctx = _ctx(people=_finder("good.example"))
    ctx.job_board = Flaky([GOOD])
    summary = _run(ctx)
    assert summary.errors == ["discover: AshbyBoardSource: boom"]


def test_a_posting_with_no_usable_domain_is_an_error_not_a_crash():
    ctx = _ctx(postings=[replace(GOOD, company_name="Bad Co", company_domain=""), GOOD],
               people=_finder("good.example"), routes={**ROUTES, **HOMEPAGES})
    summary = _run(ctx)
    assert summary.errors == ["discover Bad Co: cannot canonicalize an empty domain"]
    assert _sources(ctx, summary.run_id) == [("good.example", "board")]


# --- location and work mode --------------------------------------------------------

def test_location_filters_board_postings():
    austin, chicago = _posting("austin.example", location="Austin, TX"), _posting("chicago.example")
    search = FakeCompanySearch()
    ctx = _ctx(postings=[austin, chicago], company_search=search,
               routes={**ROUTES, "https://austin.example/": "<html></html>"},
               people=_finder("austin.example", "chicago.example"))
    summary = _run(ctx, RunFilters(location=Location("US", "TX")))
    assert _status(ctx, summary.run_id, "austin.example", "discover") == "ok"
    assert _status(ctx, summary.run_id, "chicago.example", "discover") == "excluded_no_matching_posting"
    assert _status(ctx, summary.run_id, "austin.example") == "bought"
    assert _status(ctx, summary.run_id, "chicago.example") is None
    assert _urls(ctx, summary.run_id, "austin.example") == [austin.url]
    assert _urls(ctx, summary.run_id, "chicago.example") == []
    assert (summary.found["board"], summary.left_out) == (1, 1)
    assert search.queries == [CompanyQuery(location=Location("US", "TX"), hiring_titles=("backend engineer",))]


def test_work_mode_filters_board_postings():
    remote = _posting("remote.example", location="Remote (US)", mode="remote")
    ctx = _ctx(postings=[remote, _posting("office.example")], routes={**ROUTES, "https://remote.example/": "x"},
               people=_finder("remote.example", "office.example"))
    summary = _run(ctx, work_modes=frozenset({"remote"}))
    assert _status(ctx, summary.run_id, "remote.example", "discover") == "ok"
    assert _status(ctx, summary.run_id, "office.example", "discover") == "excluded_no_matching_posting"
    assert summary.left_out == 1


# --- sector and size ---------------------------------------------------------------

def test_the_sector_check_leaves_out_known_misfits_and_keeps_unknowns():
    fits = FoundCompany("good.example", "Good Co", "Banking", "51-200")
    misfit = FoundCompany("thin.example", "Thin Co", "Software Development", "1-10")
    search = FakeCompanySearch(known={"good.example": fits, "thin.example": misfit})
    ctx = _ctx(postings=[_posting("ghost.example"), GOOD, _posting("thin.example")],
               routes={**ROUTES, **HOMEPAGES}, company_search=search,
               people=_finder("good.example", "ghost.example", "thin.example"))
    summary = _run(ctx, RunFilters(sectors=("Banking",)))
    assert ctx.company_info.for_run(summary.run_id) == [
        (company_id(ctx, "good.example"), CompanyInfo(0, "board", "Banking", "51-200", "fits", True)),
        (company_id(ctx, "ghost.example"), CompanyInfo(1, "board", None, None, "unknown", True)),
        (company_id(ctx, "thin.example"), CompanyInfo(2, "board", None, None, "no", True))]
    assert [_status(ctx, summary.run_id, d, "discover") for d in ("good.example", "ghost.example", "thin.example")] == [
        "ok", "ok", "left_out_filters"]
    assert [_status(ctx, summary.run_id, d) for d in ("good.example", "ghost.example", "thin.example")] == [
        "bought", "bought", None]
    assert search.checks == [(("ghost.example", "good.example", "thin.example"),
                              CompanyQuery(("Banking",), (), Location()))]
    assert (summary.found["board"], summary.left_out) == (2, 1)


def test_a_size_alone_is_checked_but_only_for_companies_not_already_left_out():
    search = FakeCompanySearch(known={"good.example": FoundCompany("good.example", "Good", "Banking", "11-50")})
    ctx = _ctx(postings=[GOOD, _posting("austin.example", location="Austin, TX")], company_search=search,
               routes={**ROUTES, **HOMEPAGES}, people=_finder("good.example"))
    summary = _run(ctx, RunFilters(sizes=("11-50",), location=Location("US", "IL")))
    assert search.checks == [(("good.example",), CompanyQuery((), ("11-50",), Location("US", "IL")))]
    assert _status(ctx, summary.run_id, "austin.example", "discover") == "excluded_no_matching_posting"


def test_with_nothing_left_to_check_hunter_is_not_asked():
    search = FakeCompanySearch()
    ctx = _ctx(postings=[_posting("austin.example", location="Austin, TX")], company_search=search)
    _run(ctx, RunFilters(sizes=("11-50",), location=Location("US", "IL")))
    assert search.checks == []


def test_a_failed_check_leaves_every_company_unknown_but_kept():
    class CheckDown(FakeCompanySearch):
        def check(self, domains, query):
            raise HunterAPIError("Hunter discover returned HTTP 429")

    ctx = _ctx(postings=[GOOD], posts=[SOLO_POST], routes={**ROUTES, **HOMEPAGES},
               company_search=CheckDown(), people=_finder("good.example", "sololabs.ai"))
    summary = _run(ctx, RunFilters(sectors=("Banking",)))
    assert "hunter check: Hunter discover returned HTTP 429" in summary.errors
    assert [info.fit for _, info in ctx.company_info.for_run(summary.run_id)] == ["unknown", "unknown"]
    assert summary.bought == 2 and summary.left_out == 0


def test_no_sector_or_size_means_no_check():
    search = FakeCompanySearch()
    ctx = _ctx(postings=[GOOD], posts=[SOLO_POST], company_search=search)
    _run(ctx, RunFilters(location=Location("US", "IL")))
    assert search.checks == []


# --- running and resuming ------------------------------------------------------------

def test_resume_does_not_rediscover():
    ctx = _ctx(postings=[GOOD], posts=[ACME_POST], routes={**ROUTES, **HOMEPAGES},
               people=_finder("good.example", "acme.io"))
    ctx.config = replace(ctx.config, run=RunConfig(max_companies=1))
    first = _run(ctx)
    assert [_status(ctx, first.run_id, d) for d in ("good.example", "acme.io")] == ["bought", "over_cap"]
    assert (ctx.job_board.calls, ctx.hn.calls) == (1, 1)

    def expand_again(role):
        raise AssertionError("a resume must not ask the LLM to expand the role again")
    ctx.llm.expand_titles = expand_again
    ctx.config = replace(ctx.config, run=RunConfig(max_companies=2))
    resumed = _run(ctx, resume_run_id=first.run_id)
    assert (ctx.job_board.calls, ctx.hn.calls) == (1, 1)
    assert resumed.run_id == first.run_id and resumed.bought == 2
    assert _status(ctx, first.run_id, "acme.io") == "bought"
    assert _count(ctx, "runs") == 1


def test_resuming_a_run_that_is_not_a_jobs_run_is_refused():
    ctx = _ctx(postings=[GOOD])
    companies = start_run(ctx, kind="companies")
    for run_id in (companies, 999):
        with pytest.raises(ValueError, match=f"run {run_id} is not a job run"):
            _run(ctx, resume_run_id=run_id)
    assert ctx.job_board.calls == 0 and _count(ctx, "runs") == 1


def test_runs_record_kind_jobs_and_the_role():
    filters = RunFilters(location=Location("US", "TX"), work_modes=("remote",))
    remote = _posting("good.example", "Good Co", location="Remote (US)", mode="remote")
    ctx = _ctx(postings=[remote], people=_finder("good.example"), routes={**ROUTES, **HOMEPAGES})
    summary = _run(ctx, filters, frozenset({"remote"}), SPONSORS)
    info = ctx.runs.info(summary.run_id)
    assert (info.kind, info.role, info.people, info.filters) == ("jobs", "Backend Engineer", "sponsors", filters)
    row = ctx.runs.conn.execute("SELECT status, finished_at FROM runs WHERE id = ?",
                                (summary.run_id,)).fetchone()
    assert row["status"] == "done" and row["finished_at"] is not None


# --- the dry run ---------------------------------------------------------------------

def test_a_dry_run_never_calls_the_llm_or_hn_linked_boards():
    # Post 108 is free text only the LLM fallback could read.
    ctx = _ctx(posts=[ACME_POST], fixture_ids=[108])
    fetched = _spy_on_fetches(ctx)
    summary = RunSummary(0)
    prospects = find_job_prospects(ctx, TERMS, RunFilters(), ALL_WORK_MODES, summary, dry_run=True)
    assert ctx.llm.parse_calls == 0
    assert not [url for url in fetched if "ashbyhq" in url]
    [acme] = prospects
    assert (acme.domain, acme.source, acme.hn_status) == ("acme.io", "hn", "kept")
    assert [p.url for p in acme.postings] == ["https://news.ycombinator.com/item?id=301"]
    assert (summary.hn_needs_llm, summary.hn_kept, summary.found["hn"]) == (1, 1, 1)


def test_a_dry_run_reads_and_never_writes():
    search = FakeCompanySearch(hiring=[FoundCompany("hired.example", "Hired Inc")])
    ctx = _ctx(postings=[GOOD], posts=[ACME_POST, SOLO_POST], company_search=search)
    find_job_prospects(ctx, TERMS, RunFilters(), ALL_WORK_MODES, RunSummary(0), dry_run=True)
    assert [_count(ctx, table) for table in ("runs", "companies", "run_postings", "run_hn_posts",
                                              "run_company_info", "run_companies")] == [0] * 6


def test_a_dry_run_finds_the_same_companies_as_a_real_one():
    ctx = _ctx(postings=[GOOD], posts=[ACME_POST, SOLO_POST])
    fetched = _spy_on_fetches(ctx)

    def domains(dry_run):
        found = find_job_prospects(ctx, TERMS, RunFilters(), ALL_WORK_MODES, RunSummary(0), dry_run=dry_run)
        return [(p.domain, p.source) for p in found]
    dry = domains(True)
    assert not fetched
    assert domains(False) == dry == [("good.example", "board"), ("acme.io", "hn"), ("sololabs.ai", "hn")]
    assert ASHBY_URL in fetched
