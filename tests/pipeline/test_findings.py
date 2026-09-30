"""Stage classification, building findings, stage-ordered contacts and the
LinkedIn lookup -- the parts of a run that come after research."""
from dataclasses import replace

from outreach.contacts.fake import FakeContactProvider
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.net.fetcher import FetchOutcome
from outreach.pipeline.runner import run_pipeline
from outreach.types import CompanyFacts, Stage
from tests.pipeline.factories import build_context, company_id

# Quotes that exist verbatim on good.example's fixture pages (see factories).
CAREERS_QUOTE = ("the reconciliation pipeline that currently runs overnight and "
                 "is the team's tightest constraint")
BLOG_QUOTE = "Our nightly reconciliation job now regularly exceeds its 6-hour window."
CHANGELOG_QUOTE = "recon-worker: increase lock timeout to 900s (temporary)"
GITHUB_QUOTE = "Streaming ledger reconciliation"
ABOUT_QUOTE = "Good Co builds reconciliation software for finance teams."
PRESS_QUOTE = "Good Co raises a seed round to build real-time reconciliation."

MARISOL_URL = "https://www.linkedin.com/in/marisol"


def _with_claims(ctx, *claims: RawClaim):
    ctx.llm = FakeLLM(claims=list(claims), titles=["backend engineer"])
    return ctx


def _with_profiles(ctx, profiles: dict[str, str], credits: int = 10):
    ctx.contact_provider = FakeContactProvider(
        ctx.contact_provider._people, credits=credits, profiles=profiles)
    return ctx


def _with_hunter(ctx, **changes):
    ctx.config = replace(ctx.config, hunter=replace(ctx.config.hunter, **changes))
    return ctx


def _passed(ctx, run_id, domain):
    return [f for f in ctx.findings.for_company(run_id, company_id(ctx, domain))
            if f.passed]


# --- findings --------------------------------------------------------------

def test_up_to_three_findings_most_corroborated_first():
    ctx = _with_claims(
        build_context(),
        RawClaim("reconciliation is the team's constraint", CAREERS_QUOTE, "active-build"),
        RawClaim("reconciliation exceeds its window", BLOG_QUOTE, "active-build"),
        RawClaim("lock timeout raised as a stopgap", CHANGELOG_QUOTE, "platform-infra"),
        RawClaim("they publish their reconciler", GITHUB_QUOTE, "open-source"),
        RawClaim("they sell reconciliation software", ABOUT_QUOTE, "product-launch"),
    )
    s = run_pipeline(ctx, "Backend Engineer", "fintech")

    findings = _passed(ctx, s.run_id, "good.example")
    # Four themes pass; the one quoted on two surfaces leads, the single-source
    # ties fall to theme order, and the fourth is dropped by the cap.
    assert [f.theme for f in findings] == ["active-build", "open-source", "platform-infra"]
    assert [f.corroborated for f in findings] == [True, False, False]
    assert findings[0].claim == "reconciliation is the team's constraint"
    assert findings[0].reason == "passed"
    assert len(findings[0].evidence_ids) == 2
    assert s.evidenced == 1


def test_the_findings_cap_is_config():
    ctx = _with_claims(
        build_context(),
        RawClaim("reconciliation is the team's constraint", CAREERS_QUOTE, "active-build"),
        RawClaim("lock timeout raised as a stopgap", CHANGELOG_QUOTE, "platform-infra"),
    )
    ctx.config = replace(ctx.config, gate=replace(ctx.config.gate,
                                                  max_findings_per_company=1))
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [f.theme for f in _passed(ctx, s.run_id, "good.example")] == ["active-build"]


def test_stage_signals_never_become_findings():
    ctx = _with_claims(
        build_context(),
        RawClaim("reconciliation is the team's constraint", CAREERS_QUOTE, "active-build"),
        RawClaim("they raised a seed round", PRESS_QUOTE, "funding-round"),
    )
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    good = company_id(ctx, "good.example")

    # The signal was extracted, dated and first-party: it would clear the gate.
    assert any(i.theme == "funding-round" for i in ctx.evidence.for_company(s.run_id, good))
    assert [f.theme for f in ctx.findings.for_company(s.run_id, good)] == ["active-build"]
    # It fed the stage instead.
    assert ctx.runs.company_stage(s.run_id, good).stage is Stage.SEED_STARTUP


def test_a_company_evidenced_only_by_stage_signals_has_no_findings():
    ctx = _with_claims(build_context(),
                       RawClaim("they raised a seed round", PRESS_QUOTE, "funding-round"))
    s = run_pipeline(ctx, "Backend Engineer", "fintech")

    rows = ctx.findings.for_company(s.run_id, company_id(ctx, "good.example"))
    # One miss row, and its reason is judged over the building evidence alone:
    # a passing funding quote must not make the miss read as a split verdict.
    assert [(f.passed, f.reason) for f in rows] == [(False, "no_evidence")]
    assert s.evidenced == 0
    assert s.no_findings == 2


# --- stage classification --------------------------------------------------

def test_a_quoted_series_b_sets_the_stage_when_hunter_has_no_rounds():
    quote = "We raised a $30M Series B"
    ctx = _with_claims(build_context(), RawClaim("they raised a Series B", quote,
                                                 "funding-round"))
    real = ctx.fetcher.get

    def get(url):
        if url == "https://good.example/careers":
            return FetchOutcome(url, 200, f"<html><body><p>{quote} to hire faster."
                                "</p></body></html>", "ok")
        return real(url)

    ctx.fetcher.get = get
    s = run_pipeline(ctx, "Backend Engineer", "fintech")

    result = ctx.runs.company_stage(s.run_id, company_id(ctx, "good.example"))
    assert result.stage is Stage.GROWTH
    assert any("Series B" in reason for reason in result.reasons)
    assert s.stage_counts == {"growth": 1, "unknown": 1}


def test_hunter_facts_decide_the_stage():
    ctx = build_context(facts={
        "good.example": CompanyFacts(20, "11-50", 2023, (), (), "hunter"),
        "thin.example": CompanyFacts(700, "501-1000", 2015, (), (), "hunter"),
    })
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.runs.company_stage(
        s.run_id, company_id(ctx, "good.example")).stage is Stage.SEED_STARTUP
    assert ctx.runs.company_stage(
        s.run_id, company_id(ctx, "thin.example")).stage is Stage.EXPANSION


def test_every_researched_company_is_classified_even_when_its_evidence_failed():
    ctx = build_context(explode_on_domain="bad.example",
                        facts={"thin.example": CompanyFacts(5000, "5001-10000", None,
                                                            (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    bad = company_id(ctx, "bad.example")
    assert ctx.runs.stage_status(s.run_id, bad, "evidence") == "failed"
    assert ctx.runs.company_stage(s.run_id, bad).stage is Stage.UNKNOWN
    # Excluded by size before research: it gets no stage at all.
    assert ctx.runs.company_stage(s.run_id, company_id(ctx, "thin.example")) is None
    assert s.stage_counts == {"unknown": 2}


# --- contacts ----------------------------------------------------------------

def test_contacts_credits_go_to_growth_companies_first():
    ctx = build_context(credits=3, facts={
        "thin.example": CompanyFacts(120, "51-200", 2019, (), (), "hunter"),
        "good.example": CompanyFacts(20, "11-50", 2023, (), (), "hunter"),
    })
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    # Two credits bought both companies' facts; the last one goes to the
    # growth company even though the seed company was discovered first.
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "thin.example"),
                                 "contacts") == "ok"
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "good.example"),
                                 "contacts") == "skipped_quota"
    assert ctx.contact_provider.find_calls == ["thin.example"]


def test_missing_linkedin_is_looked_up_once_and_remembered():
    ctx = _with_profiles(build_context(), {"Marisol Okonkwo": MARISOL_URL})
    ctx.contact_provider._people["thin.example"] = []

    run_pipeline(ctx, "Backend Engineer", "fintech")
    run_pipeline(ctx, "Backend Engineer", "fintech")

    assert ctx.contact_provider.profile_calls == ["Marisol Okonkwo"]
    good = company_id(ctx, "good.example")
    assert [(c.full_name, c.profile_url) for c in ctx.contacts.for_company(good)] == [
        ("Marisol Okonkwo", MARISOL_URL)]


def test_a_profile_the_search_already_returned_is_not_looked_up():
    ctx = _with_profiles(build_context(), {"Marisol Okonkwo": MARISOL_URL})
    ctx.contact_provider._people["good.example"] = [
        replace(ctx.contact_provider._people["good.example"][0],
                profile_url="https://www.linkedin.com/in/from-search")]
    ctx.contact_provider._people["thin.example"] = []
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.profile_calls == []


def test_linkedin_lookup_respects_the_config_switch_and_budget():
    # Switched off: never asked, however much budget is left.
    ctx = _with_hunter(_with_profiles(build_context(), {"Marisol Okonkwo": MARISOL_URL}),
                       linkedin_lookup=False)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.profile_calls == []

    # Two credits of enrichment and one contact search leave nothing for it.
    ctx = _with_profiles(build_context(), {"Marisol Okonkwo": MARISOL_URL}, credits=3)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.profile_calls == []
    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "contacts") == "ok"
    assert [c.profile_url for c in ctx.contacts.for_company(good)] == [None]

    # Each lookup costs `finder_cost`: at 2 it takes the last two credits,
    # so the next company's contact search cannot be paid for.
    ctx = _with_hunter(_with_profiles(build_context(), {"Marisol Okonkwo": MARISOL_URL},
                                      credits=5), finder_cost=2)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.profile_calls == ["Marisol Okonkwo"]
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "thin.example"),
                                 "contacts") == "skipped_quota"


def test_a_single_token_name_is_not_looked_up():
    ctx = _with_profiles(build_context(), {})
    ctx.contact_provider._people["good.example"] = [
        replace(ctx.contact_provider._people["good.example"][0], full_name="Marisol")]
    ctx.contact_provider._people["thin.example"] = []
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.contact_provider.profile_calls == []


def test_a_linkedin_lookup_failure_does_not_lose_the_contact():
    ctx = build_context()
    ctx.contact_provider._people["thin.example"] = []

    def blow_up(domain, first_name, last_name):
        raise RuntimeError("finder down")

    ctx.contact_provider.find_profile = blow_up
    s = run_pipeline(ctx, "Backend Engineer", "fintech")

    good = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, good, "contacts") == "ok"
    assert [(c.full_name, c.email_status, c.profile_url)
            for c in ctx.contacts.for_company(good)] == [
        ("Marisol Okonkwo", "verified", None)]
    assert f"linkedin {good} Marisol Okonkwo: finder down" in s.errors


# --- resume ------------------------------------------------------------------

def test_resume_does_not_duplicate_postings_findings_or_enrichment():
    ctx = build_context(facts={"good.example": CompanyFacts(80, "51-200", 2020, (), (), "hunter")})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    ctx.runs.set_stage(s.run_id, company_id(ctx, "good.example"), "synthesize", "failed")
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=s.run_id)
    good = company_id(ctx, "good.example")
    assert len(ctx.postings.for_company(s.run_id, good)) == 1
    assert len([f for f in ctx.findings.for_company(s.run_id, good) if f.passed]) == \
           len({f.theme for f in ctx.findings.for_company(s.run_id, good) if f.passed})
    assert ctx.facts_provider.calls.count("good.example") == 1
    # The stage is re-derived on resume, not lost with the skipped stages.
    assert ctx.runs.company_stage(s.run_id, good).stage is Stage.GROWTH
