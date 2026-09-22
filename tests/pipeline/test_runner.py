from outreach.pipeline.runner import run_pipeline
from outreach.types import SourceClass
from tests.pipeline.factories import build_context


def test_happy_path_produces_an_evidenced_company():
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.companies == 2
    assert summary.evidenced == 1
    assert summary.no_bottleneck == 1


def test_one_company_failing_does_not_abort_the_run():
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.failures == 1
    assert summary.evidenced == 1  # the healthy company still finished


def test_quota_shortfall_marks_companies_skipped_not_missing():
    ctx = build_context(credits=1)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.skipped_quota == 1
    assert summary.companies == 2


def test_rejected_quotes_are_counted_in_the_summary():
    ctx = build_context(include_invented_quote=True)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.quotes_rejected >= 1


def test_rerunning_the_same_run_id_skips_completed_stages():
    ctx = build_context()
    first = run_pipeline(ctx, "Backend Engineer", "fintech")
    calls_before = len(ctx.contact_provider.find_calls)
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=first.run_id)
    assert len(ctx.contact_provider.find_calls) == calls_before


def test_only_current_state_surfaces_are_dated_with_the_fetch_date():
    """A dated archive we cannot parse a date from must not look fresh."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    good = ctx.companies.upsert("good.example", "Good Co", None, None)
    by_class = {i.source_class: i.published_at
                for i in ctx.evidence.for_company(summary.run_id, good)}

    assert by_class[SourceClass.CAREERS_PAGE] == ctx.today
    assert by_class[SourceClass.ENG_BLOG] is None
    assert by_class[SourceClass.CHANGELOG] is None


def test_a_failed_company_gets_no_fabricated_verdict():
    """`no bottleneck found` about a company we never researched is a lie."""
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")

    bad = ctx.companies.upsert("bad.example", "Bad Co", None, None)
    assert ctx.runs.stage_status(summary.run_id, bad, "evidence") == "failed"
    verdicts = [b.company_id for b in ctx.bottlenecks.for_run(summary.run_id)]
    assert bad not in verdicts
