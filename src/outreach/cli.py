from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import Config, ConfigError, load_config, require_env
from outreach.core.ranking import rank_contacts
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.pipeline.runner import RunSummary, run_pipeline
from outreach.render.report import (ReportCompany, ReportContact, ReportEvidence,
                                    ReportView, write_report)
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import BottleneckRepo, EvidenceRepo, FetchAttemptRepo, RunRepo
from outreach.types import Company, Contact, PersonRef

app = typer.Typer(help="Outreach research pipeline")


def build_context(config_path: Path = Path("config.toml")) -> RunContext:
    load_dotenv()
    config = load_config(config_path)

    use_fakes = os.environ.get("OUTREACH_FAKE_ADAPTERS") == "1"
    if use_fakes:
        from outreach.contacts.fake import FakeContactProvider
        from outreach.llm.fake import FakeLLM
        from outreach.sources.jobboards.fake import FakeJobBoardSource
        llm, job_board = FakeLLM(), FakeJobBoardSource([])
        provider = FakeContactProvider({}, credits=0)
    else:
        from outreach.contacts.hunter import HunterProvider
        from outreach.llm.anthropic_client import AnthropicLLM
        llm = AnthropicLLM(require_env("ANTHROPIC_API_KEY"))
        provider = HunterProvider(require_env("HUNTER_API_KEY"))
        job_board = None  # set below, needs the fetcher

    conn = connect(config.paths.db)
    fetcher = Fetcher(httpx.Client())
    if not use_fakes:
        from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
        job_board = GreenhouseBoardSource(fetcher, config.discovery.greenhouse_tokens)

    return RunContext(
        config=config, today=date.today(),
        companies=CompanyRepo(conn), contacts=ContactRepo(conn),
        documents=DocumentRepo(conn), runs=RunRepo(conn), evidence=EvidenceRepo(conn),
        bottlenecks=BottleneckRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        cache=DocumentCache(config.paths.cache), fetcher=fetcher,
        llm=llm, job_board=job_board, contact_provider=provider,
    )


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
        contacts.append(ReportContact(person.full_name, person.title, person.email,
                                      person.email_status, person.profile_url, why))
    return contacts


def build_view(ctx: RunContext, summary: RunSummary, fresh: bool = True) -> ReportView:
    """Assemble the report view model from persisted rows.

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
    evidenced: list[ReportCompany] = []
    no_bottleneck: list[ReportCompany] = []
    handled_company_ids: set[int] = set()

    for bottleneck in ctx.bottlenecks.for_run(run_id):
        company = ctx.companies.get(bottleneck.company_id)
        keep = set(bottleneck.evidence_ids)
        urls = {d.id: d.url for d in ctx.documents.for_company(bottleneck.company_id)}
        evidence = [
            ReportEvidence(item.quote, item.source_class.value,
                           urls.get(item.source_document_id, ""), item.published_at)
            for item in ctx.evidence.for_company(run_id, bottleneck.company_id)
            if item.id in keep
        ]
        keywords = summary.role_title.lower().split()
        contacts = _rank_and_cap_contacts(
            ctx.contacts.for_company(bottleneck.company_id), company, keywords, ctx.config)
        fetch_log = [
            (a.source_class.value, a.outcome, a.http_status)
            for a in ctx.fetch_attempts.for_company(run_id, bottleneck.company_id)
        ]
        contacts_stage = ctx.runs.stage_status(run_id, bottleneck.company_id, "contacts")
        card = ReportCompany(
            name=company.name, domain=company.canonical_domain,
            headcount=company.headcount, headcount_source=company.headcount_source,
            claim=bottleneck.claim, summary=bottleneck.summary,
            reason=bottleneck.reason, evidence=evidence, contacts=contacts,
            fetch_log=fetch_log,
            domain_confirmed=contacts_stage != "skipped_domain_unconfirmed",
        )
        (evidenced if bottleneck.passed else no_bottleneck).append(card)
        handled_company_ids.add(bottleneck.company_id)

    # A company whose research never completed gets no `bottlenecks` row at
    # all (by design -- recording a verdict about work never done would be
    # a lie). Without this pass it simply vanishes from the report: the
    # only trace left is a mismatch between the header's company count and
    # the diagnostics. `run_companies` is the complete list of companies
    # this run touched, so anything in it with no bottleneck row is a
    # research failure, surfaced with its coverage log and error text.
    for company_id in ctx.runs.companies_for_run(run_id):
        if company_id in handled_company_ids:
            continue
        company = ctx.companies.get(company_id)
        fetch_log = [
            (a.source_class.value, a.outcome, a.http_status)
            for a in ctx.fetch_attempts.for_company(run_id, company_id)
        ]
        error = (ctx.runs.stage_error(run_id, company_id, "evidence")
                 or ctx.runs.stage_error(run_id, company_id, "discover"))
        no_bottleneck.append(ReportCompany(
            name=company.name, domain=company.canonical_domain,
            headcount=company.headcount, headcount_source=company.headcount_source,
            claim="", summary="", reason="research_failed",
            evidence=[], contacts=[], fetch_log=fetch_log, error=error,
        ))

    # Smallest and most bypassable first; unknown headcount sorts last.
    evidenced.sort(key=lambda c: (c.headcount is None, c.headcount or 0))
    no_bottleneck.sort(key=lambda c: (c.headcount is None, c.headcount or 0))

    not_recorded = "not recorded"
    return ReportView(
        role_title=summary.role_title, sector=summary.sector, run_date=ctx.today,
        headcount_min=ctx.config.discovery.headcount_min,
        headcount_max=ctx.config.discovery.headcount_max,
        evidenced=evidenced, no_bottleneck=no_bottleneck,
        # Errors aren't persisted past the run that produced them either --
        # same "not recorded" honesty as the diagnostics values below.
        errors=summary.errors if fresh else ["not recorded"],
        diagnostics={
            "Companies discovered": str(summary.companies),
            "Bottlenecks passed": f"{summary.evidenced} / {summary.companies}",
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


@app.command()
def run(
    role: str = typer.Option(..., "--role"),
    sector: str = typer.Option(..., "--sector"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    resume: int | None = typer.Option(None, "--resume"),
) -> None:
    try:
        ctx = build_context()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)

    if not ctx.config.discovery.greenhouse_tokens:
        # Without this, a first real run silently discovers zero companies
        # and writes an empty report with no indication why -- config.toml
        # ships with an empty list, so this is the actual first-run
        # experience unless someone reads the config file first.
        typer.echo(
            "Warning: discovery.greenhouse_tokens is empty in config.toml -- "
            "this run will discover zero companies.", err=True)

    if dry_run:
        terms = ctx.llm.expand_titles(role)
        postings = ctx.job_board.search(terms, ctx.config.discovery.region)
        companies = len({p.company_domain for p in postings})
        try:
            remaining = ctx.contact_provider.remaining_credits()
        except Exception as exc:
            # This is the command someone runs BECAUSE they are unsure they
            # can afford a run -- a traceback here, on the one path that
            # exists to be safe, is the wrong failure mode. Still tell them
            # what discovery found, but be explicit that the cost estimate
            # is incomplete, and exit non-zero so a script driving this
            # doesn't mistake it for a clean answer.
            typer.echo(
                f"Dry run: {companies} companies matched. A full run would use up to "
                f"{companies} enrichment credits, but the remaining credit balance "
                f"could not be checked: {exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo(
            f"Dry run: {companies} companies matched. A full run would use up to "
            f"{companies} enrichment credits; {remaining} remain."
        )
        raise typer.Exit(code=0)

    summary = run_pipeline(ctx, role, sector, resume_run_id=resume)
    path = write_report(build_view(ctx, summary), ctx.config.paths.reports)
    typer.echo(f"Run {summary.run_id}: {summary.evidenced} evidenced, "
               f"{summary.no_bottleneck} without. Report: {path}")


@app.command()
def report(run_id: int = typer.Argument(..., help="A previous run's id")) -> None:
    """Re-render a completed run's report from what is already stored.

    No adapter is touched — every field this needs was already persisted by
    `run`. Two figures cannot be recovered honestly from storage: rejected
    quotes are discarded rather than kept (nothing to count), and per-stage
    quota/failure tallies are not retained past the run that produced them.
    `build_view(..., fresh=False)` renders both as "not recorded" in the
    report itself, rather than a fabricated 0.
    """
    try:
        ctx = build_context()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)

    row = ctx.runs.conn.execute(
        "SELECT role_title, sector FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        typer.echo(f"No run with id {run_id}", err=True)
        raise typer.Exit(code=1)

    bottlenecks = ctx.bottlenecks.for_run(run_id)
    quotes_accepted = sum(
        len(ctx.evidence.for_company(run_id, b.company_id)) for b in bottlenecks)
    summary = RunSummary(
        run_id=run_id, role_title=row["role_title"], sector=row["sector"],
        companies=len(bottlenecks),
        evidenced=sum(1 for b in bottlenecks if b.passed),
        no_bottleneck=sum(1 for b in bottlenecks if not b.passed),
        quotes_accepted=quotes_accepted,
    )
    path = write_report(build_view(ctx, summary, fresh=False), ctx.config.paths.reports)
    typer.echo(f"Report: {path}")


if __name__ == "__main__":
    app()
