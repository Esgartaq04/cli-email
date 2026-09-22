from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from outreach.contacts.quota import QuotaPlan, allocate_quota
from outreach.core.clustering import cluster_by_theme
from outreach.core.dedupe import canonical_domain
from outreach.core.gate import GateVerdict, evaluate
from outreach.core.ranking import rank_contacts
from outreach.extraction.extract import extract_and_persist
from outreach.extraction.htmltext import html_to_text
from outreach.pipeline.context import RunContext
from outreach.sources.base import SurfaceTarget
from outreach.sources.surfaces.standard import surface_targets
from outreach.types import (Bottleneck, EvidenceItem, FetchAttempt, PersonRef,
                            PostingRef, SourceClass, SourceDocument)

# The stage names this runner actually checkpoints, in the order it writes
# them. There is no separate "profile" checkpoint: profiling a company is
# the first half of the evidence stage and shares its fate, so a resume that
# trusted a "profile: ok" row would have nothing to skip.
STAGES = ("discover", "evidence", "contacts", "synthesize")

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
    skipped_domain_unconfirmed: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


def run_pipeline(
    ctx: RunContext, role_title: str, sector: str, resume_run_id: int | None = None
) -> RunSummary:
    """Six stages, per-company isolation. One bad domain never costs the others.

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
    for domain, group in _group_by_domain(postings, summary).items():
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
            # This company's stage is about to be re-derived from scratch,
            # so drop whatever a previous partial attempt left behind.
            # `evidence_items` has no unique key, so without this a resume
            # doubles the rows and the report cites the same quote twice.
            ctx.evidence.clear_for_company(run_id, company_id)
            ctx.fetch_attempts.clear_for_company(run_id, company_id)
            result = _profile_and_extract(ctx, run_id, company_id, summary)
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
    still_pending = [c for c in company_ids
                     if ctx.runs.stage_status(run_id, c, "contacts") != "ok"]
    pending = []
    for company_id in still_pending:
        confirmed = any(a.outcome == "ok"
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
    try:
        plan = allocate_quota(pending, ctx.contact_provider.remaining_credits())
    except Exception as exc:
        # Asking the provider how much budget is left is a network call for
        # a real adapter. Losing it costs us the contacts stage, not the
        # evidence we have already paid to gather.
        summary.errors.append(f"contacts: {exc}")
        for company_id in pending:
            summary.failures += 1
            try:
                ctx.runs.set_stage(run_id, company_id, "contacts", "failed",
                                   error=str(exc))
            except Exception as stage_exc:
                # Already handling one outage; a second fault writing this
                # company's checkpoint must not stop the rest of the pending
                # companies from at least being recorded as failed too.
                summary.errors.append(f"contacts {company_id}: {stage_exc}")
        plan = QuotaPlan(process=[], skipped=[])
    for company_id in plan.skipped:
        ctx.runs.set_stage(run_id, company_id, "contacts", "skipped_quota")
        summary.skipped_quota += 1
    for company_id in plan.process:
        try:
            _resolve_contacts(ctx, company_id, role_title)
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

    try:
        ctx.runs.finish(run_id, datetime.now())
    except Exception as exc:
        # The run is over and the summary is complete. Failing to stamp the
        # runs row is worth reporting, but losing the summary over it would
        # throw away everything the run just paid for.
        summary.errors.append(f"finish {run_id}: {exc}")
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


@dataclass(frozen=True)
class _SurfaceSweep:
    """How a company's surfaces fared: how many were reached, and what broke."""
    completed: int
    errors: list[str]


def _profile_and_extract(ctx: RunContext, run_id: int, company_id: int,
                         summary: RunSummary) -> _SurfaceSweep:
    company = ctx.companies.get(company_id)
    completed = 0
    errors: list[str] = []
    for target in surface_targets(company.canonical_domain, github_org=None):
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
    return _SurfaceSweep(completed=completed, errors=errors)


def _sweep_surface(ctx: RunContext, run_id: int, company_id: int, domain: str,
                   target: SurfaceTarget, summary: RunSummary) -> None:
    outcome = ctx.fetcher.get(target.url)
    if outcome.outcome != "ok" or not outcome.body:
        ctx.fetch_attempts.insert(run_id, FetchAttempt(
            company_id, target.source_class, target.url,
            outcome.outcome, outcome.status, 0))
        return

    # Both the LLM and the substring guard must see the SAME text, or an
    # honest quote that merely crosses an inline tag (or carries an entity
    # like &rsquo;) is rejected, while a quote that literally preserves
    # markup can survive and render as `&lt;strong&gt;` in the report the
    # user pastes into an email. `outcome.body` is raw HTTP response text --
    # extract plain prose from it before either side ever looks at it.
    text = html_to_text(outcome.body)
    content_hash = ctx.cache.store(text.encode("utf-8"))
    published_at = (
        ctx.today if target.source_class in CURRENT_STATE_CLASSES else None
    )
    doc = SourceDocument(None, company_id, target.url, target.source_class,
                         domain, published_at, datetime.now(),
                         outcome.status or 200, content_hash)
    doc_id = ctx.documents.insert(doc)
    ctx.fetch_attempts.insert(run_id, FetchAttempt(
        company_id, target.source_class, target.url, "ok", outcome.status, 1))

    result = extract_and_persist(run_id, doc, text, ctx.llm,
                                 ctx.evidence, document_id=doc_id)
    summary.quotes_accepted += result.accepted
    summary.quotes_rejected += result.rejected


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
