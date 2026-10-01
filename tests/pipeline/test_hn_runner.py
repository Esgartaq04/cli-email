"""HN discovery merged into the pipeline: domains, postings, evidence, spend."""
import json
from dataclasses import replace

from outreach.config import HNConfig
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.pipeline.runner import run_pipeline
from outreach.sources.hn import FakeHNSource
from outreach.types import SourceClass
from tests.pipeline.factories import build_context, company_id, hn_thread

ACME_POST = (301, 'Acme | Backend Engineer | Austin, TX | ONSITE | Full-time<p>We build streaming '
                  'payroll for restaurants. <a href="https://jobs.ashbyhq.com/acmehq">jobs</a> '
                  '<a href="https://acme.io">acme.io</a>')
DESIGN_POST = (302, 'Designco | Backend Engineer | Austin, TX | ONSITE | Full-time<p>'
                    '<a href="https://jobs.ashbyhq.com/designco">jobs</a> <a href="https://designco.dev">x</a>')
SOLO_POST = (303, 'Solo Labs | Backend Engineer | Remote (US) | REMOTE<p>We build streaming payroll '
                  'for restaurants. <a href="https://sololabs.ai">sololabs.ai</a>')
GOOD_POST = (304, 'Good Co | Backend Engineer | Chicago, IL | ONSITE<p>We build reconciliation '
                  'software. <a href="https://good.example">good.example</a>')


def _ashby(title):
    return json.dumps({"jobs": [{
        "title": title, "location": "Austin, TX", "isListed": True, "workplaceType": "OnSite",
        "employmentType": "FullTime", "jobUrl": f"https://jobs.ashbyhq.com/x/{title[:4]}",
        "address": {"postalAddress": {"addressCountry": "United States"}}}]})


ROUTES = {
    "https://api.ashbyhq.com/posting-api/job-board/acmehq": _ashby("Backend Engineer"),
    "https://api.ashbyhq.com/posting-api/job-board/designco": _ashby("Product Designer"),
    "https://acme.io/": "<html><body>Acme</body></html>",
    "https://designco.dev/": "<html><body>Designco</body></html>",
}


def _ctx(posts, routes=ROUTES, claims=None, **hn):
    ctx = build_context(routes=routes)
    ctx.hn = FakeHNSource(hn_thread(item_ids=[], extra=posts))
    if claims is not None:
        ctx.llm = FakeLLM(claims=claims, titles=["backend engineer"])
    ctx.config = replace(ctx.config, hn=replace(HNConfig(), **hn))
    return ctx


def _postings(ctx, run_id, domain):
    return ctx.postings.for_company(run_id, company_id(ctx, domain))


def test_hn_company_with_ats_board_uses_the_posts_domain():
    ctx = _ctx([ACME_POST])
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    urls = [p.url for p in _postings(ctx, s.run_id, "acme.io")]
    assert urls == ["https://jobs.ashbyhq.com/x/Back"]
    assert ctx.companies.find("acmehq.com") is None


def test_ats_board_without_the_role_falls_back_to_the_post():
    ctx = _ctx([DESIGN_POST])
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [p.url for p in _postings(ctx, s.run_id, "designco.dev")] == [
        "https://news.ycombinator.com/item?id=302"]


def test_hn_only_company_gets_an_hn_post_document_the_gate_accepts():
    claim = RawClaim("builds streaming payroll", "We build streaming payroll for restaurants.",
                     "active-build")
    ctx = _ctx([SOLO_POST], routes={**ROUTES, "https://sololabs.ai/": "<html>Solo</html>"},
               claims=[claim])
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    cid = company_id(ctx, "sololabs.ai")
    docs = [d for d in ctx.documents.for_company(cid) if d.source_class is SourceClass.HN_POST]
    assert [(d.url, d.publisher_domain, str(d.published_at)) for d in docs] == [
        ("https://news.ycombinator.com/item?id=303", "news.ycombinator.com", "2026-09-01")]
    findings = [f for f in ctx.findings.for_company(s.run_id, cid) if f.passed]
    assert findings and findings[0].theme == "active-build"


def test_hn_post_never_confirms_a_domain():
    ctx = _ctx([SOLO_POST], routes={})  # sololabs.ai: every page 404s
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    cid = company_id(ctx, "sololabs.ai")
    assert ctx.runs.stage_status(s.run_id, cid, "contacts") == "skipped_domain_unconfirmed"


def test_config_token_company_is_not_capped_but_gets_its_post():
    ctx = _ctx([GOOD_POST], max_new_companies=0)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    cid = company_id(ctx, "good.example")
    assert ctx.runs.stage_status(s.run_id, cid, "evidence") == "ok"
    assert any(d.source_class is SourceClass.HN_POST for d in ctx.documents.for_company(cid))
    assert ctx.hn_posts.for_company(s.run_id, cid).status == "kept"
    assert s.hn_kept == 0


def test_skipped_companies_spend_nothing():
    ctx = _ctx([ACME_POST, SOLO_POST], max_new_companies=1)
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    cid = company_id(ctx, "sololabs.ai")
    assert ctx.runs.stage_status(s.run_id, cid, "discover") == "skipped_cap"
    assert ctx.runs.stage_status(s.run_id, cid, "enrich") is None
    assert "sololabs.ai" not in ctx.facts_provider.calls
    assert "sololabs.ai" not in ctx.contact_provider.find_calls
    assert ctx.hn_posts.for_company(s.run_id, cid).status == "skipped_cap"
    assert cid not in [c for c in ctx.runs.companies_for_run(s.run_id)
                       if ctx.runs.stage_status(s.run_id, c, "discover") == "ok"]


def test_hn_outage_keeps_config_companies():
    ctx = build_context()
    ctx.hn = FakeHNSource(error=RuntimeError("algolia down"))
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert "hn: algolia down" in s.errors
    assert ctx.runs.stage_status(s.run_id, company_id(ctx, "good.example"), "evidence") == "ok"


def test_hn_disabled_skips_discovery():
    ctx = _ctx([ACME_POST], enabled=False)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert ctx.hn.calls == 0
    assert ctx.companies.find("acme.io") is None


def test_hn_postings_are_not_refetched():
    ctx = _ctx([SOLO_POST], routes={**ROUTES, "https://sololabs.ai/": "<html>Solo</html>"})
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    attempts = ctx.fetch_attempts.for_company(s.run_id, company_id(ctx, "sololabs.ai"))
    hn = [a for a in attempts if a.url.startswith("https://news.ycombinator.com")]
    assert [(a.source_class, a.outcome, a.document_count) for a in hn] == [
        (SourceClass.HN_POST, "ok", 1)]


def test_resume_keeps_the_original_hn_selection():
    from tests.pipeline.factories import hn_thread as fixture_thread
    ctx = build_context(routes={"https://acmerobotics.com/": "<html>Acme</html>",
                                "https://zetapay.com/": "<html>Zeta</html>"})
    ctx.hn = FakeHNSource(fixture_thread(item_ids=[101, 102, 104, 105]))
    ctx.config = replace(ctx.config, hn=replace(HNConfig(), max_new_companies=2))
    first = run_pipeline(ctx, "Backend Engineer", "fintech")
    zeta = company_id(ctx, "zetapay.com")
    ctx.runs.set_stage(first.run_id, zeta, "synthesize", "failed")

    # The thread grew: a Series C company would now rank first and push
    # an already-kept company out of the cap. A resume must not re-select.
    grown = (501, 'Bigco | Backend Engineer | Austin, TX | ONSITE<p>We raised a $90M Series C. '
                  '<a href="https://bigco.dev">bigco.dev</a>')
    ctx.hn = FakeHNSource(fixture_thread(item_ids=[101, 102, 104, 105], extra=[grown]))
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=first.run_id)

    assert ctx.hn.calls == 0
    assert ctx.companies.find("bigco.dev") is None
    assert ctx.runs.stage_status(first.run_id, zeta, "discover") == "ok"
    assert ctx.runs.stage_status(first.run_id, zeta, "synthesize") == "ok"
    assert ctx.runs.stage_status(first.run_id, company_id(ctx, "quillhealth.io"),
                                 "discover") == "skipped_cap"
    assert any(d.source_class is SourceClass.HN_POST for d in ctx.documents.for_company(zeta))
