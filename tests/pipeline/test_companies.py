"""A company run: Hunter's company search, then contacts bought at what it found."""
from dataclasses import replace

import pytest

from outreach.config import DEFAULT_PEOPLE, RunConfig
from outreach.contacts.fake import FakePeopleFinder
from outreach.contacts.hunter import HunterAPIError
from outreach.pipeline.companies import run_companies
from outreach.sources.companies import CompanyQuery, FakeCompanySearch, FoundCompany
from outreach.types import CompanyInfo, Location, RunFilters
from tests.pipeline.factories import CONFIG, build_context, company_id, person, start_run

HIRING = DEFAULT_PEOPLE["hiring"]
SPONSORS = DEFAULT_PEOPLE["sponsors"]
FILTERS = RunFilters(sectors=("Banking",), sizes=("51-200",))

A = FoundCompany("a.example", "A Bank", "Banking", "51-200")
B = FoundCompany("b.example", "B Bank", "Banking", "51-200")
C = FoundCompany("c.example", "C Bank", "Banking", "51-200")


def _finder(*companies):
    return FakePeopleFinder({c.domain: [person("Bo Diaz", "CTO", domain=c.domain)] for c in companies})


def _contacts_status(ctx, run_id, domain):
    return ctx.runs.stage_status(run_id, company_id(ctx, domain), "contacts")


def _run_count(ctx):
    return ctx.runs.conn.execute("SELECT count(*) FROM runs").fetchone()[0]


def test_company_run_buys_contacts_at_hunters_companies():
    a = FoundCompany("a.example", "A Bank", "Banking", "51-200")
    b = FoundCompany("b.example", "B Insurance", "Insurance", "11-50")
    ctx = build_context(people=_finder(a, b), company_search=FakeCompanySearch([a, b]))
    summary = run_companies(ctx, RunFilters(sectors=("Banking", "Insurance"), sizes=("51-200", "11-50")),
                            HIRING)
    assert (summary.found, summary.bought) == ({"hunter": 2}, 2)
    assert [_contacts_status(ctx, summary.run_id, d) for d in ("a.example", "b.example")] == ["bought", "bought"]
    assert ctx.company_info.for_run(summary.run_id) == [
        (company_id(ctx, "a.example"), CompanyInfo(0, "hunter", "Banking", "51-200")),
        (company_id(ctx, "b.example"), CompanyInfo(1, "hunter", "Insurance", "11-50"))]
    assert [ctx.runs.stage_status(summary.run_id, company_id(ctx, d), "discover")
            for d in ("a.example", "b.example")] == ["ok", "ok"]
    assert ctx.runs.info(summary.run_id).spend is not None


def test_company_run_records_kind_people_and_filters():
    filters = RunFilters(("Banking",), ("51-200",), Location("US", "NY"))
    search = FakeCompanySearch([A])
    ctx = build_context(people=_finder(A), company_search=search)
    summary = run_companies(ctx, filters, SPONSORS)
    info = ctx.runs.info(summary.run_id)
    assert (info.kind, info.people, info.role, info.filters) == ("companies", "sponsors", "", filters)
    assert search.queries == [CompanyQuery(("Banking",), ("51-200",), Location("US", "NY"))]
    assert _run_count(ctx) == 1


def test_no_location_given_searches_the_us_not_everywhere():
    search = FakeCompanySearch([A])
    run_companies(build_context(people=_finder(A), company_search=search), FILTERS, HIRING)
    [query] = search.queries
    assert query.location == Location() and query.location is not None


def test_a_company_run_is_finished_when_the_buying_is_done():
    ctx = build_context(people=_finder(A), company_search=FakeCompanySearch([A]))
    summary = run_companies(ctx, FILTERS, HIRING)
    row = ctx.runs.conn.execute("SELECT status, finished_at FROM runs WHERE id = ?",
                                (summary.run_id,)).fetchone()
    assert row["status"] == "done" and row["finished_at"] is not None


def test_a_failed_search_creates_no_run():
    search = FakeCompanySearch(error=HunterAPIError("Hunter discover returned HTTP 500"))
    ctx = build_context(company_search=search)
    with pytest.raises(HunterAPIError, match="HTTP 500"):
        run_companies(ctx, FILTERS, HIRING)
    assert _run_count(ctx) == 0


def test_resume_does_not_search_again():
    search = FakeCompanySearch([A, B])
    ctx = build_context(people=_finder(A, B), company_search=search,
                        config=replace(CONFIG, run=RunConfig(max_companies=1)))
    first = run_companies(ctx, FILTERS, HIRING)
    assert [_contacts_status(ctx, first.run_id, d) for d in ("a.example", "b.example")] == [
        "bought", "over_cap"]
    searches = len(search.queries)
    ctx.config = replace(CONFIG, run=RunConfig(max_companies=2))
    resumed = run_companies(ctx, FILTERS, HIRING, resume_run_id=first.run_id)
    assert len(search.queries) == searches == 1
    assert resumed.run_id == first.run_id and resumed.bought == 2
    assert _contacts_status(ctx, first.run_id, "b.example") == "bought"
    assert _run_count(ctx) == 1


def test_resuming_a_run_that_is_not_a_company_run_is_refused():
    search = FakeCompanySearch([A])
    ctx = build_context(people=_finder(A), company_search=search)
    jobs = start_run(ctx, kind="jobs")
    for run_id in (jobs, 999):
        with pytest.raises(ValueError, match=f"run {run_id} is not a company run"):
            run_companies(ctx, FILTERS, HIRING, resume_run_id=run_id)
    assert search.queries == [] and _run_count(ctx) == 1


def test_a_second_run_moves_on_to_new_companies():
    search = FakeCompanySearch([A, B, C])
    ctx = build_context(people=_finder(A, B, C), company_search=search,
                        config=replace(CONFIG, run=RunConfig(max_companies=2)))
    first = run_companies(ctx, FILTERS, HIRING)
    assert [_contacts_status(ctx, first.run_id, d) for d in ("a.example", "b.example", "c.example")] == [
        "bought", "bought", "over_cap"]
    second = run_companies(ctx, FILTERS, HIRING)
    assert [_contacts_status(ctx, second.run_id, d) for d in ("a.example", "b.example", "c.example")] == [
        "skipped_earlier_list", "skipped_earlier_list", "bought"]
    assert (second.found, second.bought) == ({"hunter": 3}, 1)
