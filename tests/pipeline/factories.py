from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

import httpx

from outreach.config import (Config, DiscoveryConfig, GateConfig, PathsConfig,
                             RankingConfig)
from outreach.contacts.facts import FakeFactsProvider
from outreach.contacts.fake import FakeContactProvider
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import (EvidenceRepo, FetchAttemptRepo, FindingRepo,
                                 PostingRepo, RunRepo)
from outreach.types import CompanyFacts, PersonRef, PostingRef

TODAY = date(2026, 9, 21)

# The careers page is a CURRENT-STATE surface. It exists because evidence
# dating is class-dependent: good.example's blog index states no date, so it
# is never "fresh" and a company evidenced only by it fails the gate's
# recency rule. A company that is actively hiring has a careers page, and its
# fetch date is an honest publication date, so this is what makes
# good.example's cluster both independent AND current.
GOOD_CAREERS = ("<html><body><p>You will own the reconciliation pipeline that "
                "currently runs overnight and is the team's tightest "
                "constraint.</p></body></html>")
GOOD_BLOG = ("<html><body><p>Our nightly reconciliation job now regularly "
             "exceeds its 6-hour window.</p></body></html>")
# Two dated releases: each becomes its own document, dated from its heading.
# The quote the fake LLM offers lives in the newer one.
GOOD_CHANGELOG = ("<html><body>"
                  "<h2>2026-09-12</h2><p>recon-worker: increase lock timeout to 900s "
                  "(temporary)</p>"
                  "<h2>August 1, 2026</h2><p>Exports now stream instead of "
                  "buffering.</p></body></html>")
# good.example has no /press: its press index lives at /news, one dated post.
GOOD_NEWS = ('<html><body><a href="/news/seed-round">We raised a seed round</a>'
             "</body></html>")
GOOD_NEWS_POST = ('<html><head><script type="application/ld+json">'
                  '{"datePublished":"2026-09-02"}</script></head><body>'
                  "<p>Good Co raises a seed round to build real-time "
                  "reconciliation.</p></body></html>")
GOOD_ABOUT = ("<html><body><p>Good Co builds reconciliation software for "
              "finance teams.</p></body></html>")
# Dev docs are hooks, never evidence: /docs is absent, /developers answers.
GOOD_DEV_DOCS = "<html><body><p>API reference: POST /v1/reconciliations</p></body></html>"
GOOD_REPOS = json.dumps([{
    "name": "recon-worker", "description": "Streaming ledger reconciliation",
    "html_url": "https://github.com/goodco/recon-worker",
    "pushed_at": "2026-09-15T12:00:00Z", "fork": False, "archived": False,
}])
THIN_PAGE = "<html><body><p>We build payments software.</p></body></html>"
# The homepage is what the enrich stage fetches to confirm a domain and to
# find the GitHub org it links to. Never ingested, so it carries no quote.
GOOD_HOME = ('<html><body><a href="https://github.com/goodco">GitHub</a>'
             "</body></html>")

CONFIG = Config(
    gate=GateConfig(1, True, 180, frozenset({
        "job_posting", "careers_page", "eng_blog", "changelog", "github",
        "status_page", "press", "about"})),
    ranking=RankingConfig(3, ("recruit", "talent", "sourcer")),
    discovery=DiscoveryConfig(1, 2000, "US", ()),
    paths=PathsConfig(Path("db"), Path("cache"), Path("reports")),
)


def _transport(explode_on_domain: str | None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(404)
        if explode_on_domain and explode_on_domain in url:
            # Deliberately NOT an httpx error: Fetcher.get catches
            # httpx.HTTPError by design and converts it into a
            # `network_error` outcome, which the runner handles as data. A
            # non-HTTP error is the only way to reach the runner's
            # per-company `except Exception`, which is the isolation
            # boundary this fixture exists to test.
            raise RuntimeError("boom")
        if url == "https://good.example/":
            return httpx.Response(200, text=GOOD_HOME)
        if "good.example/careers" in url:
            return httpx.Response(200, text=GOOD_CAREERS)
        if "good.example/blog" in url:
            return httpx.Response(200, text=GOOD_BLOG)
        if "good.example/changelog" in url:
            return httpx.Response(200, text=GOOD_CHANGELOG)
        if url == "https://good.example/news":
            return httpx.Response(200, text=GOOD_NEWS)
        if url == "https://good.example/news/seed-round":
            return httpx.Response(200, text=GOOD_NEWS_POST)
        if url == "https://good.example/about":
            return httpx.Response(200, text=GOOD_ABOUT)
        if url == "https://good.example/developers":
            return httpx.Response(200, text=GOOD_DEV_DOCS)
        if url.startswith("https://api.github.com/orgs/goodco/repos"):
            return httpx.Response(200, text=GOOD_REPOS)
        if "thin.example" in url:
            return httpx.Response(200, text=THIN_PAGE)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def build_context(
    credits: int = 10,
    explode_on_domain: str | None = None,
    include_invented_quote: bool = False,
    include_malformed_posting: bool = False,
    include_unconfirmed_domain: bool = False,
    facts: dict[str, CompanyFacts] | None = None,
    facts_fail_on: frozenset[str] = frozenset(),
) -> RunContext:
    root = Path(tempfile.mkdtemp())
    conn = connect(root / "t.db")

    claims = [
        RawClaim("reconciliation is the team's tightest constraint",
                 "the reconciliation pipeline that currently runs overnight and "
                 "is the team's tightest constraint",
                 "active-build"),
        RawClaim("reconciliation exceeds its window",
                 "Our nightly reconciliation job now regularly exceeds its 6-hour window.",
                 "active-build"),
        RawClaim("lock timeout raised as a stopgap",
                 "recon-worker: increase lock timeout to 900s (temporary)",
                 "active-build"),
    ]
    if include_invented_quote:
        claims.append(RawClaim("they are moving to Kubernetes",
                               "We are migrating everything to Kubernetes.",
                               "infra-migration"))

    postings = []
    if include_malformed_posting:
        # A board that returns a posting with no usable domain at all.
        # Deliberately FIRST, so a run that canonicalizes the batch in one
        # pass dies before it reaches either healthy company.
        postings.append(
            PostingRef("Broken Co", "", "Backend Engineer", "u0", "Remote"))
    postings += [
        PostingRef("Good Co", "good.example", "Backend Engineer", "u1", "Chicago, IL",
                   work_mode="unknown", employment_type="full_time"),
        PostingRef("Thin Co", "thin.example", "Backend Engineer", "u2", "Austin, TX",
                   work_mode="onsite", employment_type="full_time"),
    ]
    if explode_on_domain:
        postings.append(
            PostingRef("Bad Co", explode_on_domain, "Backend Engineer", "u3", "NYC, NY"))
    if include_unconfirmed_domain:
        # A Greenhouse token that does not match the company's real domain:
        # every surface 404s via the factory's catch-all, so no fetch ever
        # confirms "ghost.example" -- exactly the guessed-wrong-domain case.
        postings.append(
            PostingRef("Ghost Co", "ghost.example", "Backend Engineer", "u9", "NYC, NY"))

    return RunContext(
        config=CONFIG, today=TODAY,
        companies=CompanyRepo(conn), contacts=ContactRepo(conn),
        documents=DocumentRepo(conn), runs=RunRepo(conn), evidence=EvidenceRepo(conn),
        findings=FindingRepo(conn), postings=PostingRepo(conn),
        fetch_attempts=FetchAttemptRepo(conn),
        cache=DocumentCache(root / "cache"),
        fetcher=Fetcher(httpx.Client(transport=_transport(explode_on_domain)),
                        sleep=lambda seconds: None),
        llm=FakeLLM(claims=claims, titles=["backend engineer"]),
        job_board=FakeJobBoardSource(postings),
        contact_provider=FakeContactProvider(
            # `find()` never returns "verified" here -- real domain search
            # (Hunter's included) only reports that an address exists, so
            # the fixture mirrors that contract rather than short-circuiting
            # it. Every one of these is expected to reach `verify()`; see
            # `test_the_pipeline_never_trusts_finds_self_reported_verified`.
            people_by_domain={
                "good.example": [
                    PersonRef("Marisol Okonkwo", "Co-founder & CTO", None,
                              "m@good.example", "unverified"),
                    PersonRef("Rita Sourcer", "Technical Recruiter", None, None,
                              "not_found"),
                ],
                "thin.example": [
                    PersonRef("Tomas Reyes", "Head of Engineering", None,
                              "t@thin.example", "unverified"),
                ],
                "ghost.example": [
                    PersonRef("Some Stranger", "VP Engineering", None,
                              "s@ghost.example", "unverified"),
                ],
            },
            credits=credits,
        ),
        facts_provider=FakeFactsProvider(facts or {}, fail_on=facts_fail_on),
    )


def company_id(ctx: RunContext, domain: str) -> int:
    """The id a run gave `domain`, looked up rather than upserted so a test
    cannot accidentally create the company it is asserting about."""
    row = ctx.companies.conn.execute(
        "SELECT id FROM companies WHERE canonical_domain = ?", (domain,)).fetchone()
    assert row is not None, f"{domain} was never discovered"
    return int(row["id"])
