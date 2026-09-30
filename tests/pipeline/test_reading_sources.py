"""How the runner reads a company's blog posts and job postings."""
from dataclasses import replace
from datetime import date

import httpx

from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.net.fetcher import FetchOutcome, Fetcher
from outreach.pipeline.runner import run_pipeline
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.types import PostingRef, SourceClass
from tests.pipeline.factories import TODAY, build_context, company_id

THEME = "scaling-bottlenecks"


def _page(body: str, published: str | None = None) -> str:
    ld = (f'<script type="application/ld+json">{{"datePublished":"{published}"}}</script>'
          if published else "")
    return f"<html><head>{ld}</head><body>{body}</body></html>"


def _links(*hrefs: str) -> str:
    return _page("".join(f'<a href="{h}">link</a>' for h in hrefs))


SITE = {
    "/blog": _links("/blog/engineering", "/blog/launch-announcement"),
    "/blog/engineering": _links("/blog/post-a", "/blog/post-b", "/blog/post-c", "/blog/post-d"),
    "/blog/launch-announcement": _page("<p>MARKETING: we launched a shiny thing.</p>"),
    "/blog/post-a": _page("<p>QUOTE-A the ledger job no longer fits its window.</p>", "2026-08-25"),
    # /blog/post-b is deliberately absent: a listed post that 404s.
    "/blog/post-c": _page("<p>QUOTE-C reindexing needs an engineer on call.</p>", "2026-07-01"),
    "/blog/post-d": _page("<p>QUOTE-D the queue backs up every close.</p>", "2026-06-01"),
    "/jobs/1": _page("<p>QUOTE-JOB you will own the ledger job that no longer fits its window.</p>"),
}

CLAIMS = [
    RawClaim("ledger job outgrew its window", "QUOTE-A the ledger job no longer fits its window.", THEME),
    RawClaim("reindexing is manual", "QUOTE-C reindexing needs an engineer on call.", THEME),
    RawClaim("queue backs up", "QUOTE-D the queue backs up every close.", THEME),
    RawClaim("posting owns the ledger job", "QUOTE-JOB you will own the ledger job that no longer fits its window.", THEME),
    RawClaim("marketing", "MARKETING: we launched a shiny thing.", "launch-hype"),
]


def _ctx(site=SITE, host="blogco.example", posting_url="https://blogco.example/jobs/1",
         max_blog_posts=5):
    base = build_context()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == host and request.url.path in site:
            return httpx.Response(200, text=site[request.url.path])
        if request.url.path == "/jobs/1" and request.url.host == "boards.example":
            return httpx.Response(200, text=SITE["/jobs/1"])
        return httpx.Response(404)

    discovery = replace(base.config.discovery, max_blog_posts=max_blog_posts)
    return replace(
        base,
        config=replace(base.config, discovery=discovery),
        fetcher=Fetcher(httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=lambda seconds: None),
        llm=FakeLLM(claims=CLAIMS, titles=["backend engineer"]),
        job_board=FakeJobBoardSource([
            PostingRef("Blog Co", host, "Backend Engineer", posting_url, "Chicago, IL")]),
    )


def _documents(ctx, domain="blogco.example"):
    company = ctx.companies.get(1)
    assert company.canonical_domain == domain
    return ctx.documents.for_company(1)


def test_reads_engineering_posts_not_the_marketing_index():
    ctx = _ctx()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    urls = {d.url for d in _documents(ctx) if d.source_class is SourceClass.ENG_BLOG}
    assert urls == {"https://blogco.example/blog/post-a",
                    "https://blogco.example/blog/post-c",
                    "https://blogco.example/blog/post-d"}


def test_a_post_is_dated_from_its_own_markup():
    ctx = _ctx()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    by_url = {d.url: d for d in _documents(ctx)}
    assert by_url["https://blogco.example/blog/post-a"].published_at == date(2026, 8, 25)
    assert by_url["https://blogco.example/blog/post-c"].published_at == date(2026, 7, 1)


def test_a_post_that_does_not_state_a_date_stays_undated():
    site = dict(SITE, **{"/blog/post-a": _page("<p>QUOTE-A the ledger job no longer fits its window.</p>")})
    ctx = _ctx(site=site)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    by_url = {d.url: d for d in _documents(ctx)}
    assert by_url["https://blogco.example/blog/post-a"].published_at is None


def test_failed_posts_are_skipped_and_the_post_budget_still_fills():
    ctx = _ctx(max_blog_posts=2)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    urls = {d.url for d in _documents(ctx) if d.source_class is SourceClass.ENG_BLOG}
    # post-b 404s and is skipped; the budget of 2 is met by post-a and post-c.
    assert urls == {"https://blogco.example/blog/post-a", "https://blogco.example/blog/post-c"}
    attempts = ctx.fetch_attempts.for_company(1, 1)
    assert any(a.url.endswith("/blog/post-b") and a.outcome == "http_error" for a in attempts)


def test_the_post_budget_caps_how_many_posts_are_read():
    ctx = _ctx(max_blog_posts=1)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    urls = [d.url for d in _documents(ctx) if d.source_class is SourceClass.ENG_BLOG]
    assert urls == ["https://blogco.example/blog/post-a"]


def test_a_blog_with_no_followable_posts_is_read_at_the_index_as_before():
    site = {"/blog": _page("<p>QUOTE-A the ledger job no longer fits its window.</p>")}
    ctx = _ctx(site=site)
    run_pipeline(ctx, "Backend Engineer", "fintech")
    urls = [d.url for d in _documents(ctx) if d.source_class is SourceClass.ENG_BLOG]
    assert urls == ["https://blogco.example/blog"]


def test_the_matching_job_posting_is_read_as_current_first_party_evidence():
    ctx = _ctx()
    run_pipeline(ctx, "Backend Engineer", "fintech")
    postings = [d for d in _documents(ctx) if d.source_class is SourceClass.JOB_POSTING]
    assert [d.url for d in postings] == ["https://blogco.example/jobs/1"]
    assert postings[0].published_at == TODAY
    assert postings[0].publisher_domain == "blogco.example"


def test_a_blog_and_a_job_posting_corroborate_one_bottleneck():
    """Two source classes on one theme is exactly what the gate asks for."""
    ctx = _ctx()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.evidenced == 1
    assert summary.no_findings == 0


def test_blog_posts_alone_cannot_pass_the_gate():
    # The shared factory gate now needs one source; this test is about a
    # two-source gate refusing a single source class, so it asks for one.
    ctx = _ctx(posting_url="https://blogco.example/jobs/missing")
    ctx = replace(ctx, config=replace(
        ctx.config, gate=replace(ctx.config.gate, min_independent_sources=2)))
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.evidenced == 0
    assert summary.no_findings == 1


def test_a_job_posting_hosted_elsewhere_does_not_confirm_a_guessed_domain():
    """Reaching boards.example says nothing about whether blogco.example is real."""
    ctx = _ctx(site={}, posting_url="https://boards.example/jobs/1")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.skipped_domain_unconfirmed == 1


# --- research surfaces, against the shared good.example fixture -------------

def _good(ctx, source_class):
    return [d for d in ctx.documents.for_company(company_id(ctx, "good.example"))
            if d.source_class is source_class]


def _good_attempts(ctx, run_id, source_class):
    return [a for a in ctx.fetch_attempts.for_company(run_id, company_id(ctx, "good.example"))
            if a.source_class is source_class]


def test_each_changelog_entry_is_its_own_dated_document():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    docs = _good(ctx, SourceClass.CHANGELOG)
    assert [(d.url, d.published_at) for d in docs] == [
        ("https://good.example/changelog#2026-09-12", date(2026, 9, 12)),
        ("https://good.example/changelog#2026-08-01", date(2026, 8, 1)),
    ]
    # One fetch, recorded once, with both entries counted against it.
    assert [(a.url, a.outcome, a.document_count)
            for a in _good_attempts(ctx, s.run_id, SourceClass.CHANGELOG)] == [
        ("https://good.example/changelog", "ok", 2)]


def test_the_changelog_entry_budget_keeps_the_newest():
    ctx = build_context()
    ctx = replace(ctx, config=replace(ctx.config, discovery=replace(
        ctx.config.discovery, max_changelog_entries=1)))
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [d.url for d in _good(ctx, SourceClass.CHANGELOG)] == [
        "https://good.example/changelog#2026-09-12"]


def test_a_changelog_with_no_dated_entries_is_read_whole_and_undated():
    ctx = build_context()
    real = ctx.fetcher.get

    def get(url):
        if url == "https://good.example/changelog":
            return FetchOutcome(url, 200, "<html><body><p>recon-worker: increase lock "
                                "timeout to 900s (temporary)</p></body></html>", "ok")
        return real(url)

    ctx.fetcher.get = get
    run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [(d.url, d.published_at) for d in _good(ctx, SourceClass.CHANGELOG)] == [
        ("https://good.example/changelog", None)]


def test_press_falls_back_to_news_and_reads_dated_posts():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [(a.url, a.outcome) for a in _good_attempts(ctx, s.run_id, SourceClass.PRESS)] == [
        ("https://good.example/press", "http_error"),
        ("https://good.example/news", "ok"),
        ("https://good.example/news/seed-round", "ok"),
    ]
    assert [(d.url, d.published_at) for d in _good(ctx, SourceClass.PRESS)] == [
        ("https://good.example/news/seed-round", date(2026, 9, 2))]


def test_a_surface_that_answers_at_its_primary_path_tries_no_alternate():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    thin = company_id(ctx, "thin.example")  # thin.example answers on every path
    press = [a.url for a in ctx.fetch_attempts.for_company(s.run_id, thin)
             if a.source_class is SourceClass.PRESS]
    assert press == ["https://thin.example/press"]


def test_dev_docs_are_recorded_for_hooks_but_never_extracted():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [(a.url, a.outcome, a.document_count)
            for a in _good_attempts(ctx, s.run_id, SourceClass.DEV_DOCS)] == [
        ("https://good.example/docs", "http_error", 0),
        ("https://good.example/developers", "ok", 0),
    ]
    assert _good(ctx, SourceClass.DEV_DOCS) == []


def test_recent_github_repos_become_dated_first_party_evidence():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    docs = _good(ctx, SourceClass.GITHUB)
    assert [(d.url, d.published_at, d.publisher_domain) for d in docs] == [
        ("https://github.com/goodco/recon-worker", date(2026, 9, 15), "good.example")]
    assert [(a.url, a.outcome, a.document_count)
            for a in _good_attempts(ctx, s.run_id, SourceClass.GITHUB)] == [
        ("https://api.github.com/orgs/goodco/repos?sort=pushed&per_page=20", "ok", 1)]
    assert ctx.cache.read(docs[0].content_hash) == (
        "recon-worker: Streaming ledger reconciliation")


def test_the_status_page_is_no_longer_fetched():
    ctx = build_context()
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    for cid in (company_id(ctx, "good.example"), company_id(ctx, "thin.example")):
        assert not any(a.url.startswith("https://status.")
                       for a in ctx.fetch_attempts.for_company(s.run_id, cid))


def test_press_never_reads_blog_posts_its_index_links_to():
    """One post read as ENG_BLOG and again as PRESS would pose as two
    independent sources for the same claim."""
    ctx = build_context()
    real = ctx.fetcher.get

    def get(url):
        if url == "https://good.example/news":
            return FetchOutcome(url, 200, '<html><body><a href="/blog/engineering">Eng</a>'
                                '<a href="/blog/x">A post</a>'
                                '<a href="/news/seed-round">Seed</a></body></html>', "ok")
        return real(url)

    ctx.fetcher.get = get
    s = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert [d.url for d in _good(ctx, SourceClass.PRESS)] == [
        "https://good.example/news/seed-round"]
    assert not any("/blog/" in a.url for a in _good_attempts(ctx, s.run_id, SourceClass.PRESS))
