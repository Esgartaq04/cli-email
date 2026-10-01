from dataclasses import replace

from outreach.cli import build_view
from outreach.pipeline.runner import run_pipeline
from outreach.render.report import write_report
from tests.pipeline.test_reading_sources import THEME, _ctx

BLOG_ONLY = "https://blogco.example/jobs/missing"


def _strict(ctx, min_sources=2):
    """One fresh first-party source is enough by default, so a blog-only
    company passes; demanding two is what makes it a gate failure to explain."""
    gate = replace(ctx.config.gate, min_independent_sources=min_sources)
    return replace(ctx, config=replace(ctx.config, gate=gate))


def _failed_company(tmp_path):
    ctx = _strict(_ctx(posting_url=BLOG_ONLY))
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    assert len(view.no_findings) == 1 and not (view.target_stage or view.other_stages)
    return view, view.no_findings[0], write_report(view, tmp_path).read_text(encoding="utf-8")


def test_a_failed_company_lists_its_themes_with_source_counts(tmp_path):
    _, company, _ = _failed_company(tmp_path)
    assert [t.theme for t in company.themes] == [THEME]
    theme = company.themes[0]
    assert len(theme.evidence) == 3
    assert theme.source_labels == ["eng blog"]
    assert theme.independent_sources == 1
    assert theme.reason == "insufficient_independent_sources"


def test_a_failed_company_lists_the_pages_read_and_what_each_yielded(tmp_path):
    _, company, _ = _failed_company(tmp_path)
    by_url = {s.url: s for s in company.sources_read}
    assert set(by_url) == {"https://blogco.example/blog/post-a",
                           "https://blogco.example/blog/post-c",
                           "https://blogco.example/blog/post-d"}
    assert all(s.claims == 1 for s in by_url.values())


def test_pages_that_yielded_no_claims_are_still_listed(tmp_path):
    ctx = _ctx()
    ctx.llm._claims = []  # every page reads fine but nothing is extractable
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    company = build_view(ctx, summary).no_findings[0]
    assert company.themes == []
    assert company.sources_read and all(s.claims == 0 for s in company.sources_read)


def test_the_rendered_report_explains_why_the_gate_failed(tmp_path):
    view, _, html = _failed_company(tmp_path)
    assert view.min_independent_sources == 2
    assert "What was found" in html
    assert THEME in html
    assert "1 of 2" in html
    assert "independent sources" in html
    assert "QUOTE-A the ledger job no longer fits its window." in html
    assert "Pages read" in html
    assert "https://blogco.example/blog/post-a" in html


def test_an_evidenced_company_does_not_get_the_failure_sections(tmp_path):
    ctx = _ctx()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    # Blog Co has findings but nothing places it in a stage.
    assert len(view.other_stages) == 1 and not view.no_findings
    assert view.other_stages[0].themes == [] and view.other_stages[0].sources_read == []
    assert "What was found" not in write_report(view, tmp_path).read_text(encoding="utf-8")
