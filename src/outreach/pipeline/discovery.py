"""Discovery from HN's "Who is hiring?" thread, up to the point money is spent.

Everything here is free except the capped LLM fallback: the thread is read,
posts are matched against the role, parsed, filtered to the region and work
mode, ranked by the stage hints the posts give away for nothing ("Series B",
"team of 80"), checked against recent research, and capped -- all before a
single Hunter credit. What survives is handed to the runner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Sequence

from outreach.core.hnparse import (hint_signals, parse_post, post_text,
                                   validate_llm_post)
from outreach.core.stage import classify_stage, parse_headcount_statement, stage_sort_key
from outreach.core.workmode import posting_matches
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.ashby import AshbyBoardSource
from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
from outreach.sources.jobboards.lever import LeverBoardSource
from outreach.sources.jobboards.multi import MultiBoardSource
from outreach.sources.jobboards.region import matches_region
from outreach.types import (HNPost, HNThread, ParsedPost, PostingRef, StageResult,
                            WorkMode)

if TYPE_CHECKING:
    from outreach.pipeline.context import RunContext
    from outreach.pipeline.runner import RunSummary

HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"


@dataclass(frozen=True)
class HNCandidate:
    post: HNPost
    parsed: ParsedPost
    method: str  # "parsed" | "llm"
    stage_hint: StageResult
    headcount_hint: int | None


@dataclass
class HNSelection:
    thread: HNThread | None
    kept: list[HNCandidate] = field(default_factory=list)
    # Companies the config tokens already found: their post is still used as
    # evidence, but they are neither ranked nor capped -- the user chose them.
    known: list[HNCandidate] = field(default_factory=list)
    skipped_recent: list[HNCandidate] = field(default_factory=list)
    skipped_cap: list[HNCandidate] = field(default_factory=list)
    needs_llm: int = 0


def candidate_posting(candidate: HNCandidate) -> PostingRef:
    """The HN post itself as a job posting, for a company with no board."""
    p = candidate.parsed
    return PostingRef(p.company, p.domain, p.role, HN_ITEM_URL.format(candidate.post.item_id),
                      p.location, p.work_mode, p.employment_type)


def select_hn_candidates(ctx: RunContext, run_id: int, terms: Sequence[str],
                         work_modes: frozenset[WorkMode], known_domains: frozenset[str],
                         summary: RunSummary, allow_llm: bool = True) -> HNSelection:
    if ctx.hn is None:
        return HNSelection(thread=None)
    thread = ctx.hn.latest_thread()
    if thread is None:
        summary.errors.append("hn: no Who is hiring? thread found")
        return HNSelection(thread=None)
    summary.hn_thread_title = thread.title
    summary.hn_posts_read = len(thread.posts)
    config = ctx.config.hn
    selection = HNSelection(thread=thread)
    wanted = [t.lower() for t in terms if t.strip()]
    llm_budget = config.llm_fallback_max

    candidates: list[HNCandidate] = []
    seen: set[str] = set()
    for post in thread.posts:
        text = post_text(post.html)
        lowered = text.lower()
        # The free prefilter: nothing below, the LLM least of all, ever
        # looks at a post for a role nobody asked about.
        if not any(term in lowered for term in wanted):
            continue
        summary.hn_role_matched += 1

        parsed, method = parse_post(post.html), "parsed"
        if parsed is not None:
            summary.hn_parsed += 1
        elif not allow_llm:
            selection.needs_llm += 1
            continue
        elif llm_budget > 0:
            llm_budget -= 1
            method = "llm"
            try:
                answer = ctx.llm.parse_job_post(text)
                parsed = validate_llm_post(answer, post.html) if answer is not None else None
            except Exception:
                parsed = None
            if parsed is None:
                summary.hn_llm_rejected += 1
                continue
            summary.hn_llm_parsed += 1
        else:
            continue

        posting = PostingRef(parsed.company, parsed.domain, parsed.role, "",
                             parsed.location, parsed.work_mode, parsed.employment_type)
        if not (matches_region(parsed.location, ctx.config.discovery.region)
                and posting_matches(posting, work_modes)):
            summary.hn_filtered += 1
            continue
        if parsed.domain in seen:
            continue  # the same company posting twice: the first post stands
        seen.add(parsed.domain)

        hints = hint_signals(text, post.posted_at.date())
        counts = [n for n in (parse_headcount_statement(h.quote) for h in hints) if n]
        candidate = HNCandidate(post, parsed, method,
                                classify_stage(None, hints, ctx.today, ctx.config.stage),
                                max(counts) if counts else None)
        if parsed.domain in known_domains:
            selection.known.append(candidate)
        else:
            candidates.append(candidate)

    # Stable sort: equal stage keys keep thread order.
    candidates.sort(key=lambda c: stage_sort_key(c.stage_hint.stage, c.headcount_hint))
    since = datetime.combine(ctx.today, time()) - timedelta(days=config.recheck_days)
    for candidate in candidates:
        company = ctx.companies.find(candidate.parsed.domain)
        if company is not None and ctx.runs.researched_since(company.id, since, run_id):
            selection.skipped_recent.append(candidate)
        elif len(selection.kept) < config.max_new_companies:
            selection.kept.append(candidate)
        else:
            selection.skipped_cap.append(candidate)

    summary.hn_kept = len(selection.kept)
    summary.hn_skipped_recent = len(selection.skipped_recent)
    summary.hn_skipped_cap = len(selection.skipped_cap)
    return selection


def hn_board_source(fetcher: Fetcher, candidates: Sequence[HNCandidate]) -> MultiBoardSource:
    """One multi-board search over the boards the HN posts link."""
    tokens: dict[str, list[str]] = {"greenhouse": [], "ashby": [], "lever": []}
    for c in candidates:
        if c.parsed.ats is not None and c.parsed.ats[1] not in tokens[c.parsed.ats[0]]:
            tokens[c.parsed.ats[0]].append(c.parsed.ats[1])
    return MultiBoardSource([
        cls(fetcher, tokens[kind]) for kind, cls in (
            ("greenhouse", GreenhouseBoardSource), ("ashby", AshbyBoardSource),
            ("lever", LeverBoardSource)) if tokens[kind]])
