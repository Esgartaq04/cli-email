from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import ConfigError, load_config, require_env
from outreach.core.ranking import score_title
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.pipeline.runner import RunSummary, run_pipeline
from outreach.render.report import (ReportCompany, ReportContact, ReportEvidence,
                                    ReportView, write_report)
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import BottleneckRepo, EvidenceRepo, FetchAttemptRepo, RunRepo

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
        contacts = []
        for c in ctx.contacts.for_company(bottleneck.company_id):
            # The ranking explanation is a pure function of title and headcount,
            # so recompute it here rather than storing a derived string.
            score = score_title(c.title, company.headcount, keywords,
                                ctx.config.ranking)
            why = score.explanation if score else "ranking unavailable"
            if c.contacted_at:
                why += f" · emailed {c.contacted_at:%d %b}"
            contacts.append(ReportContact(c.full_name, c.title, c.email,
                                          c.email_status, c.profile_url, why))
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

    # Smallest and most bypassable first; unknown headcount sorts last.
    evidenced.sort(key=lambda c: (c.headcount is None, c.headcount or 0))
    no_bottleneck.sort(key=lambda c: (c.headcount is None, c.headcount or 0))

    not_recorded = "not recorded"
    return ReportView(
        role_title=summary.role_title, sector=summary.sector, run_date=ctx.today,
        headcount_min=ctx.config.discovery.headcount_min,
        headcount_max=ctx.config.discovery.headcount_max,
        evidenced=evidenced, no_bottleneck=no_bottleneck,
        diagnostics={
            "Companies discovered": str(summary.companies),
            "Bottlenecks passed": f"{summary.evidenced} / {summary.companies}",
            "Quotes accepted": str(summary.quotes_accepted),
            "Quotes rejected": str(summary.quotes_rejected) if fresh else not_recorded,
            "Skipped on quota": str(summary.skipped_quota) if fresh else not_recorded,
            "Skipped — domain unconfirmed": (
                str(summary.skipped_domain_unconfirmed) if fresh else not_recorded),
            "Company failures": str(summary.failures) if fresh else not_recorded,
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
