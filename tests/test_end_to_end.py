"""Whole pipeline, every adapter faked, asserting a real report is produced."""
from tests.pipeline.factories import build_context
from outreach.pipeline.runner import run_pipeline
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
