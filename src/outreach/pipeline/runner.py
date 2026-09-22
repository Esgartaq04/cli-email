from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from outreach.contacts.quota import allocate_quota
from outreach.core.clustering import cluster_by_theme
from outreach.core.dedupe import dedupe_postings
from outreach.core.gate import GateVerdict, evaluate
from outreach.core.ranking import rank_contacts
from outreach.extraction.extract import extract_and_persist
from outreach.pipeline.context import RunContext
from outreach.sources.surfaces.standard import surface_targets
from outreach.types import (Bottleneck, EvidenceItem, FetchAttempt, PersonRef,
                            SourceClass, SourceDocument)

STAGES = ("discover", "profile", "contacts", "evidence", "synthesize")

# Surfaces that describe a company's CURRENT state rather than a dated
# archive. For these the fetch date is an honest publication date: a
# careers page or a status page says what is true today. An eng blog post,
# a changelog entry, a GitHub event or a news article are dated artifacts,
# and V1 parses no date out of them — so their `published_at` stays None
# and the gate treats them as not-fresh. That fails toward the
# no-bottleneck branch, which is the safe direction: we would rather stay
# silent about a company than claim stale pain is current.
CURRENT_STATE_CLASSES = frozenset({
    SourceClass.CAREERS_PAGE,
    SourceClass.JOB_POSTING,
    SourceClass.STATUS_PAGE,
})


@dataclass
class RunSummary:
    run_id: int
    role_title: str = ""
    sector: str = ""
    companies: int = 0
    evidenced: int = 0
    no_bottleneck: int = 0
    quotes_accepted: int = 0
    quotes_rejected: int = 0
    skipped_quota: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


def run_pipeline(
    ctx: RunContext, role_title: str, sector: str, resume_run_id: int | None = None
) -> RunSummary:
    """Six stages, per-company isolation. One bad domain never costs the others.

    Every per-company step is wrapped: a failure becomes a `failed` stage
    row and an entry in `summary.errors`, and the loop moves on. Run-level
    setup (creating the run, asking the job board for postings) is
    deliberately NOT swallowed — there is no partial result to salvage when
    those fail, and a silent empty run would be worse than a traceback.
    """
    run_id = resume_run_id or ctx.runs.create(
        role_title, sector, ctx.config.discovery.region,
        (ctx.config.discovery.headcount_min, ctx.config.discovery.headcount_max),
        datetime.now(),
    )
    summary = RunSummary(run_id=run_id, role_title=role_title, sector=sector)

    # --- discover -------------------------------------------------------
    terms = ctx.llm.expand_titles(role_title)
    postings = ctx.job_board.search(terms, ctx.config.discovery.region)
    company_ids: list[int] = []
    for domain, group in dedupe_postings(postings).items():
        try:
            company_id = ctx.companies.upsert(domain, group[0].company_name, None, None)
        except Exception as exc:  # one unusable domain is not a dead run
            summary.failures += 1
            summary.errors.append(f"discover {domain}: {exc}")
            continue
        ctx.runs.set_stage(run_id, company_id, "discover", "ok")
        company_ids.append(company_id)
    summary.companies = len(company_ids)

    # --- profile + evidence (per company, isolated) ---------------------
    for company_id in company_ids:
        if ctx.runs.stage_status(run_id, company_id, "evidence") == "ok":
            continue
        try:
            _profile_and_extract(ctx, run_id, company_id, summary)
            ctx.runs.set_stage(run_id, company_id, "evidence", "ok")
        except Exception as exc:  # one company's failure is data, not a crash
            summary.failures += 1
            summary.errors.append(f"evidence {company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "evidence", "failed", error=str(exc))

    # --- contacts (quota-aware) -----------------------------------------
    pending = [c for c in company_ids
               if ctx.runs.stage_status(run_id, c, "contacts") != "ok"]
    plan = allocate_quota(pending, ctx.contact_provider.remaining_credits())
    for company_id in plan.skipped:
        ctx.runs.set_stage(run_id, company_id, "contacts", "skipped_quota")
        summary.skipped_quota += 1
    for company_id in plan.process:
        try:
            _resolve_contacts(ctx, run_id, company_id, role_title)
            ctx.runs.set_stage(run_id, company_id, "contacts", "ok")
        except Exception as exc:
            summary.failures += 1
            summary.errors.append(f"contacts {company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "contacts", "failed", error=str(exc))

    # --- gate + synthesize ----------------------------------------------
    for company_id in company_ids:
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

    ctx.runs.finish(run_id, datetime.now())
    return summary


def _profile_and_extract(ctx: RunContext, run_id: int, company_id: int,
                         summary: RunSummary) -> None:
    company = ctx.companies.get(company_id)
    for target in surface_targets(company.canonical_domain, github_org=None):
        outcome = ctx.fetcher.get(target.url)
        if outcome.outcome != "ok" or not outcome.body:
            ctx.fetch_attempts.insert(run_id, FetchAttempt(
                company_id, target.source_class, target.url,
                outcome.outcome, outcome.status, 0))
            continue

        content_hash = ctx.cache.store(outcome.body.encode("utf-8"))
        published_at = (
            ctx.today if target.source_class in CURRENT_STATE_CLASSES else None
        )
        doc = SourceDocument(None, company_id, target.url, target.source_class,
                             company.canonical_domain, published_at, datetime.now(),
                             outcome.status or 200, content_hash)
        doc_id = ctx.documents.insert(doc)
        ctx.fetch_attempts.insert(run_id, FetchAttempt(
            company_id, target.source_class, target.url, "ok", outcome.status, 1))

        result = extract_and_persist(run_id, doc, outcome.body, ctx.llm,
                                     ctx.evidence, document_id=doc_id)
        summary.quotes_accepted += result.accepted
        summary.quotes_rejected += result.rejected


def _resolve_contacts(ctx: RunContext, run_id: int, company_id: int,
                      role_title: str) -> None:
    company = ctx.companies.get(company_id)
    keywords = role_title.lower().split()
    people = ctx.contact_provider.find(company.canonical_domain, keywords)
    for person, _score in rank_contacts(people, company.headcount, keywords,
                                        ctx.config.ranking):
        status = person.email_status
        if person.email and status != "verified":
            status = ctx.contact_provider.verify(person.email)
        resolved = PersonRef(
            full_name=person.full_name, title=person.title,
            profile_url=person.profile_url,
            email=person.email if status == "verified" else None,
            email_status=status,
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

    if best is None:
        ctx.bottlenecks.insert(run_id, Bottleneck(
            None, company_id, "", "", False, _failure_reason(ctx, items), ()))
        summary.no_bottleneck += 1
        return

    _theme, cluster, verdict = best
    quotes = [i.quote for i in cluster]
    ctx.bottlenecks.insert(run_id, Bottleneck(
        None, company_id, cluster[0].claim,
        ctx.llm.write_summary(cluster[0].claim, quotes),
        True, verdict.reason, verdict.evidence_ids))
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
