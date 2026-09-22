from datetime import date
from pathlib import Path

from outreach.render.report import ReportCompany, ReportEvidence, ReportView, render_report


def view(**overrides) -> ReportView:
    base = dict(
        role_title="Backend Engineer", sector="fintech", run_date=date(2026, 9, 21),
        headcount_min=20, headcount_max=1000,
        evidenced=[ReportCompany(
            name="Ledgerline", domain="ledgerline.example", headcount=48,
            headcount_source="careers page", claim="recon overruns its window",
            summary="two first-party sources agree", reason="passed",
            evidence=[ReportEvidence(
                quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
                source_class="eng_blog", url="https://ledgerline.example/blog/1",
                published_at=date(2026, 9, 2))],
            contacts=[], fetch_log=[])],
        no_bottleneck=[], diagnostics={"Companies discovered": "2"},
    )
    base.update(overrides)
    return ReportView(**base)


def test_verbatim_quote_appears_in_the_output():
    html = render_report(view())
    assert "Our nightly reconciliation job now regularly exceeds its 6-hour window." in html


def test_evidenced_section_precedes_no_bottleneck_section():
    html = render_report(view())
    assert html.index("Evidenced bottlenecks") < html.index("No bottleneck found")


def test_gate_failure_reason_is_shown_for_no_bottleneck_companies():
    v = view(evidenced=[], no_bottleneck=[ReportCompany(
        name="Northsight", domain="northsight.example", headcount=310,
        headcount_source="band", claim="", summary="",
        reason="insufficient_independent_sources", evidence=[], contacts=[],
        fetch_log=[("eng_blog", "http_error", 404)])])
    html = render_report(v)
    assert "insufficient_independent_sources" in html or "independent source" in html
    assert "404" in html


def test_unverified_contact_never_renders_an_email_address(tmp_path):
    from outreach.render.report import ReportContact
    v = view()
    v.evidenced[0].contacts = [ReportContact(
        full_name="Deepak Raman", title="Eng Lead", email=None,
        email_status="unverified", profile_url="https://p/1", why="tier 3")]
    html = render_report(v)
    assert "Deepak Raman" in html
    assert "@" not in html.split("Deepak Raman")[1].split("</div>")[0]


def test_diagnostics_footer_is_rendered():
    html = render_report(view())
    assert "Companies discovered" in html


def test_unconfirmed_domain_shows_a_note_instead_of_empty_contacts():
    v = view()
    v.evidenced[0].domain_confirmed = False
    v.evidenced[0].contacts = []
    html = render_report(v)
    assert "unconfirmed" in html.lower()
