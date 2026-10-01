"""Assemble the report's view model from what a run persisted.

Everything here reads storage and nothing calls an adapter, so the same code
lays out a run that just finished and one `report` re-renders months later.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import TYPE_CHECKING

from outreach.config import Config
from outreach.core.clustering import cluster_by_theme
from outreach.core.gate import evaluate
from outreach.core.ranking import rank_contacts
from outreach.core.stage import latest_round_label, stage_sort_key
from outreach.core.themes import BUILDING_THEMES
from outreach.render.report import (STAGE_LABELS, ReportCompany, ReportContact,
                                    ReportEvidence, ReportExcluded, ReportFinding,
                                    ReportHook, ReportPosting, ReportSource, ReportTheme,
                                    ReportView, linkedin_search_url)
from outreach.types import (Company, Contact, Finding, PersonRef, SourceClass,
                            SourceDocument, Stage, StageResult)

if TYPE_CHECKING:
    # Typing only: render/ reads what the pipeline stored, it never runs it.
    from outreach.pipeline.context import RunContext
    from outreach.pipeline.runner import RunSummary

_TARGET_STAGES = frozenset({Stage.GROWTH, Stage.EXPANSION})

# The stage row that records each way a run drops a company before
# researching it, and the status it writes there.
_EXCLUSIONS = (("discover", "excluded_no_matching_posting"), ("enrich", "excluded_size"),
               ("discover", "skipped_cap"), ("discover", "skipped_recent"))


def _rank_and_cap_contacts(
    stored: list[Contact], company: Company, keywords: list[str], config: Config
) -> list[ReportContact]:
    """Re-rank and cap `contacts.for_company` on read, per run.

    `contacts.for_company` is company-scoped, not run-scoped: it returns
    every contact ever stored for this company across every past run, and
    `max_contacts_per_company` is applied only when a run WRITES contacts
    (`rank_contacts` in `_resolve_contacts`), never when the report reads
    them back. After a few runs a card could list far more than the
    configured cap, including people surfaced under a different `--role`
    whose title no longer scores against these keywords. Re-ranking and
    truncating here, against THIS run's role, is what keeps the card
    honest regardless of how many runs have touched this company.
    """
    by_name = {c.full_name: c for c in stored}
    people = [PersonRef(c.full_name, c.title, c.profile_url, c.email, c.email_status)
              for c in stored]
    ranked = rank_contacts(people, company.headcount, keywords, config.ranking)

    contacts: list[ReportContact] = []
    for person, score in ranked:
        why = score.explanation
        orig = by_name.get(person.full_name)
        if orig and orig.contacted_at:
            why += f" · emailed {orig.contacted_at:%d %b}"
        search = (linkedin_search_url(person.full_name, company.name)
                  if person.profile_url is None else None)
        contacts.append(ReportContact(person.full_name, person.title, person.email,
                                      person.email_status, person.profile_url, why,
                                      linkedin_search=search))
    return contacts


def _theme_breakdown(ctx: RunContext, run_id: int, company_id: int
                     ) -> tuple[list[ReportTheme], list[ReportSource]]:
    """What a run found for a company, laid out for a reader asking "why not?".

    Themes come with their own gate verdict because the gate is evaluated
    per theme: a company can hold thirteen quotes and still have no theme
    that clears it, and the reader needs to see that. Only building themes
    are listed, because only they were ever candidates: a funding round
    shown here with a "passed" verdict would read as a finding the report
    had somehow dropped. Every claim still counts on its page below.
    """
    items = ctx.evidence.for_company(run_id, company_id)
    docs = ctx.documents.for_company(company_id)
    urls = {d.id: d.url for d in docs}

    read_urls = {a.url for a in ctx.fetch_attempts.for_company(run_id, company_id)
                 if a.outcome == "ok" and a.document_count}
    latest: dict[str, SourceDocument] = {}
    for d in docs:
        if d.url in read_urls and (d.url not in latest or d.id > latest[d.url].id):
            latest[d.url] = d
    claims_per_doc = Counter(i.source_document_id for i in items)
    sources = [ReportSource(d.url, d.source_class.value, d.published_at,
                            claims_per_doc.get(d.id, 0))
               for d in sorted(latest.values(), key=lambda d: d.id)]

    building = [i for i in items if i.theme in BUILDING_THEMES]
    themes: list[ReportTheme] = []
    for theme, cluster in cluster_by_theme(building).items():
        verdict = evaluate(cluster, ctx.config.gate, ctx.today)
        themes.append(ReportTheme(
            theme=theme,
            evidence=[ReportEvidence(i.quote, i.source_class.value,
                                     urls.get(i.source_document_id, ""), i.published_at)
                      for i in cluster],
            source_labels=sorted({i.source_class.value.replace("_", " ") for i in cluster}),
            independent_sources=len({(i.source_class.value, i.publisher_domain)
                                     for i in cluster}),
            reason=verdict.reason,
        ))
    themes.sort(key=lambda t: (-t.independent_sources, -len(t.evidence), t.theme))
    return themes, sources


def _report_findings(ctx: RunContext, run_id: int, company_id: int,
                     findings: list[Finding]) -> list[ReportFinding]:
    """Each passing finding with the quotes the gate passed it on."""
    items = {i.id: i for i in ctx.evidence.for_company(run_id, company_id)}
    urls = {d.id: d.url for d in ctx.documents.for_company(company_id)}
    return [
        ReportFinding(
            theme=f.theme, claim=f.claim, summary=f.summary, corroborated=f.corroborated,
            evidence=[ReportEvidence(i.quote, i.source_class.value,
                                     urls.get(i.source_document_id, ""), i.published_at)
                      for i in (items[e] for e in f.evidence_ids if e in items)],
        )
        for f in findings
    ]


def _repo_label(ctx: RunContext, doc: SourceDocument) -> str:
    """"name — description", from the text the run stored as "name: description"."""
    name = doc.url.rstrip("/").rsplit("/", 1)[-1]
    try:
        text = ctx.cache.read(doc.content_hash)
    except OSError:
        # The cache is disposable; the link is not. A bare url still works.
        return doc.url
    prefix = f"{name}: "
    description = text[len(prefix):].strip() if text.startswith(prefix) else ""
    return f"{name} — {description}" if description else name


def _hooks(ctx: RunContext, run_id: int, company_id: int) -> list[ReportHook]:
    """Recently pushed repos and public developer docs: things to build against.

    Documents are company-scoped, not run-scoped, so a repo counts as this
    run's only when it was fetched inside the run's window. Re-reading a
    stored document refreshes its fetch time and push date (see
    `DocumentRepo.insert`), so a repo read again now is in the window with
    its current push, and one an earlier run saw -- since archived, deleted
    or gone quiet -- is not.
    """
    attempts = ctx.fetch_attempts.for_company(run_id, company_id)
    started, finished = ctx.runs.window(run_id)
    # An unfinished run (crashed, or still going) has read up to now.
    finished = finished or datetime.now()

    latest: dict[str, SourceDocument] = {}
    for d in ctx.documents.for_company(company_id):
        if (d.source_class is SourceClass.GITHUB and started <= d.fetched_at <= finished
                and (d.url not in latest or d.id > latest[d.url].id)):
            latest[d.url] = d
    newest = sorted(latest.values(), key=lambda d: (d.published_at or date.min, d.id),
                    reverse=True)
    hooks = [ReportHook("repo", _repo_label(ctx, d), d.url, d.published_at)
             for d in newest]

    # Dev docs are recorded and never extracted, so the attempt is the record.
    hooks += [ReportHook("docs", a.url, a.url, None) for a in attempts
              if a.source_class is SourceClass.DEV_DOCS and a.outcome == "ok"]
    return hooks


def _failure_error(ctx: RunContext, run_id: int, company_id: int) -> str | None:
    """The error text of whichever stage stopped this company short of a verdict."""
    for stage in ("evidence", "synthesize", "discover"):
        if ctx.runs.stage_status(run_id, company_id, stage) == "failed":
            return ctx.runs.stage_error(run_id, company_id, stage)
    return ctx.runs.stage_error(run_id, company_id, "evidence")


def _card(ctx: RunContext, run_id: int, company: Company, stage: StageResult | None,
          findings: list[Finding], keywords: list[str]) -> ReportCompany:
    company_id = company.id
    passing = [f for f in findings if f.passed]
    stage_value = stage.stage.value if stage else Stage.UNKNOWN.value
    if passing:
        reason = "passed"
    elif findings:
        reason = findings[0].reason
    else:
        # No finding row at all: research (or synthesis) never completed.
        # Recording a verdict about work never done would be a lie, so the
        # card says research failed and carries the error instead.
        reason = "research_failed"

    card = ReportCompany(
        name=company.name, domain=company.canonical_domain,
        headcount=company.headcount, headcount_source=company.headcount_source,
        reason=reason,
        stage=stage_value, stage_label=STAGE_LABELS[stage_value],
        stage_reasons=list(stage.reasons) if stage else [],
        founded_year=company.founded_year, headcount_band=company.headcount_band,
        latest_round=latest_round_label(company.funding_rounds),
        postings=[ReportPosting(p.title, p.url, p.work_mode)
                  for p in ctx.postings.for_company(run_id, company_id)],
        findings=_report_findings(ctx, run_id, company_id, passing),
        hooks=_hooks(ctx, run_id, company_id),
        contacts=_rank_and_cap_contacts(
            ctx.contacts.for_company(company_id), company, keywords, ctx.config),
        fetch_log=[(a.source_class.value, a.outcome, a.http_status)
                   for a in ctx.fetch_attempts.for_company(run_id, company_id)],
        domain_confirmed=(ctx.runs.stage_status(run_id, company_id, "contacts")
                          != "skipped_domain_unconfirmed"),
        hn_post=_hn_link(ctx, run_id, company_id),
    )
    if not passing:
        card.themes, card.sources_read = _theme_breakdown(ctx, run_id, company_id)
    if reason == "research_failed":
        card.error = _failure_error(ctx, run_id, company_id)
    return card


def _hn_link(ctx: RunContext, run_id: int, company_id: int) -> tuple[str, str] | None:
    record = ctx.hn_posts.for_company(run_id, company_id) if ctx.hn_posts else None
    if record is None:
        return None
    return (f"via HN Who is hiring? ({record.posted_at:%B %Y})",
            f"https://news.ycombinator.com/item?id={record.item_id}")


def _stage_counts(counts: dict[str, int]) -> str:
    shown = [f"{label} {counts[stage]}" for stage, label in STAGE_LABELS.items()
             if counts.get(stage)]
    return " · ".join(shown) or "none"


def build_view(ctx: RunContext, summary: RunSummary, fresh: bool = True) -> ReportView:
    """Assemble the report view model from persisted rows.

    `run_companies` is the complete list of companies this run touched, so
    every one of them lands somewhere: excluded, with findings, or without.
    A company whose research failed has no finding row, and without walking
    that table it would simply vanish from the report.

    `fresh` is True for a `summary` that just came out of `run_pipeline` in
    this process, and False for one reconstructed from storage by `report`.
    Reconstruction can't recover every figure honestly: rejected quotes are
    discarded rather than kept, and per-stage quota/failure tallies aren't
    retained past the run that produced them. Rendering those as "0" would
    be indistinguishable from a run that genuinely had zero, which defeats
    the one thing the diagnostics footer exists for -- catching silent
    degradation -- so a reconstructed summary renders them as text saying
    so instead of a number.
    """
    run_id = summary.run_id
    keywords = summary.role_title.lower().split()
    findings_by_company: dict[int, list[Finding]] = {}
    for f in ctx.findings.for_run(run_id):
        findings_by_company.setdefault(f.company_id, []).append(f)

    sections: dict[str, list[tuple[tuple, ReportCompany]]] = {
        "target": [], "other": [], "none": []}
    excluded: list[tuple[tuple, ReportExcluded]] = []
    for company_id in ctx.runs.companies_for_run(run_id):
        company = ctx.companies.get(company_id)
        exclusion = next((status for stage, status in _EXCLUSIONS
                          if ctx.runs.stage_status(run_id, company_id, stage) == status),
                         None)
        if exclusion is not None:
            # Dropped before classification, so it has no stage to sort by.
            excluded.append((stage_sort_key(Stage.UNKNOWN, company.headcount),
                             ReportExcluded(company.name, company.canonical_domain,
                                            exclusion)))
            continue
        stage = ctx.runs.company_stage(run_id, company_id)
        card = _card(ctx, run_id, company, stage,
                     findings_by_company.get(company_id, []), keywords)
        stage_enum = stage.stage if stage else Stage.UNKNOWN
        if not card.findings:
            section = "none"
        elif stage_enum in _TARGET_STAGES:
            section = "target"
        else:
            section = "other"
        sections[section].append((stage_sort_key(stage_enum, company.headcount), card))

    def ordered(entries):
        # Stable, so companies that tie keep the run's own (id) order.
        return [item for _, item in sorted(entries, key=lambda e: e[0])]

    not_recorded = "not recorded"
    return ReportView(
        role_title=summary.role_title, sector=summary.sector, run_date=ctx.today,
        headcount_min=ctx.config.discovery.headcount_min,
        headcount_max=ctx.config.discovery.headcount_max,
        target_stage=ordered(sections["target"]),
        other_stages=ordered(sections["other"]),
        no_findings=ordered(sections["none"]),
        excluded=ordered(excluded),
        min_independent_sources=ctx.config.gate.min_independent_sources,
        hn_recheck_days=ctx.config.hn.recheck_days,
        # Errors aren't persisted past the run that produced them either --
        # same "not recorded" honesty as the diagnostics values below.
        errors=summary.errors if fresh else ["not recorded"],
        diagnostics={
            "Companies discovered": str(summary.companies),
            "Companies with findings": f"{summary.evidenced} / {summary.companies}",
            "Excluded — over size cap": str(summary.excluded_size),
            "Excluded — work mode": str(summary.excluded_no_matching_posting),
            "Companies by stage": _stage_counts(summary.stage_counts),
            "HN thread": (summary.hn_thread_title or "none read") if fresh else not_recorded,
            "HN posts read / matching role": (
                f"{summary.hn_posts_read} / {summary.hn_role_matched}"
                if fresh else not_recorded),
            "HN parsed / LLM-parsed / LLM-rejected": (
                f"{summary.hn_parsed} / {summary.hn_llm_parsed} / {summary.hn_llm_rejected}"
                if fresh else not_recorded),
            "HN filtered out": str(summary.hn_filtered) if fresh else not_recorded,
            "HN kept / skipped (cap) / skipped (recent)": (
                f"{summary.hn_kept if fresh else not_recorded} / {summary.hn_skipped_cap}"
                f" / {summary.hn_skipped_recent}"),
            "Company facts fetched / cached": (
                f"{summary.facts_fetched} / {summary.facts_cached}"
                if fresh else not_recorded),
            "Quotes accepted": str(summary.quotes_accepted),
            "Quotes rejected": str(summary.quotes_rejected) if fresh else not_recorded,
            "Skipped on quota": str(summary.skipped_quota) if fresh else not_recorded,
            "Skipped — domain unconfirmed": (
                str(summary.skipped_domain_unconfirmed) if fresh else not_recorded),
            "Company failures": str(summary.failures) if fresh else not_recorded,
            "Wall clock (s)": (
                f"{summary.wall_clock_seconds:.1f}"
                if fresh and summary.wall_clock_seconds is not None else not_recorded),
            "Enrichment credits remaining": (
                str(summary.credits_after)
                if fresh and summary.credits_after is not None else not_recorded),
            "Enrichment credits consumed": (
                str(summary.credits_before - summary.credits_after)
                if fresh and summary.credits_before is not None
                and summary.credits_after is not None else not_recorded),
        },
    )
