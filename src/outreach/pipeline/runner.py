from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

from outreach.contacts.quota import CreditBudget
from outreach.core.clustering import cluster_by_theme
from outreach.core.dedupe import canonical_domain
from outreach.core.gate import GateVerdict, evaluate
from outreach.core.ranking import rank_contacts
from outreach.core.stage import exceeds_cap
from outreach.core.workmode import posting_matches
from outreach.extraction.changelog import split_changelog
from outreach.extraction.extract import extract_and_persist
from outreach.extraction.htmltext import html_to_text
from outreach.extraction.pubdate import parse_published_date
from outreach.net.fetcher import FetchOutcome
from outreach.pipeline.context import RunContext
from outreach.sources.base import SurfaceTarget
from outreach.sources.surfaces.blog import find_blog_links
from outreach.sources.surfaces.github import find_github_org, parse_repos
from outreach.sources.surfaces.standard import surface_targets
from outreach.types import (ALL_WORK_MODES, Company, EvidenceItem, FetchAttempt, Finding,
                            PersonRef, PostingRef, SourceClass, SourceDocument,
                            WorkMode)

# The stage names this runner actually checkpoints, in the order it writes
# them. There is no separate "profile" checkpoint: profiling a company is
# the first half of the evidence stage and shares its fate, so a resume that
# trusted a "profile: ok" row would have nothing to skip.
STAGES = ("discover", "enrich", "evidence", "contacts", "synthesize")

# Surfaces that describe a company's CURRENT state rather than a dated
# archive. For these the fetch date is an honest publication date: a
# careers page or an about page says what is true today. A blog or press
# post, a changelog entry and a GitHub repo are dated artifacts and carry
# their own date -- from the post's markup, the entry's heading, the repo's
# last push. Where none can be read, `published_at` stays None and the gate
# treats the evidence as not fresh. That fails toward the no-bottleneck
# branch, which is the safe direction: we would rather stay silent about a
# company than claim stale pain is current.
CURRENT_STATE_CLASSES = frozenset({
    SourceClass.CAREERS_PAGE,
    SourceClass.JOB_POSTING,
    SourceClass.ABOUT,
})

# Fetches that reach a host other than the company's own, so answering says
# nothing about whether a guessed domain is real: a job posting usually lives
# on the board, and GitHub repos live on api.github.com.
_OFF_DOMAIN_CLASSES = frozenset({SourceClass.JOB_POSTING, SourceClass.GITHUB})


@dataclass
class RunSummary:
    run_id: int
    role_title: str = ""
    sector: str = ""
    companies: int = 0
    evidenced: int = 0
    no_findings: int = 0
    excluded_size: int = 0
    excluded_no_matching_posting: int = 0
    facts_fetched: int = 0
    facts_cached: int = 0
    # Companies per startup stage; filled once stage classification lands.
    stage_counts: dict[str, int] = field(default_factory=dict)
    quotes_accepted: int = 0
    quotes_rejected: int = 0
    skipped_quota: int = 0
    skipped_domain_unconfirmed: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)
    wall_clock_seconds: float | None = None
    credits_before: int | None = None
    credits_after: int | None = None


def run_pipeline(
    ctx: RunContext, role_title: str, sector: str, resume_run_id: int | None = None,
    work_modes: frozenset[WorkMode] = ALL_WORK_MODES,
) -> RunSummary:
    """Five checkpointed stages, per-company isolation. One bad domain never
    costs the others.

    Every per-company step is wrapped: a failure becomes a `failed` stage
    row and an entry in `summary.errors`, and the loop moves on.

    The only calls left unswallowed are the three that run BEFORE any
    per-company work and decide whether there is a run at all:
    `runs.create`, `llm.expand_titles` and `job_board.search`. If one of
    those fails there is no partial result to salvage, and a silently empty
    run would be worse than a traceback. Everything after them — including
    `contact_provider.remaining_credits` and the closing `runs.finish` — is
    guarded, because by then the run has already spent money and computed a
    summary that the caller must get back.
    """
    started = time.perf_counter()
    run_id = resume_run_id or ctx.runs.create(
        role_title, sector, ctx.config.discovery.region,
        (ctx.config.discovery.headcount_min, ctx.config.discovery.headcount_max),
        datetime.now(),
    )
    summary = RunSummary(run_id=run_id, role_title=role_title, sector=sector)

    # --- discover -------------------------------------------------------
    terms = ctx.llm.expand_titles(role_title)
    # A multi-board source swallows one board's failure so the others'
    # postings survive, and keeps the error for us to surface. Only this
    # search's errors are ours: the list accumulates across calls.
    board_errors = getattr(ctx.job_board, "errors", None)
    seen_errors = len(board_errors) if board_errors is not None else 0
    postings = ctx.job_board.search(terms, ctx.config.discovery.region)
    if board_errors is not None:
        summary.errors.extend(f"discover: {e}" for e in board_errors[seen_errors:])
    company_ids: list[int] = []
    postings_by_company: dict[int, list[PostingRef]] = {}
    for domain, group in _group_by_domain(postings, summary).items():
        matching = [p for p in group if posting_matches(p, work_modes)]
        try:
            company_id = ctx.companies.upsert(domain, group[0].company_name, None, None)
            if not matching:
                # Recorded, not dropped: the report lists who was left out
                # and why, so a too-narrow filter is visible rather than
                # looking like a board that found nothing.
                ctx.runs.set_stage(run_id, company_id, "discover",
                                   "excluded_no_matching_posting")
                summary.excluded_no_matching_posting += 1
                continue
            for posting in matching:
                ctx.postings.insert(run_id, company_id, posting)
            ctx.runs.set_stage(run_id, company_id, "discover", "ok")
        except Exception as exc:  # one unusable domain is not a dead run
            summary.failures += 1
            summary.errors.append(f"discover {domain}: {exc}")
            continue
        company_ids.append(company_id)
        postings_by_company[company_id] = matching
    summary.companies = len(company_ids)

    # One balance check, one budget, shared by every paid call in the run.
    # Enrichment spends first: a company's size decides whether it is worth
    # researching at all, and contacts for a company we then drop would be
    # credits spent on nobody.
    credit_error: Exception | None = None
    try:
        summary.credits_before = ctx.contact_provider.remaining_credits()
    except Exception as exc:
        # Asking the provider how much budget is left is a network call for
        # a real adapter. Losing it costs us every paid call this run, not
        # the evidence still to gather.
        credit_error = exc
        summary.errors.append(f"credits: {exc}")
    budget = CreditBudget(summary.credits_before or 0)

    # --- enrich (per company, isolated) ---------------------------------
    research_ids: list[int] = []
    for company_id in company_ids:
        status = ctx.runs.stage_status(run_id, company_id, "enrich")
        if status not in ("ok", "excluded_size"):
            try:
                status, error = _enrich(ctx, run_id, company_id, budget, summary)
                ctx.runs.set_stage(run_id, company_id, "enrich", status, error=error)
            except Exception as exc:
                # Enrichment only ever narrows or labels the list; a company
                # we failed to enrich is still researched, as size unknown.
                # It is not a `failures` entry: nothing about it was lost.
                status = "failed"
                summary.errors.append(f"enrich {company_id}: {exc}")
                try:
                    ctx.runs.set_stage(run_id, company_id, "enrich", "failed",
                                       error=str(exc))
                except Exception as stage_exc:
                    summary.errors.append(f"enrich {company_id}: {stage_exc}")
        if status == "excluded_size":
            # Known to be over the cap: dropped here, before a single LLM
            # call is spent researching it.
            summary.excluded_size += 1
            continue
        research_ids.append(company_id)

    # --- profile + evidence (per company, isolated) ---------------------
    for company_id in research_ids:
        if ctx.runs.stage_status(run_id, company_id, "evidence") == "ok":
            continue
        try:
            # This company's stage is about to be re-derived from scratch,
            # so drop whatever a previous partial attempt left behind.
            # `evidence_items` has no unique key, so without this a resume
            # doubles the rows and the report cites the same quote twice.
            # The homepage attempt belongs to the enrich stage, which is not
            # being re-run, and is what confirms the domain for contacts --
            # so it is kept.
            ctx.evidence.clear_for_company(run_id, company_id)
            ctx.fetch_attempts.clear_for_company(
                run_id, company_id, keep=frozenset({SourceClass.HOMEPAGE}))
            result = _profile_and_extract(ctx, run_id, company_id, summary,
                                          postings_by_company.get(company_id, ()))
            joined = "; ".join(result.errors)
            summary.errors.extend(f"evidence {company_id}: {e}" for e in result.errors)
            if result.completed or not result.errors:
                # At least one surface was reached. Partial evidence is
                # still evidence: a company whose changelog 500s but whose
                # blog answered has earned its trip to the gate.
                ctx.runs.set_stage(run_id, company_id, "evidence", "ok",
                                   error=joined or None)
            else:
                summary.failures += 1
                ctx.runs.set_stage(run_id, company_id, "evidence", "failed",
                                   error=joined)
        except Exception as exc:  # one company's failure is data, not a crash
            # Covers a failure anywhere above, including the `set_stage`
            # calls themselves: a locked database or a constraint violation
            # writing this company's checkpoint must not abort the run for
            # every company still waiting behind it.
            summary.failures += 1
            summary.errors.append(f"evidence {company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "evidence", "failed", error=str(exc))

    # --- contacts (quota-aware) -----------------------------------------
    # A Greenhouse board token is a guessed domain label, never a confirmed
    # one (see `sources/jobboards/greenhouse.py`): `acmecorp` is not
    # necessarily `acmecorp.com`. Enrichment must never run against a
    # domain no surface fetch actually reached -- an unconfirmed domain
    # that happens to belong to a DIFFERENT real company would come back
    # with that company's own verified staff, and the report would present
    # them under the target's name with a green "verified" badge. Requiring
    # at least one successful surface fetch before spending an enrichment
    # credit is cheap insurance against emailing the wrong company.
    still_pending = [c for c in research_ids
                     if ctx.runs.stage_status(run_id, c, "contacts") != "ok"]
    pending = []
    for company_id in still_pending:
        # A job-posting page is usually hosted by the job board, not the
        # company, and GitHub repos are read from api.github.com, so reaching
        # either says nothing about the guessed domain. The enrich stage's
        # homepage fetch does count: it is the domain.
        confirmed = any(a.outcome == "ok" and a.source_class not in _OFF_DOMAIN_CLASSES
                        for a in ctx.fetch_attempts.for_company(run_id, company_id))
        if confirmed:
            pending.append(company_id)
        else:
            try:
                ctx.runs.set_stage(run_id, company_id, "contacts",
                                   "skipped_domain_unconfirmed")
            except Exception as exc:
                summary.errors.append(f"contacts {company_id}: {exc}")
            summary.skipped_domain_unconfirmed += 1
    for company_id in pending:
        if credit_error is not None:
            # We never learned the balance: an outage, not a shortfall.
            # `skipped_quota` would tell the reader to buy credits they may
            # already have.
            summary.failures += 1
            try:
                ctx.runs.set_stage(run_id, company_id, "contacts", "failed",
                                   error=str(credit_error))
            except Exception as stage_exc:
                # Already handling one outage; a second fault writing this
                # company's checkpoint must not stop the rest of the pending
                # companies from at least being recorded as failed too.
                summary.errors.append(f"contacts {company_id}: {stage_exc}")
            continue
        if not budget.try_spend(1):
            # Running out of credits is a normal condition, not an error:
            # the report says `skipped — quota` rather than going quiet.
            ctx.runs.set_stage(run_id, company_id, "contacts", "skipped_quota")
            summary.skipped_quota += 1
            continue
        try:
            _resolve_contacts(ctx, company_id, role_title)
            ctx.runs.set_stage(run_id, company_id, "contacts", "ok")
        except Exception as exc:
            summary.failures += 1
            summary.errors.append(f"contacts {company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "contacts", "failed", error=str(exc))

    try:
        summary.credits_after = ctx.contact_provider.remaining_credits()
    except Exception as exc:
        # Diagnostics-only: the contacts stage itself already completed (or
        # already recorded its own failure) above, so losing this second
        # balance check must not touch anything the run already did.
        summary.errors.append(f"contacts credits_after: {exc}")

    # --- gate + synthesize ----------------------------------------------
    for company_id in research_ids:
        if ctx.runs.stage_status(run_id, company_id, "evidence") != "ok":
            # Research never completed for this company. Recording "no
            # bottleneck found" would be a verdict about work we did not
            # do; the failure is already on the record as a stage row.
            continue
        if ctx.runs.stage_status(run_id, company_id, "synthesize") == "ok":
            continue
        try:
            _synthesize(ctx, run_id, company_id, summary)
            ctx.runs.set_stage(run_id, company_id, "synthesize", "ok")
        except Exception as exc:
            summary.failures += 1
            summary.errors.append(f"synthesize {company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "synthesize", "failed",
                               error=str(exc))

    try:
        ctx.runs.finish(run_id, datetime.now())
    except Exception as exc:
        # The run is over and the summary is complete. Failing to stamp the
        # runs row is worth reporting, but losing the summary over it would
        # throw away everything the run just paid for.
        summary.errors.append(f"finish {run_id}: {exc}")
    summary.wall_clock_seconds = time.perf_counter() - started
    return summary


def _group_by_domain(
    postings: Sequence[PostingRef], summary: RunSummary
) -> dict[str, list[PostingRef]]:
    """Group postings by canonical domain, skipping the ones that cannot be.

    `dedupe_postings` canonicalizes the whole batch in one pass, so a single
    posting with an empty or hostless domain raises out of it and takes the
    entire run with it — before any company has been touched. Canonicalizing
    one posting at a time keeps that blast radius to the one bad posting.
    """
    grouped: dict[str, list[PostingRef]] = {}
    for posting in postings:
        try:
            domain = canonical_domain(posting.company_domain)
        except Exception as exc:
            summary.failures += 1
            summary.errors.append(
                f"discover {posting.company_name!r} "
                f"({posting.company_domain!r}): {exc}")
            continue
        grouped.setdefault(domain, []).append(posting)
    return grouped


def _enrich(ctx: RunContext, run_id: int, company_id: int, budget: CreditBudget,
            summary: RunSummary) -> tuple[str, str | None]:
    """Confirm the domain, find its GitHub org, and learn the company's size.

    Returns the enrich stage's (status, error). Every outcome but
    `excluded_size` keeps the company in the run: a size we could not
    learn is unknown, and unknown never drops a company.
    """
    company = ctx.companies.get(company_id)
    # Only the homepage row is this stage's to re-derive; the evidence
    # stage's rows for the same company stay (see `clear_for_company`).
    ctx.fetch_attempts.clear_for_company(
        run_id, company_id, only=frozenset({SourceClass.HOMEPAGE}))
    homepage = ctx.fetcher.get(f"https://{company.canonical_domain}/")
    _record_attempt(ctx, run_id, company_id, SourceClass.HOMEPAGE, homepage, 0)

    status, error = "ok", None
    if homepage.outcome != "ok":
        # The same guard as contacts: a guessed domain nobody answers on may
        # be someone else's, and their Hunter record would be the wrong
        # company's size -- enough to drop the right company on a stranger's
        # headcount. Research still runs; it is how the report shows why.
        status = "skipped_domain_unconfirmed"
    else:
        # A homepage that links no org is not proof the org is gone: a
        # JS-rendered page links nothing at all. Only a found org is written,
        # so one we learned earlier survives.
        github_org = find_github_org(homepage.body or "")
        if github_org:
            ctx.companies.set_github_org(company_id, github_org)
        status, error = _company_facts(ctx, company, budget, summary)

    company = ctx.companies.get(company_id)
    if exceeds_cap(company.headcount, company.headcount_band,
                   ctx.config.discovery.headcount_max):
        return "excluded_size", None
    return status, error


def _company_facts(ctx: RunContext, company: Company, budget: CreditBudget,
                   summary: RunSummary) -> tuple[str, str | None]:
    """Buy this company's facts unless the stored ones are still fresh."""
    fetched_at = company.facts_fetched_at
    if (fetched_at is not None and (ctx.today - fetched_at.date()).days
            < ctx.config.hunter.facts_ttl_days):
        summary.facts_cached += 1
        return "ok", None
    if not budget.try_spend(ctx.config.hunter.enrichment_cost):
        return "skipped_quota", None
    # Stamped with the run's date, not the wall clock, so the TTL check above
    # agrees with it and a run stays reproducible.
    stamp = datetime.combine(ctx.today, datetime.min.time())
    try:
        facts = ctx.facts_provider.company_facts(company.canonical_domain)
    except Exception as exc:
        # Hunter adapters raise errors carrying status and path only, never
        # the key, so the text is safe to keep on the stage row.
        summary.errors.append(f"enrich {company.id}: {exc}")
        return "failed", str(exc)
    if facts is None:
        # Hunter was asked and had nothing: remembered, so the next run
        # inside the TTL does not pay again for the same miss.
        ctx.companies.mark_facts_checked(company.id, stamp)
    else:
        ctx.companies.set_facts(company.id, facts, stamp)
    summary.facts_fetched += 1
    return "ok", None


@dataclass(frozen=True)
class _SurfaceSweep:
    """How a company's surfaces fared: how many were reached, and what broke."""
    completed: int
    errors: list[str]


def _profile_and_extract(ctx: RunContext, run_id: int, company_id: int,
                         summary: RunSummary,
                         postings: Sequence[PostingRef] = ()) -> _SurfaceSweep:
    company = ctx.companies.get(company_id)
    completed = 0
    errors: list[str] = []
    # Learned from the homepage by the enrich stage; see the skip record below.
    github_org = company.github_org
    for target in surface_targets(company.canonical_domain, github_org=github_org):
        try:
            _sweep_surface(ctx, run_id, company_id, company.canonical_domain,
                           target, summary)
        except Exception as exc:
            # One surface is not the company. A blog that raises must not
            # discard the changelog evidence that would have cleared the
            # gate on its own.
            errors.append(f"{target.source_class.value}: {exc}")
            continue
        completed += 1

    try:
        _sweep_postings(ctx, run_id, company_id, company.canonical_domain,
                        postings, summary)
    except Exception as exc:
        # Postings are extra corroboration, not a surface: losing them must
        # not count against the company or discard the surfaces already read.
        errors.append(f"job_posting: {exc}")

    if github_org is None:
        # `surface_targets` silently leaves GitHub out of the list whenever
        # the homepage linked no org. Recording that as a FetchAttempt
        # (rather than nothing at all) makes the gap show up in the report's
        # coverage log instead of looking like GitHub was never even
        # considered.
        try:
            ctx.fetch_attempts.insert(run_id, FetchAttempt(
                company_id, SourceClass.GITHUB, "", "skipped_no_github_org", None, 0))
        except Exception as exc:
            errors.append(f"github: {exc}")

    return _SurfaceSweep(completed=completed, errors=errors)


def _sweep_surface(ctx: RunContext, run_id: int, company_id: int, domain: str,
                   target: SurfaceTarget, summary: RunSummary) -> None:
    cls = target.source_class
    page = _fetch_surface(ctx, run_id, company_id, target)
    if page is None:
        return
    discovery = ctx.config.discovery
    if cls is SourceClass.ENG_BLOG:
        _sweep_blog(ctx, run_id, company_id, domain, cls, page, summary,
                    limit=discovery.max_blog_posts)
    elif cls is SourceClass.PRESS:
        # A press page is a blog in all but name: an index of dated posts.
        _sweep_blog(ctx, run_id, company_id, domain, cls, page, summary,
                    limit=discovery.max_press_posts)
    elif cls is SourceClass.CHANGELOG:
        _sweep_changelog(ctx, run_id, company_id, domain, page, summary)
    elif cls is SourceClass.DEV_DOCS:
        # Recorded, never extracted: API reference says what a company ships
        # to developers -- a hook for the email -- but nothing about where it
        # hurts, and it would only feed the LLM pages of endpoint tables.
        _record_attempt(ctx, run_id, company_id, cls, page, 0)
    elif cls is SourceClass.GITHUB:
        _sweep_github(ctx, run_id, company_id, domain, page, summary)
    else:
        _ingest(ctx, run_id, company_id, domain, cls, page, summary)


def _fetch_surface(ctx: RunContext, run_id: int, company_id: int,
                   target: SurfaceTarget) -> FetchOutcome | None:
    """The target's first URL that answers with a page, or None if none does.

    Alternates are tried in order only after the primary misses (a site's
    press page may live at /news). Every miss is recorded as it happens; the
    page that answers is returned unrecorded, since how many documents it
    yields is known only once it has been read.
    """
    for url in (target.url, *target.alternates):
        page = ctx.fetcher.get(url)
        if page.outcome == "ok" and page.body:
            return page
        _record_attempt(ctx, run_id, company_id, target.source_class, page, 0)
    return None


def _record_attempt(ctx: RunContext, run_id: int, company_id: int,
                    source_class: SourceClass, outcome: FetchOutcome,
                    documents: int) -> None:
    ctx.fetch_attempts.insert(run_id, FetchAttempt(
        company_id, source_class, outcome.url, outcome.outcome, outcome.status,
        documents))


def _ingest(ctx: RunContext, run_id: int, company_id: int, domain: str,
            source_class: SourceClass, outcome: FetchOutcome, summary: RunSummary,
            page_dated: bool = False) -> None:
    """Store one fetched page and extract its claims.

    `page_dated` reads the publication date out of the page's own markup,
    for individual posts; without it the date is the fetch date for
    current-state surfaces and None for everything else.
    """
    # Both the LLM and the substring guard must see the SAME text, or an
    # honest quote that merely crosses an inline tag (or carries an entity
    # like &rsquo;) is rejected, while a quote that literally preserves
    # markup can survive and render as `&lt;strong&gt;` in the report the
    # user pastes into an email. `outcome.body` is raw HTTP response text --
    # extract plain prose from it before either side ever looks at it.
    text = html_to_text(outcome.body)
    if page_dated:
        published_at = parse_published_date(outcome.body)
    else:
        published_at = ctx.today if source_class in CURRENT_STATE_CLASSES else None
    # Recorded before extraction, as it always was: a page that answered
    # confirms the domain even if the LLM call on it then fails.
    _record_attempt(ctx, run_id, company_id, source_class, outcome, 1)
    _ingest_text(ctx, run_id, company_id, domain, source_class, outcome.url,
                 outcome.status, text, published_at, summary)


def _ingest_text(ctx: RunContext, run_id: int, company_id: int, domain: str,
                 source_class: SourceClass, url: str, status: int | None,
                 text: str, published_at: date | None, summary: RunSummary) -> None:
    """Store one document's plain text and extract its claims.

    `text` is exactly what the LLM reads and what the substring guard checks
    quotes against. The caller records the fetch attempt: one fetch can
    yield several documents (changelog entries, repos).
    """
    content_hash = ctx.cache.store(text.encode("utf-8"))
    doc = SourceDocument(None, company_id, url, source_class,
                         domain, published_at, datetime.now(),
                         status or 200, content_hash)
    doc_id = ctx.documents.insert(doc)

    result = extract_and_persist(run_id, doc, text, ctx.llm,
                                 ctx.evidence, document_id=doc_id)
    summary.quotes_accepted += result.accepted
    summary.quotes_rejected += result.rejected


def _sweep_changelog(ctx: RunContext, run_id: int, company_id: int, domain: str,
                     page: FetchOutcome, summary: RunSummary) -> None:
    """Read each dated release as its own document, newest first.

    A changelog page spans years; read whole, its one date would be the
    fetch date or none, and a two-year-old workaround would sit beside last
    week's fix. Split into entries, each claim carries the date of the
    release that made it. A page with no dated headings is read whole and
    undated, as before, so an unusual layout is not lost.
    """
    entries = split_changelog(page.body or "")
    if not entries:
        _ingest(ctx, run_id, company_id, domain, SourceClass.CHANGELOG, page, summary)
        return
    entries = entries[:ctx.config.discovery.max_changelog_entries]
    _record_attempt(ctx, run_id, company_id, SourceClass.CHANGELOG, page, len(entries))
    for entry in entries:
        # Two entries on one day share this url; their text differs, and
        # documents are keyed on (url, content_hash), so both are kept.
        _ingest_text(ctx, run_id, company_id, domain, SourceClass.CHANGELOG,
                     f"{page.url}#{entry.published_at.isoformat()}", page.status,
                     f"{entry.heading}\n{entry.text}", entry.published_at, summary)


def _sweep_github(ctx: RunContext, run_id: int, company_id: int, domain: str,
                  page: FetchOutcome, summary: RunSummary) -> None:
    """Read the org's recently pushed repos, each dated by its last push.

    The publisher is still the company: the org was found on its own
    homepage, so a repo there is first-party evidence of what it is building.
    """
    repos = parse_repos(page.body or "", ctx.today, ctx.config.gate.recency_days,
                        ctx.config.discovery.max_github_repos)
    _record_attempt(ctx, run_id, company_id, SourceClass.GITHUB, page, len(repos))
    for repo in repos:
        _ingest_text(ctx, run_id, company_id, domain, SourceClass.GITHUB, repo.url,
                     page.status, f"{repo.name}: {repo.description}", repo.pushed_at,
                     summary)


def _sweep_blog(ctx: RunContext, run_id: int, company_id: int, domain: str,
                cls: SourceClass, index: FetchOutcome, summary: RunSummary,
                limit: int) -> None:
    """Read individual posts, not the index's front page.

    A blog index is a wall of teasers with no dates, and mixes marketing in
    with engineering writing. The posts carry the substance and their own
    publication dates, and most blogs have an engineering section that
    holds the ones worth reading. If no post links can be found the index
    itself is read, as before, so a blog with unusual markup is not lost.
    Press pages are read the same way, at most `limit` posts either way.

    `index` is the page that answered, which may be an alternate: post
    links are resolved against it, so /news finds its /news/... posts.
    """
    links = find_blog_links(index.body or "", index.url)
    candidates = links.posts
    discovery = [index]
    if links.engineering_index:
        eng = ctx.fetcher.get(links.engineering_index)
        discovery.append(eng)
        if eng.outcome == "ok" and eng.body:
            eng_posts = find_blog_links(eng.body, links.engineering_index).posts
            if eng_posts:
                candidates = eng_posts

    if not candidates:
        for page in discovery[1:]:
            _record_attempt(ctx, run_id, company_id, cls, page, 0)
        _ingest(ctx, run_id, company_id, domain, cls, index, summary)
        return

    for page in discovery:
        _record_attempt(ctx, run_id, company_id, cls, page, 0)

    # Listed posts can 404 (unpublished cards, moved URLs), so keep going past
    # failures, but never spend more than twice the budget doing it.
    read = tried = 0
    for url in candidates:
        if read >= limit or tried >= 2 * limit:
            break
        tried += 1
        post = ctx.fetcher.get(url)
        if post.outcome == "ok" and post.body:
            _ingest(ctx, run_id, company_id, domain, cls, post, summary, page_dated=True)
            read += 1
        else:
            _record_attempt(ctx, run_id, company_id, cls, post, 0)


def _sweep_postings(ctx: RunContext, run_id: int, company_id: int, domain: str,
                    postings: Sequence[PostingRef], summary: RunSummary) -> None:
    """Read the postings this company was discovered through.

    A posting for the very role being researched is first-party, current
    evidence, and a different source class from a blog post -- which is what
    lets a bottleneck be corroborated at all.
    """
    seen: set[str] = set()
    for posting in postings:
        if len(seen) >= ctx.config.discovery.max_job_postings:
            break
        if posting.url in seen:
            continue
        seen.add(posting.url)
        outcome = ctx.fetcher.get(posting.url)
        if outcome.outcome != "ok" or not outcome.body:
            _record_attempt(ctx, run_id, company_id, SourceClass.JOB_POSTING, outcome, 0)
            continue
        _ingest(ctx, run_id, company_id, domain, SourceClass.JOB_POSTING, outcome, summary)


def _resolve_contacts(ctx: RunContext, company_id: int, role_title: str) -> None:
    company = ctx.companies.get(company_id)
    keywords = role_title.lower().split()
    people = ctx.contact_provider.find(company.canonical_domain, keywords)
    for person, _score in rank_contacts(people, company.headcount, keywords,
                                        ctx.config.ranking):
        # `find()`'s own reported status is never trusted here, even if it
        # says "verified" -- domain search only tells you an address
        # exists, not that it was confirmed deliverable. Treating find()'s
        # status as authoritative would make the verified-email invariant
        # hold by adapter convention rather than pipeline requirement: a
        # one-character bug in an adapter's mapping (or a fake in a test)
        # could ship guessed addresses as verified with nothing here to
        # catch it. The ONLY routes to "verified" are `verify()` and an
        # address already confirmed and billed in a prior run.
        prior = ctx.contacts.already_resolved(company_id, person.full_name)
        confirmed = (prior.email if prior is not None
                     and prior.email_status == "verified" else None)
        if confirmed and person.email in (None, confirmed):
            # This exact address was already confirmed and billed in an
            # earlier run. Verification is charged per address, so
            # re-asking buys the same answer twice — and a provider that
            # has gone quiet must not erase what we paid to learn.
            status, email = "verified", confirmed
        elif person.email:
            status = ctx.contact_provider.verify(person.email)
            email = person.email if status == "verified" else None
        else:
            # No address to verify at all.
            status, email = "not_found", None

        resolved = PersonRef(
            full_name=person.full_name, title=person.title,
            profile_url=person.profile_url, email=email, email_status=status,
        )
        ctx.contacts.upsert(company_id, resolved, "provider", datetime.now())


def _synthesize(ctx: RunContext, run_id: int, company_id: int,
                summary: RunSummary) -> None:
    """Pick the strongest theme that clears the gate, or record why none did.

    The gate is evaluated per theme, never across the whole pile: two
    unrelated complaints from two surfaces are not one corroborated
    bottleneck, and evaluating them together would manufacture the
    independence the gate exists to demand.
    """
    items = ctx.evidence.for_company(run_id, company_id)
    passing: list[tuple[str, list[EvidenceItem], GateVerdict]] = []
    for theme, cluster in cluster_by_theme(items).items():
        verdict = evaluate(cluster, ctx.config.gate, ctx.today)
        if verdict.passed:
            passing.append((theme, cluster, verdict))

    # Most corroborated theme wins; the theme name breaks ties so two runs
    # over the same evidence always pick the same bottleneck.
    best = min(passing, key=lambda t: (-len(t[1]), t[0]), default=None)

    # Still the single-verdict shape of the old bottleneck: one passing
    # theme, or one row saying why none passed.
    if best is None:
        ctx.findings.insert(run_id, Finding(
            None, company_id, "", "", "", False, False, _failure_reason(ctx, items), ()))
        summary.no_findings += 1
        return

    theme, cluster, verdict = best
    quotes = [i.quote for i in cluster]
    ctx.findings.insert(run_id, Finding(
        None, company_id, theme, cluster[0].claim,
        ctx.llm.write_summary(cluster[0].claim, quotes),
        corroborated=False, passed=True, reason=verdict.reason,
        evidence_ids=verdict.evidence_ids))
    summary.evidenced += 1


def _failure_reason(ctx: RunContext, items: Sequence[EvidenceItem]) -> str:
    """Why no theme cleared the gate, in terms the report can print.

    Re-evaluating every item together gives the common case a useful
    reason ("no_evidence", "no_first_party_source", ...). But the union can
    clear a gate that no single theme cleared — two themes of one item each
    look like two independent sources when piled together — and echoing
    that verdict's "passed" as the reason a company FAILED is exactly the
    mixing artifact the per-theme evaluation is there to prevent. Name it
    instead.
    """
    combined = evaluate(items, ctx.config.gate, ctx.today)
    if combined.passed:
        return "evidence_split_across_themes"
    return combined.reason
