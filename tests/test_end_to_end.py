"""Whole pipeline, every adapter faked, asserting a real report is produced."""
from dataclasses import replace
from datetime import date

from tests.pipeline.factories import build_context
from outreach.contacts.fake import FakeContactProvider
from outreach.pipeline.runner import run_pipeline
from outreach.render.report import write_report
from outreach.cli import _summary_from_storage, build_view
from outreach.types import CompanyFacts, FundingRound

SERIES_B = CompanyFacts(120, None, 2019, (FundingRound("series_b", date(2026, 3, 1)),),
                        (), "hunter")


def _html(view, tmp_path) -> str:
    return write_report(view, tmp_path).read_text(encoding="utf-8")


def test_run_produces_a_report_file_with_evidence_and_failures(tmp_path):
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)

    html = path.read_text(encoding="utf-8")
    assert path.exists()
    # good.example has findings but no known stage; thin.example has none.
    assert [c.domain for c in view.other_stages] == ["good.example"]
    assert [c.domain for c in view.no_findings] == ["thin.example"]
    for section in ("target-stage", "other-stages", "no-findings", "excluded", "diagnostics"):
        assert f'id="{section}"' in html
    assert "Run diagnostics" in html
    assert str(summary.quotes_rejected) in html


def test_a_growth_company_with_findings_leads_the_report(tmp_path):
    ctx = build_context(facts={"good.example": SERIES_B})
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)

    [good] = view.target_stage
    assert good.domain == "good.example"
    assert (good.stage, good.stage_label) == ("growth", "Growth & Establishment")
    assert good.latest_round == "Series B (2026-03)" and good.founded_year == 2019
    assert good.findings and all(f.evidence for f in good.findings)
    assert [(p.url, p.work_mode) for p in good.postings] == [("u1", "unknown")]
    html = _html(view, tmp_path)
    assert html.index("good.example") < html.index('id="other-stages"')
    assert 'class="stage stage-growth"' in html
    assert "Series B (2026-03) · ~120 employees" in html


def test_fresh_summary_renders_real_diagnostic_counts(tmp_path):
    """The live `run` path (fresh=True, the default) must keep showing real numbers."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    html = _html(view, tmp_path)

    assert "not recorded" not in html
    assert str(summary.quotes_rejected) in html
    assert str(summary.skipped_quota) in html
    assert str(summary.failures) in html
    assert view.diagnostics["Company facts fetched / cached"] == (
        f"{summary.facts_fetched} / {summary.facts_cached}")
    assert view.diagnostics["Companies by stage"] == "Stage unknown 2"


def test_reconstructed_summary_shows_not_recorded_for_unrecoverable_diagnostics(tmp_path):
    """`report RUN_ID` reconstructs a RunSummary from storage (fresh=False).

    Rejected-quote and quota/failure counts are never persisted, so they
    must render as an honest "not recorded" rather than a fabricated 0 that
    would be indistinguishable from a run that genuinely had zero.
    """
    ctx = build_context()
    real_summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert real_summary.quotes_rejected > 0  # sanity: this run really did reject some

    reconstructed = _summary_from_storage(ctx, real_summary.run_id, "Backend Engineer",
                                          "fintech")
    # Everything the stage rows and findings can answer comes back exactly.
    for name in ("companies", "evidenced", "no_findings", "excluded_size",
                 "excluded_no_matching_posting", "stage_counts", "quotes_accepted"):
        assert getattr(reconstructed, name) == getattr(real_summary, name), name
    view = build_view(ctx, reconstructed, fresh=False)
    html = _html(view, tmp_path)

    # quotes_rejected, skipped_quota, skipped_domain_unconfirmed, failures,
    # facts fetched/cached, wall clock, credits remaining, credits consumed,
    # and the errors list: none of these nine is recoverable from storage.
    assert html.count("not recorded") == 9
    assert "Quotes accepted" in html and str(real_summary.quotes_accepted) in html


def test_a_company_whose_research_failed_still_appears_in_the_report(tmp_path):
    """A company with no finding row (research never completed) must not
    silently vanish -- it gets a card with its coverage log and error text."""
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)

    failed = [c for c in view.no_findings if c.reason == "research_failed"]
    assert len(failed) == 1
    assert failed[0].domain == "bad.example"
    assert failed[0].error is not None and "boom" in failed[0].error
    # Every REAL surface fetch raised before recording an attempt, so the log
    # holds only the job-posting read (its URL is unfetchable in this
    # fixture) and the synthetic GitHub skip -- the point is that the log is
    # READ at all (not omitted), and the error text is preserved either way.
    assert [(cls, outcome) for cls, outcome, _ in failed[0].fetch_log] == [
        ("job_posting", "blocked_by_robots"), ("github", "skipped_no_github_org")]

    html = _html(view, tmp_path)
    assert "bad.example" in html
    assert "Research failed" in html


def test_run_errors_are_rendered_in_the_diagnostics_footer(tmp_path):
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.errors  # sanity: this run really did record an error
    html = _html(build_view(ctx, summary), tmp_path)
    assert any(e in html for e in summary.errors)


def test_report_header_states_the_enforced_size_cap(tmp_path):
    """The cap is enforced at enrich (a company known to exceed it is
    excluded), so the header states it -- and only it: the floor of 1 is no
    filter at all and must not read as a band."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    html = _html(build_view(ctx, summary), tmp_path)
    assert "≤ 2,000 employees" in html
    assert "1–2000" not in html and "1–2,000" not in html


def test_excluded_companies_are_listed_with_their_reason(tmp_path):
    over_cap = CompanyFacts(5000, None, 2001, (), (), "hunter")
    ctx = build_context(facts={"good.example": over_cap})
    # Thin Co's only posting is onsite; Good Co's does not say, so it is kept
    # through discovery and then dropped for its size.
    summary = run_pipeline(ctx, "Backend Engineer", "fintech",
                           work_modes=frozenset({"remote"}))
    view = build_view(ctx, summary)

    assert {(e.domain, e.reason) for e in view.excluded} == {
        ("good.example", "excluded_size"), ("thin.example", "excluded_no_matching_posting")}
    assert not (view.target_stage or view.other_stages or view.no_findings)
    html = _html(view, tmp_path)
    assert "over 2,000 employees" in html
    assert "no full-time posting in the requested work mode" in html
    assert view.diagnostics["Excluded — over size cap"] == "1"
    assert view.diagnostics["Excluded — work mode"] == "1"


def test_hooks_list_recent_repos_and_developer_docs(tmp_path):
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    good = next(c for c in view.other_stages if c.domain == "good.example")

    assert [(h.kind, h.label, h.url, h.when) for h in good.hooks] == [
        ("repo", "recon-worker — Streaming ledger reconciliation",
         "https://github.com/goodco/recon-worker", date(2026, 9, 15)),
        ("docs", "https://good.example/developers", "https://good.example/developers", None),
    ]
    # thin.example links no GitHub org, so it has no repos to offer; its
    # fixture answers every URL, so its /docs does count as developer docs.
    thin = next(c for c in view.no_findings if c.domain == "thin.example")
    assert [(h.kind, h.url) for h in thin.hooks] == [("docs", "https://thin.example/docs")]
    html = _html(view, tmp_path)
    assert "Hooks for a build" in html
    assert "pushed 15 Sep 2026" in html


def test_a_contact_without_a_profile_gets_a_labelled_search_link(tmp_path):
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    thin = next(c for c in view.no_findings if c.domain == "thin.example")
    [tomas] = thin.contacts
    assert tomas.profile_url is None
    html = _html(view, tmp_path)
    assert "Search LinkedIn" in html
    assert "linkedin.com/search/results/people/?keywords=Tomas+Reyes+Thin+Co" in html


def test_a_found_profile_is_linked_as_linkedin_not_search(tmp_path):
    ctx = build_context()
    ctx = replace(ctx, contact_provider=FakeContactProvider(
        ctx.contact_provider._people, credits=10,
        profiles={"Marisol Okonkwo": "https://www.linkedin.com/in/marisol"}))
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    good = next(c for c in view.other_stages if c.domain == "good.example")
    # The recruiter is ranked out, so Marisol is the card's only contact.
    [marisol] = good.contacts
    assert marisol.linkedin_search is None

    html = _html(view, tmp_path)
    start = html.index('<span class="co-name">Good Co</span>')
    card_html = html[start:html.index("</article>", start)]
    assert 'href="https://www.linkedin.com/in/marisol"' in card_html
    assert "Search LinkedIn" not in card_html


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
    card = next(c for c in view.other_stages if c.domain == "good.example")
    assert len(card.contacts) <= ctx.config.ranking.max_contacts_per_company
    names = {c.full_name for c in card.contacts}
    assert "Irrelevant Designer" not in names  # no tier match -> dropped, not "unavailable"


def test_current_state_surface_date_is_labeled_distinctly_from_a_real_date(tmp_path):
    """A careers-page quote must read as "current as of", not as if it were
    dated the way a real blog post's publication date is."""
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    html = _html(build_view(ctx, summary), tmp_path)
    assert "current as of" in html
