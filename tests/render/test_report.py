from datetime import date

from outreach.render.report import (CURRENT_STATE_CLASSES, STAGE_LABELS, ReportCompany,
                                    ReportContact, ReportEvidence, ReportExcluded,
                                    ReportFinding, ReportHook, ReportPosting, ReportView,
                                    linkedin_search_url, render_report)

QUOTE = "Our nightly reconciliation job now regularly exceeds its 6-hour window."


def card(name="Ledgerline", domain="ledgerline.example", stage="growth", **overrides
         ) -> ReportCompany:
    base = dict(
        name=name, domain=domain, headcount=120, headcount_source="hunter",
        reason="passed", stage=stage, stage_label=STAGE_LABELS[stage],
        stage_reasons=["Series B (2026-03)", "~120 employees", "founded 2019"],
        founded_year=2019, latest_round="Series B (2026-03)",
        postings=[ReportPosting("Backend Engineer", f"https://{domain}/jobs/1", "remote")],
        findings=[ReportFinding(
            theme="active-build", claim="recon overruns its window",
            summary="two first-party sources agree", corroborated=True,
            evidence=[ReportEvidence(QUOTE, "eng_blog", f"https://{domain}/blog/1",
                                     date(2026, 9, 2))])],
    )
    base.update(overrides)
    return ReportCompany(**base)


def view_with(target=(), other=(), none=(), excluded=(), **overrides) -> ReportView:
    base = dict(
        role_title="Backend Engineer", sector="fintech", run_date=date(2026, 9, 21),
        headcount_min=1, headcount_max=2000,
        target_stage=list(target), other_stages=list(other), no_findings=list(none),
        excluded=list(excluded), diagnostics={"Companies discovered": "2"},
    )
    base.update(overrides)
    return ReportView(**base)


def _card_html(html: str, name: str) -> str:
    start = html.index(f'<span class="co-name">{name}</span>')
    return html[start:html.index("</article>", start)]


def test_verbatim_quote_appears_in_the_output():
    html = render_report(view_with(target=[card()]))
    assert QUOTE in html


def test_sections_render_in_stage_order():
    html = render_report(view_with(
        target=[card()], other=[card("Seedling", "seedling.example", stage="seed_startup")],
        none=[card("Quiet", "quiet.example", stage="unknown", findings=[],
                   reason="no_evidence")],
        excluded=[ReportExcluded("Huge", "huge.example", "excluded_size")]))
    idx = [html.index(f'id="{s}"') for s in
           ("target-stage", "other-stages", "no-findings", "excluded", "diagnostics")]
    assert idx == sorted(idx)
    assert "Growth &amp; Expansion" in html and "No building evidence" in html


def test_stage_badge_and_reasons_render():
    html = render_report(view_with(target=[card()]))
    assert 'class="stage stage-growth"' in html
    assert "Growth &amp; Establishment" in html
    assert "Series B (2026-03) · ~120 employees" in html


def test_card_shows_size_source_founding_and_latest_round():
    html = _card_html(render_report(view_with(target=[card()])), "Ledgerline")
    assert "~120 employees" in html and "(hunter)" in html
    assert "founded 2019" in html
    assert "latest round Series B (2026-03)" in html


def test_postings_carry_a_work_mode_tag():
    html = render_report(view_with(target=[card()]))
    assert '<span class="work-mode">remote</span>' in html
    assert 'href="https://ledgerline.example/jobs/1"' in html


def test_a_non_web_posting_or_repo_url_is_text_not_a_link():
    """Posting and repo URLs are third-party data; autoescaping does not
    stop a `javascript:` href."""
    c = card(postings=[ReportPosting("Backend Engineer", "javascript:alert(1)", "remote")],
             hooks=[ReportHook("repo", "evil-repo", "JavaScript:alert(2)", None)])
    html = _card_html(render_report(view_with(target=[c])), "Ledgerline")
    assert "Backend Engineer" in html and "evil-repo" in html
    assert "javascript:" not in html.lower()


def test_finding_quotes_link_to_their_sources():
    html = _card_html(render_report(view_with(target=[card()])), "Ledgerline")
    assert "What they&#39;re building" in html or "What they're building" in html
    assert "recon overruns its window" in html
    assert 'href="https://ledgerline.example/blog/1"' in html


def test_corroborated_badge_only_on_two_source_findings():
    single = card("Solo", "solo.example", findings=[ReportFinding(
        "active-build", "one source only", "s", corroborated=False,
        evidence=[ReportEvidence(QUOTE, "eng_blog", "https://solo.example/b", None)])])
    html = render_report(view_with(target=[card(), single]))
    assert 'class="badge corroborated"' in _card_html(html, "Ledgerline")
    assert 'class="badge corroborated"' not in _card_html(html, "Solo")


def test_hooks_list_repos_with_their_push_date_and_docs():
    c = card(hooks=[
        ReportHook("repo", "recon-worker — Streaming ledger reconciliation",
                   "https://github.com/ledgerline/recon-worker", date(2026, 9, 15)),
        ReportHook("docs", "https://ledgerline.example/developers",
                   "https://ledgerline.example/developers", None)])
    html = _card_html(render_report(view_with(target=[c])), "Ledgerline")
    assert "Hooks for a build" in html
    assert 'href="https://github.com/ledgerline/recon-worker"' in html
    assert "pushed 15 Sep 2026" in html
    assert 'href="https://ledgerline.example/developers"' in html


def test_a_card_without_hooks_has_no_hooks_heading():
    assert "Hooks for a build" not in render_report(view_with(target=[card()]))


def test_linkedin_search_url_is_a_people_search_for_name_and_company():
    assert linkedin_search_url("Tomas Reyes", "Thin Co") == (
        "https://www.linkedin.com/search/results/people/?keywords=Tomas+Reyes+Thin+Co")


def test_a_contact_without_a_profile_gets_a_labelled_search_link():
    c = card(contacts=[ReportContact(
        "Tomas Reyes", "Head of Engineering", None, "not_found", None, "tier 1",
        linkedin_search=linkedin_search_url("Tomas Reyes", "Thin Co"))])
    html = render_report(view_with(target=[c]))
    assert "Search LinkedIn" in html
    assert "linkedin.com/search/results/people/?keywords=Tomas+Reyes+Thin+Co" in html


def test_a_found_profile_is_linked_as_linkedin_not_search():
    c = card(contacts=[ReportContact(
        "Marisol Okonkwo", "Co-founder & CTO", "m@ledgerline.example", "verified",
        "https://www.linkedin.com/in/marisol", "tier 1")])
    card_html = _card_html(render_report(view_with(target=[c])), "Ledgerline")
    assert 'href="https://www.linkedin.com/in/marisol"' in card_html
    assert ">LinkedIn</a>" in card_html
    assert "Search LinkedIn" not in card_html


def test_unverified_contact_never_renders_an_email_address():
    c = card(contacts=[ReportContact(
        full_name="Deepak Raman", title="Eng Lead", email="guess@ledgerline.example",
        email_status="unverified", profile_url="https://www.linkedin.com/in/deepak",
        why="tier 3")])
    html = render_report(view_with(target=[c]))
    person = html.split("Deepak Raman")[1].split("</article>")[0]
    assert "@" not in person
    assert "no verified address" in person


def test_excluded_companies_are_listed_with_their_reason():
    html = render_report(view_with(excluded=[
        ReportExcluded("Huge", "huge.example", "excluded_size"),
        ReportExcluded("Onsite", "onsite.example", "excluded_no_matching_posting")]))
    excluded = html[html.index('id="excluded"'):html.index('id="diagnostics"')]
    assert "huge.example" in excluded and "onsite.example" in excluded
    assert "over 2,000 employees" in excluded
    assert "no full-time posting in the requested work mode" in excluded


def test_report_header_states_the_enforced_size_cap():
    html = render_report(view_with())
    header = html[html.index('<header class="run">'):html.index("</header>")]
    assert "≤ 2,000 employees" in header


def test_gate_failure_reason_is_shown_for_no_findings_companies():
    c = card("Northsight", "northsight.example", stage="unknown", findings=[],
             reason="insufficient_independent_sources",
             fetch_log=[("eng_blog", "http_error", 404)])
    html = render_report(view_with(none=[c], min_independent_sources=2))
    assert "fewer than 2 independent sources" in html
    assert "404" in html


def test_research_failure_shows_its_error_text():
    c = card("Broken", "broken.example", stage="unknown", findings=[],
             reason="research_failed", error="boom")
    html = _card_html(render_report(view_with(none=[c])), "Broken")
    assert "Research failed" in html and "boom" in html


def test_a_current_state_date_reads_as_current_as_of():
    assert CURRENT_STATE_CLASSES == {"careers_page", "job_posting", "about"}
    c = card(findings=[ReportFinding("active-build", "claim", "s", False, evidence=[
        ReportEvidence("we hire", "about", "https://ledgerline.example/about",
                       date(2026, 9, 21))])])
    html = _card_html(render_report(view_with(target=[c])), "Ledgerline")
    assert "current as of 21 Sep" in html
    assert "21 Sep 2026" not in html


def test_diagnostics_footer_is_rendered():
    html = render_report(view_with())
    assert "Run diagnostics" in html and "Companies discovered" in html


def test_unconfirmed_domain_shows_a_note_instead_of_empty_contacts():
    html = render_report(view_with(target=[card(domain_confirmed=False, contacts=[])]))
    assert "unconfirmed" in html.lower()


def test_hn_company_card_links_its_post():
    html = render_report(view_with(target=[card(hn_post=(
        "via HN Who is hiring? (September 2026)", "https://news.ycombinator.com/item?id=101"))]))
    assert 'href="https://news.ycombinator.com/item?id=101"' in html
    assert "via HN Who is hiring? (September 2026)" in html


def test_skipped_cap_and_recent_are_listed_as_excluded():
    html = render_report(view_with(excluded=[
        ReportExcluded("Acme", "acme.io", "skipped_cap"),
        ReportExcluded("Zeta", "zeta.io", "skipped_recent")], hn_recheck_days=30))
    assert "not this run — over the HN cap" in html
    assert "researched in the last 30 days" in html
