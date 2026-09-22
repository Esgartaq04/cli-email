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

    assert html.count("not recorded") == 3
    assert "Quotes accepted" in html and str(real_summary.quotes_accepted) in html
