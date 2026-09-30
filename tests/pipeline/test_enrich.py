"""The enrich stage and the work-mode filter at discovery."""
from outreach.pipeline.runner import run_pipeline
from outreach.sources.jobboards.multi import MultiBoardSource
from outreach.types import CompanyFacts
from tests.pipeline.factories import build_context, company_id


def test_a_company_over_the_cap_is_excluded_before_any_llm_call():
    ctx = build_context(facts={"thin.example": CompanyFacts(5000, "5001-10000", 2005, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    thin = company_id(ctx, "thin.example")
    assert ctx.runs.stage_status(s.run_id, thin, "enrich") == "excluded_size"
    assert ctx.runs.stage_status(s.run_id, thin, "evidence") is None
    assert s.excluded_size == 1


def test_a_band_straddling_the_cap_is_kept():
    ctx = build_context(facts={"thin.example": CompanyFacts(3000, "1001-5000", None, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "thin.example"), "evidence") == "ok"


def test_zero_credits_still_researches_every_company_as_unknown():
    ctx = build_context(credits=0)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "enrich") == "skipped_quota"
    assert ctx.runs.stage_status(s.run_id, good, "evidence") == "ok"
    assert ctx.facts_provider.calls == []


def test_facts_fresher_than_the_ttl_are_not_bought_again():
    ctx = build_context(facts={"good.example": CompanyFacts(80, "51-200", 2020, (), (), "hunter")})
    run_pipeline(ctx, "Backend Engineer", "fintech")
    second = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.facts_provider.calls.count("good.example") == 1
    assert second.facts_cached == 2  # good.example's facts and thin.example's miss


def test_an_enrichment_outage_keeps_the_company():
    ctx = build_context(facts_fail_on=frozenset({"good.example"}))
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "enrich") == "failed"
    assert ctx.runs.stage_status(s.run_id, good, "evidence") == "ok"
    assert "facts lookup failed" in ctx.runs.stage_error(s.run_id, good, "enrich")


def test_a_company_with_no_posting_in_the_requested_mode_is_excluded_not_dropped():
    # factory postings: good.example "Chicago, IL" (unknown), thin.example work_mode="onsite"
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech", work_modes=frozenset({"remote"}))
    thin = company_id(ctx, "thin.example")
    assert ctx.runs.stage_status(s.run_id, thin, "discover") == "excluded_no_matching_posting"
    assert thin in ctx.runs.companies_for_run(s.run_id)
    assert s.excluded_no_matching_posting == 1
    # Excluded before enrich: no homepage fetch, no credit, no research.
    assert ctx.runs.stage_status(s.run_id, thin, "enrich") is None
    assert "thin.example" not in ctx.facts_provider.calls


def test_only_matching_postings_are_recorded_for_the_run():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech", work_modes=frozenset({"remote"}))
    good, thin = company_id(ctx, "good.example"), company_id(ctx, "thin.example")
    assert [p.url for p in ctx.postings.for_company(s.run_id, good)] == ["u1"]
    assert ctx.postings.for_company(s.run_id, thin) == []


def test_the_homepage_github_link_is_remembered():
    ctx = build_context()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.companies.get(company_id(ctx, "good.example")).github_org == "goodco"


def test_facts_are_stamped_with_the_run_date_not_the_wall_clock():
    ctx = build_context(facts={"good.example": CompanyFacts(80, "51-200", 2020, (), (), "hunter")})
    run_pipeline(ctx, "Backend Engineer", "fintech")
    good = ctx.companies.get(company_id(ctx, "good.example"))
    assert good.facts_fetched_at.date() == ctx.today
    assert good.headcount == 80


def test_an_unreachable_homepage_buys_no_facts_and_keeps_the_company():
    """A guessed domain no fetch confirmed may belong to someone else: its
    Hunter record would be another company's size, so no credit is spent."""
    ctx = build_context(include_unconfirmed_domain=True)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    ghost = company_id(ctx, "ghost.example")
    assert ctx.runs.stage_status(s.run_id, ghost, "enrich") == "skipped_domain_unconfirmed"
    assert "ghost.example" not in ctx.facts_provider.calls
    assert ctx.runs.stage_status(s.run_id, ghost, "evidence") == "ok"


def test_the_homepage_confirms_a_domain_that_research_later_clears():
    """The evidence stage re-derives its own fetch log; it must not erase the
    homepage attempt the contacts guard relies on."""
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")
    homepage = [a for a in ctx.fetch_attempts.for_company(s.run_id, good)
                if a.source_class.value == "homepage"]
    assert [(a.url, a.outcome, a.document_count) for a in homepage] == [
        ("https://good.example/", "ok", 0)]


def test_resuming_does_not_buy_facts_or_refetch_the_homepage_twice():
    ctx = build_context()
    first = run_pipeline(ctx, "Backend Engineer", "fintech")
    calls = list(ctx.facts_provider.calls)
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=first.run_id)
    assert ctx.facts_provider.calls == calls
    good = company_id(ctx, "good.example")
    homepage = [a for a in ctx.fetch_attempts.for_company(first.run_id, good)
                if a.source_class.value == "homepage"]
    assert len(homepage) == 1


class _DownBoard:
    def search(self, role_terms, region):
        raise RuntimeError("503")


def test_board_errors_are_reported_as_discover_errors():
    ctx = build_context()
    ctx.job_board = MultiBoardSource([_DownBoard(), ctx.job_board])
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert "discover: _DownBoard: 503" in s.errors
    assert s.companies == 2  # the healthy board's postings still arrive
