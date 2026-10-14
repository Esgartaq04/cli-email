"""A job run: companies hiring for a role, from job boards, HN and Hunter, and contacts bought at them.

Finding the companies only reads. It searches, fetches and checks, but writes
nothing, so a dry run can use it and a search that fails leaves no half-started
run behind. `run_jobs` records what was found, in the order it will be bought
at, and a resume reads that record back instead of searching again.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Sequence

from outreach.config import PeopleList
from outreach.core.dedupe import canonical_domain
from outreach.core.filters import posting_in_location
from outreach.core.workmode import posting_matches
from outreach.pipeline.contacts import Candidate, buy_contacts, restore_candidates
from outreach.pipeline.context import RunContext
from outreach.pipeline.discovery import (HNCandidate, candidate_posting, hn_board_source,
                                         select_hn_candidates)
from outreach.pipeline.summary import RunSummary
from outreach.sources.companies import CompanyQuery, FoundCompany
from outreach.types import (CompanyInfo, HNPostRecord, Location, PostingRef, RunFilters,
                            WorkMode)

SOURCES = ("board", "hn", "hunter_hiring")
# What a company left out because of the user's filters is recorded as; a company
# over the HN cap (`skipped_cap`) is left out too, but not by anything the user asked.
_LEFT_OUT_BY_FILTERS = ("left_out_filters", "excluded_no_matching_posting")


@dataclass(frozen=True)
class JobProspect:
    """A company a job run found, with what it needs to be recorded and bought at.

    `fit` is how it stands against the run's sector and size: "unknown" is a
    company Hunter has no data on, which is kept. A company with a `left_out`
    reason is recorded but never bought at. `guessed_domain` is a domain nobody
    has confirmed -- a job board's token -- so its homepage is checked first.
    """
    domain: str
    name: str
    source: str  # "board" | "hn" | "hunter_hiring"
    postings: tuple[PostingRef, ...] = ()
    hn: HNCandidate | None = None
    hn_status: str | None = None  # "kept" | "skipped_cap", for an HN company
    sector: str | None = None
    size: str | None = None
    fit: str = "fits"  # "fits" | "unknown" | "no"
    left_out: str | None = None  # "excluded_no_matching_posting" | "left_out_filters" | "skipped_cap"
    guessed_domain: bool = False


def find_job_prospects(ctx: RunContext, terms: Sequence[str], filters: RunFilters,
                       work_modes: frozenset[WorkMode], summary: RunSummary, *,
                       use_hn: bool = True, dry_run: bool = False) -> list[JobProspect]:
    """The companies hiring for `terms`, in the order a run buys at them. Writes nothing.

    A dry run makes no LLM call and does not search the job boards HN posts link
    to, so every HN company then uses its post as its posting; the companies
    found are the same either way. `summary` gets the HN funnel and the counts.
    """
    prospects = _board_prospects(ctx, terms, filters, work_modes, summary)
    if use_hn and ctx.hn is not None and ctx.config.hn.enabled:
        prospects += _hn_prospects(ctx, terms, filters, work_modes, summary,
                                   frozenset(p.domain for p in prospects), dry_run)
    prospects += _hiring_prospects(ctx, terms, filters, summary, {p.domain for p in prospects})
    ordered = _in_order(_checked(ctx, filters, prospects, summary))

    summary.found = {source: sum(1 for p in ordered if p.source == source and p.left_out is None)
                     for source in SOURCES}
    summary.left_out = sum(1 for p in ordered if p.left_out in _LEFT_OUT_BY_FILTERS)
    return ordered


def _wanted(posting: PostingRef, work_modes: frozenset[WorkMode], where: Location) -> bool:
    """Whether a posting is in a work mode and a place the run asked for."""
    return (posting_matches(posting, work_modes)
            and posting_in_location(posting.location, posting.work_mode, where))


# --- 1. job boards ------------------------------------------------------------------

def _board_prospects(ctx: RunContext, terms: Sequence[str], filters: RunFilters,
                     work_modes: frozenset[WorkMode], summary: RunSummary) -> list[JobProspect]:
    # A multi-board source swallows one board's failure so the others' postings
    # survive, and keeps the error for us to surface. Its list accumulates across
    # searches, so only what this search added is ours.
    board_errors = getattr(ctx.job_board, "errors", None)
    seen_errors = len(board_errors) if board_errors is not None else 0
    postings = ctx.job_board.search(terms, filters.location.country)
    if board_errors is not None:
        summary.errors.extend(f"discover: {e}" for e in board_errors[seen_errors:])

    groups: dict[str, list[PostingRef]] = {}
    for posting in postings:
        try:
            domain = canonical_domain(posting.company_domain)
        except ValueError as exc:
            summary.errors.append(f"discover {posting.company_name}: {exc}")
            continue
        groups.setdefault(domain, []).append(posting)

    prospects = []
    for domain, group in groups.items():
        matching = tuple(p for p in group if _wanted(p, work_modes, filters.location))
        prospects.append(JobProspect(
            domain, group[0].company_name, "board", matching, guessed_domain=True,
            left_out=None if matching else "excluded_no_matching_posting"))
    return prospects


# --- 2. HN "Who is hiring?" ----------------------------------------------------------

def _hn_prospects(ctx: RunContext, terms: Sequence[str], filters: RunFilters,
                  work_modes: frozenset[WorkMode], summary: RunSummary,
                  board_domains: frozenset[str], dry_run: bool) -> list[JobProspect]:
    # HN is additive: whatever goes wrong there, the board companies are still bought at.
    try:
        selection = select_hn_candidates(ctx, terms, work_modes, board_domains, summary,
                                         allow_llm=not dry_run, location=filters.location)
        postings = _hn_postings(ctx, terms, filters, work_modes, selection.kept, summary, dry_run)
    except Exception as exc:
        summary.errors.append(f"hn: {exc}")
        return []
    # A company a board already found (`selection.known`) keeps the board's posting.
    kept = [JobProspect(c.parsed.domain, c.parsed.company, "hn", postings[c.parsed.domain],
                        hn=c, hn_status="kept") for c in selection.kept]
    skipped = [JobProspect(c.parsed.domain, c.parsed.company, "hn", hn=c, hn_status="skipped_cap",
                           left_out="skipped_cap") for c in selection.skipped_cap]
    return kept + skipped


def _hn_postings(ctx: RunContext, terms: Sequence[str], filters: RunFilters,
                 work_modes: frozenset[WorkMode], kept: Sequence[HNCandidate],
                 summary: RunSummary, dry_run: bool) -> dict[str, tuple[PostingRef, ...]]:
    """Each kept HN company's postings: those on the board its post links that
    pass the filters, or else the post itself."""
    found: dict[str, list[PostingRef]] = {c.parsed.domain: [] for c in kept}
    with_board = [c for c in kept if c.parsed.ats is not None]
    if with_board and not dry_run:
        # A board token's domain is a guess ("acmehq" -> acmehq.com); the post
        # links the company's real site, so its board postings take that instead.
        by_guess = {canonical_domain(f"{c.parsed.ats[1]}.com"): c for c in with_board}
        board = hn_board_source(ctx.fetcher, with_board)
        for posting in board.search(terms, filters.location.country):
            candidate = by_guess.get(canonical_domain(posting.company_domain))
            if candidate is None:
                continue
            posting = replace(posting, company_domain=candidate.parsed.domain,
                              company_name=candidate.parsed.company)
            if _wanted(posting, work_modes, filters.location):
                found[candidate.parsed.domain].append(posting)
        summary.errors.extend(f"hn: {e}" for e in board.errors)
    # No board, or a board without a posting that fits: the post is the posting.
    return {c.parsed.domain: tuple(found[c.parsed.domain] or [candidate_posting(c)]) for c in kept}


# --- 3. Hunter's hiring search -------------------------------------------------------

def _hiring_prospects(ctx: RunContext, terms: Sequence[str], filters: RunFilters,
                      summary: RunSummary, seen: set[str]) -> list[JobProspect]:
    """Companies Hunter says are hiring, other than those already found: a company
    found both ways keeps the posting it was found by."""
    if ctx.company_search is None:
        return []
    # `filters.location` is passed as is: its default is the US, whereas
    # None would ask Hunter for companies anywhere.
    query = CompanyQuery(filters.sectors, filters.sizes, filters.location,
                         hiring_titles=tuple(terms))
    try:
        found = ctx.company_search.search(query)
    except Exception as exc:
        summary.errors.append(f"hunter: {exc}")
        return []
    prospects = []
    for company in found:
        if company.domain not in seen:
            seen.add(company.domain)
            prospects.append(JobProspect(company.domain, company.name, "hunter_hiring",
                                         sector=company.sector, size=company.size))
    return prospects


# --- 4. sector and size --------------------------------------------------------------

def _checked(ctx: RunContext, filters: RunFilters, prospects: list[JobProspect],
             summary: RunSummary) -> list[JobProspect]:
    """Set the board and HN companies still in the running against the sector and size.

    Hunter's hiring search was already narrowed by them, so its companies are not asked again.
    """
    if not (filters.sectors or filters.sizes):
        return prospects
    to_check = {p.domain for p in prospects if p.source != "hunter_hiring" and p.left_out is None}
    if not to_check:
        return prospects
    answers: dict[str, FoundCompany | None] = {}
    if ctx.company_search is not None:
        try:
            answers = ctx.company_search.check(
                [p.domain for p in prospects if p.domain in to_check],
                CompanyQuery(filters.sectors, filters.sizes, filters.location))
        except Exception as exc:
            # No answer is not "does not fit": every company stays, as unknown.
            summary.errors.append(f"hunter check: {exc}")
    return [_fitted(p, answers) if p.domain in to_check else p for p in prospects]


def _fitted(prospect: JobProspect, answers: dict[str, FoundCompany | None]) -> JobProspect:
    if prospect.domain not in answers:
        return replace(prospect, fit="unknown")
    found = answers[prospect.domain]
    if found is None:
        return replace(prospect, fit="no", left_out="left_out_filters")
    return replace(prospect, sector=found.sector, size=found.size)


# --- 5. order ------------------------------------------------------------------------

def _in_order(prospects: list[JobProspect]) -> list[JobProspect]:
    """Companies that fit first, then those that may, then those left out; each
    group keeps the order they were found in (boards, HN, Hunter)."""
    kept = [p for p in prospects if p.left_out is None]
    return ([p for p in kept if p.fit == "fits"] + [p for p in kept if p.fit == "unknown"]
            + [p for p in prospects if p.left_out is not None])


# --- the run -------------------------------------------------------------------------

def run_jobs(ctx: RunContext, role: str, filters: RunFilters, work_modes: frozenset[WorkMode],
             people: PeopleList, *, use_hn: bool = True,
             resume_run_id: int | None = None) -> RunSummary:
    """Find companies hiring for `role`, buy contacts at them for the `people` list, and finish the run.

    With `resume_run_id` the run's recorded companies are bought at again; the
    role is not expanded and nothing is searched. A search that fails raises
    before anything is stored.
    """
    if resume_run_id is None:
        terms = ctx.llm.expand_titles(role)
        summary = RunSummary(run_id=0)  # there is no run to number until discovery is done
        prospects = find_job_prospects(ctx, terms, filters, work_modes, summary,
                                       use_hn=use_hn)
        run_id = ctx.runs.start("jobs", role, filters, people.name, datetime.now())
        summary.run_id = run_id
        candidates = _record(ctx, run_id, prospects, summary)
    else:
        info = ctx.runs.info(resume_run_id)
        if info is None or info.kind != "jobs":
            raise ValueError(f"run {resume_run_id} is not a job run")
        run_id = resume_run_id
        summary = RunSummary(run_id)
        candidates = restore_candidates(ctx, run_id)

    buy_contacts(ctx, run_id, people, candidates, summary)
    ctx.runs.finish(run_id, datetime.now())
    return summary


def _record(ctx: RunContext, run_id: int, prospects: Sequence[JobProspect],
            summary: RunSummary) -> list[Candidate]:
    """Store each company in the order found, as discovered; those kept are returned to buy at."""
    candidates = []
    for position, prospect in enumerate(prospects):
        company_id = ctx.companies.upsert(prospect.domain, prospect.name, None, None)
        ctx.company_info.insert(run_id, company_id, CompanyInfo(
            position, prospect.source, prospect.sector, prospect.size, prospect.fit,
            prospect.guessed_domain))
        for posting in prospect.postings:
            ctx.postings.insert(run_id, company_id, posting)
        if prospect.hn is not None:
            post = prospect.hn.post
            ctx.hn_posts.insert(run_id, company_id, HNPostRecord(
                post.item_id, summary.hn_thread_title or "", post.posted_at.date(),
                prospect.hn.method, prospect.hn_status, prospect.hn.parsed, post.html))
        ctx.runs.set_stage(run_id, company_id, "discover", prospect.left_out or "ok")
        if prospect.left_out is None:
            candidates.append(Candidate(company_id, prospect.domain, prospect.name,
                                        prospect.guessed_domain))
    return candidates
