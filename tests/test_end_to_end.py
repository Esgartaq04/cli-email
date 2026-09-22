"""Whole pipeline, every adapter faked, asserting a real report is produced."""
from tests.pipeline.factories import build_context
from outreach.pipeline.runner import RunSummary, run_pipeline
from outreach.render.report import write_report
from outreach.cli import build_view


def test_run_produces_a_report_file_with_evidence_and_failures(tmp_path):
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)

    html = path.read_text()
    assert path.exists()
    assert "Evidenced bottlenecks" in html
    assert "No bottleneck found" in html
    assert "Run diagnostics" in html
    assert str(summary.quotes_rejected) in html


def test_fresh_summary_renders_real_diagnostic_counts(tmp_path):
    """The live `run` path (fresh=True, the default) must keep showing real numbers."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)
    html = path.read_text()

    assert "not recorded" not in html
    assert str(summary.quotes_rejected) in html
    assert str(summary.skipped_quota) in html
    assert str(summary.failures) in html


def test_reconstructed_summary_shows_not_recorded_for_unrecoverable_diagnostics(tmp_path):
    """`report RUN_ID` reconstructs a RunSummary from storage (fresh=False).

    Rejected-quote and quota/failure counts are never persisted, so they
    must render as an honest "not recorded" rather than a fabricated 0 that
    would be indistinguishable from a run that genuinely had zero.
    """
    ctx = build_context()
    real_summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert real_summary.quotes_rejected > 0  # sanity: this run really did reject some

    reconstructed = RunSummary(
        run_id=real_summary.run_id, role_title=real_summary.role_title,
        sector=real_summary.sector, companies=real_summary.companies,
        evidenced=real_summary.evidenced, no_bottleneck=real_summary.no_bottleneck,
        quotes_accepted=real_summary.quotes_accepted,
        # quotes_rejected, skipped_quota, failures left at their dataclass
        # defaults of 0 -- exactly what `report RUN_ID` does, since none of
        # the three is recoverable from what got persisted.
    )
    view = build_view(ctx, reconstructed, fresh=False)
    path = write_report(view, tmp_path)
    html = path.read_text()

    # quotes_rejected, skipped_quota, skipped_domain_unconfirmed, failures,
    # wall clock, credits remaining, credits consumed, and the errors list:
    # none of these eight is recoverable from what got persisted.
    assert html.count("not recorded") == 8
    assert "Quotes accepted" in html and str(real_summary.quotes_accepted) in html


def test_a_company_whose_research_failed_still_appears_in_the_report(tmp_path):
    """A company with no bottleneck row (research never completed) must not
    silently vanish -- it gets a card with its coverage log and error text."""
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)

    failed = [c for c in view.no_bottleneck if c.reason == "research_failed"]
    assert len(failed) == 1
    assert failed[0].domain == "bad.example"
    assert failed[0].error is not None and "boom" in failed[0].error
    # Every surface fetch raised before recording an attempt here, so the
    # coverage log is legitimately empty -- the point is that it is READ
    # (not omitted), and that the error text is preserved either way.
    assert failed[0].fetch_log == []

    path = write_report(view, tmp_path)
    html = path.read_text()
    assert "bad.example" in html
    assert "Research failed" in html


def test_run_errors_are_rendered_in_the_diagnostics_footer(tmp_path):
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.errors  # sanity: this run really did record an error
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)
    html = path.read_text()
    assert any(e in html for e in summary.errors)


def test_report_header_does_not_claim_an_unenforced_headcount_band(tmp_path):
    """headcount is never populated in V1 -- the header must not claim a
    size band was enforced when no company was ever filtered by one."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)
    html = path.read_text()
    assert "headcount 20" not in html and "20–1000" not in html


def test_report_reads_contacts_ranked_and_capped_not_every_row_ever_stored():
    """`contacts.for_company` is company-scoped, not run-scoped: after
    multiple runs it can return more people than the configured cap,
    including ones whose title no longer matches this run's role keywords.
    The report must re-rank and truncate on read, not print every row."""
    from datetime import datetime
    from outreach.types import PersonRef

    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = ctx.companies.upsert("good.example", "Good Co", None, None)

    # Simulate leftovers from other runs: more tier-1 people than the cap
    # (3, per CONFIG.ranking), plus one whose title never scores at all.
    extra_people = [
        PersonRef("Extra Founder One", "Founder", None, "f1@good.example", "verified"),
        PersonRef("Extra Founder Two", "Founder", None, "f2@good.example", "verified"),
        PersonRef("Extra Founder Three", "Founder", None, "f3@good.example", "verified"),
        PersonRef("Irrelevant Designer", "Product Designer", None,
                  "d@good.example", "verified"),
    ]
    for person in extra_people:
        ctx.contacts.upsert(good, person, "provider", datetime.now())

    assert len(ctx.contacts.for_company(good)) > ctx.config.ranking.max_contacts_per_company

    view = build_view(ctx, summary)
    card = next(c for c in view.evidenced if c.domain == "good.example")
    assert len(card.contacts) <= ctx.config.ranking.max_contacts_per_company
    names = {c.full_name for c in card.contacts}
    assert "Irrelevant Designer" not in names  # no tier match -> dropped, not "unavailable"


def test_current_state_surface_date_is_labeled_distinctly_from_a_real_date(tmp_path):
    """A careers-page quote must read as "current as of", not as if it were
    dated the way a real blog post's publication date is."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)
    html = path.read_text()
    assert "current as of" in html
