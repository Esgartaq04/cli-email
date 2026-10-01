from __future__ import annotations

import os
from collections import Counter
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import ConfigError, load_config, require_env
from outreach.core.workmode import parse_work_modes, posting_matches
from outreach.net.fetcher import Fetcher, build_user_agent
from outreach.pipeline.context import RunContext
from outreach.pipeline.runner import RunSummary, run_pipeline
from outreach.render.report import write_report
# Also re-exported: `build_view` lived here before it moved to render/.
from outreach.render.view import build_view
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import (EvidenceRepo, FetchAttemptRepo, FindingRepo, PostingRepo,
                                 RunRepo)

app = typer.Typer(help="Outreach research pipeline")


def build_context(config_path: Path = Path("config.toml")) -> RunContext:
    load_dotenv()
    config = load_config(config_path)

    use_fakes = os.environ.get("OUTREACH_FAKE_ADAPTERS") == "1"
    if use_fakes:
        from outreach.contacts.facts import FakeFactsProvider
        from outreach.contacts.fake import FakeContactProvider
        from outreach.llm.fake import FakeLLM
        from outreach.sources.jobboards.fake import FakeJobBoardSource
        llm, job_board = FakeLLM(), FakeJobBoardSource([])
        provider = FakeContactProvider({}, credits=0)
        facts_provider = FakeFactsProvider({})
    else:
        from outreach.contacts.facts import HunterFactsProvider
        from outreach.contacts.hunter import HunterProvider
        from outreach.llm.anthropic_client import AnthropicLLM
        llm = AnthropicLLM(require_env("ANTHROPIC_API_KEY"))
        hunter_key = require_env("HUNTER_API_KEY")
        provider = HunterProvider(hunter_key)
        facts_provider = HunterFactsProvider(hunter_key)
        job_board = None  # set below, needs the fetcher

    conn = connect(config.paths.db)
    # The contact address is deliberately not in the source: it is a
    # politeness signal for site operators, not a secret, but committing it
    # would publish a scrapeable address every time this repo is shared.
    fetcher = Fetcher(
        httpx.Client(),
        user_agent=build_user_agent(os.environ.get("OUTREACH_CONTACT_EMAIL")),
    )
    if not use_fakes:
        from outreach.sources.jobboards.ashby import AshbyBoardSource
        from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
        from outreach.sources.jobboards.lever import LeverBoardSource
        from outreach.sources.jobboards.multi import MultiBoardSource
        discovery = config.discovery
        # A board with no tokens has nothing to search; leaving it out keeps
        # an unconfigured board from being reported as a failing one.
        job_board = MultiBoardSource([
            cls(fetcher, tokens) for cls, tokens in (
                (GreenhouseBoardSource, discovery.greenhouse_tokens),
                (AshbyBoardSource, discovery.ashby_tokens),
                (LeverBoardSource, discovery.lever_tokens),
            ) if tokens])

    return RunContext(
        config=config, today=date.today(),
        companies=CompanyRepo(conn), contacts=ContactRepo(conn),
        documents=DocumentRepo(conn), runs=RunRepo(conn), evidence=EvidenceRepo(conn),
        findings=FindingRepo(conn), postings=PostingRepo(conn),
        fetch_attempts=FetchAttemptRepo(conn),
        cache=DocumentCache(config.paths.cache), fetcher=fetcher,
        llm=llm, job_board=job_board, contact_provider=provider,
        facts_provider=facts_provider,
    )


def _summary_from_storage(ctx: RunContext, run_id: int, role_title: str,
                          sector: str) -> RunSummary:
    """Rebuild what a v2 run's summary can honestly recover from storage.

    Findings answer evidenced/no-findings, the stage rows answer how many
    companies were discovered, excluded and classified, and the evidence
    rows answer how many quotes were accepted. Everything else stays at its
    default and is rendered "not recorded" by `build_view(..., fresh=False)`.
    """
    findings = ctx.findings.for_run(run_id)
    evidenced = {f.company_id for f in findings if f.passed}
    company_ids = ctx.runs.companies_for_run(run_id)

    def count(stage: str, status: str) -> int:
        return sum(1 for c in company_ids
                   if ctx.runs.stage_status(run_id, c, stage) == status)

    stages = (ctx.runs.company_stage(run_id, c) for c in company_ids)
    return RunSummary(
        run_id=run_id, role_title=role_title, sector=sector,
        # A company excluded for its postings never counted as discovered.
        companies=count("discover", "ok"),
        evidenced=len(evidenced),
        no_findings=len({f.company_id for f in findings} - evidenced),
        excluded_size=count("enrich", "excluded_size"),
        excluded_no_matching_posting=count("discover", "excluded_no_matching_posting"),
        stage_counts=dict(Counter(s.stage.value for s in stages if s is not None)),
        quotes_accepted=sum(len(ctx.evidence.for_company(run_id, c)) for c in company_ids),
    )


@app.command()
def run(
    role: str = typer.Option(..., "--role"),
    sector: str = typer.Option(..., "--sector"),
    work_mode: str | None = typer.Option(
        None, "--work-mode",
        help="Comma-separated remote, hybrid, onsite. Default: all three."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    resume: int | None = typer.Option(None, "--resume"),
) -> None:
    # Validated before build_context so a typo costs nothing: building the
    # context checks API keys and opens the database.
    try:
        work_modes = parse_work_modes(work_mode)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)

    try:
        ctx = build_context()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)

    if not os.environ.get("OUTREACH_CONTACT_EMAIL", "").strip():
        # The user agent still identifies the tool, but without a contact
        # address a site operator who notices this traffic has no way to
        # reach anyone about it. That is a politeness regression, not an
        # error, so it warns rather than aborting.
        typer.echo(
            "Warning: OUTREACH_CONTACT_EMAIL is not set -- requests will "
            "identify this tool but name no one to contact about them.",
            err=True)

    discovery = ctx.config.discovery
    if not (discovery.greenhouse_tokens or discovery.ashby_tokens or discovery.lever_tokens):
        # Without this, a first real run silently discovers zero companies
        # and writes an empty report with no indication why -- config.toml
        # ships with empty lists, so this is the actual first-run
        # experience unless someone reads the config file first.
        typer.echo(
            "Warning: no greenhouse_tokens, ashby_tokens or lever_tokens in "
            "config.toml -- this run will discover zero companies.", err=True)

    if dry_run:
        terms = ctx.llm.expand_titles(role)
        board_errors = getattr(ctx.job_board, "errors", None)
        seen_errors = len(board_errors) if board_errors is not None else 0
        postings = ctx.job_board.search(terms, discovery.region)
        if board_errors is not None:
            for error in board_errors[seen_errors:]:
                # A board that failed made the count below an undercount.
                typer.echo(f"Warning: discovery board failed: {error}", err=True)
        # The same filter the pipeline applies: a company with no posting in
        # the wanted work mode is excluded before any credit is spent on it.
        companies = len({p.company_domain for p in postings
                         if posting_matches(p, work_modes)})
        hunter = ctx.config.hunter
        finder = hunter.finder_cost if hunter.linkedin_lookup else 0
        enrichment = companies * hunter.enrichment_cost
        searches = companies
        lookups = companies * ctx.config.ranking.max_contacts_per_company * finder
        total = enrichment + searches + lookups
        estimate = (
            f"Dry run: {companies} companies matched. A full run would use up to "
            f"{enrichment} enrichment + {searches} contact-search + {lookups} "
            f"LinkedIn-lookup credits ({total} total)")
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
                f"{estimate}, but the remaining credit balance could not be "
                f"checked: {exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"{estimate}; {remaining} remain.")
        raise typer.Exit(code=0)

    summary = run_pipeline(ctx, role, sector, resume_run_id=resume, work_modes=work_modes)
    path = write_report(build_view(ctx, summary), ctx.config.paths.reports)
    typer.echo(f"Run {summary.run_id}: {summary.evidenced} evidenced, "
               f"{summary.no_findings} without. Report: {path}")


@app.command()
def report(run_id: int = typer.Argument(..., help="A previous run's id")) -> None:
    """Re-render a completed run's report from what is already stored.

    No adapter is touched — every field this needs was already persisted by
    `run`. Some figures cannot be recovered honestly from storage: rejected
    quotes are discarded rather than kept (nothing to count), and per-stage
    quota/failure tallies and facts fetched/cached are not retained past the
    run that produced them. `build_view(..., fresh=False)` renders those as
    "not recorded" in the report itself, rather than a fabricated 0.

    Only a v2 run can be re-rendered: see the pre-v2 guard below.
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
    if ctx.runs.schema_version(run_id) < 2:
        # A bottlenecks-era run has no findings or stages. Laying it out in
        # the new report would show every company as having found nothing,
        # which is not what that run found; its own saved report still is.
        typer.echo(f"Run {run_id} is a pre-v2 run; see its saved HTML report.", err=True)
        raise typer.Exit(code=1)

    summary = _summary_from_storage(ctx, run_id, row["role_title"], row["sector"])
    path = write_report(build_view(ctx, summary, fresh=False), ctx.config.paths.reports)
    typer.echo(f"Report: {path}")


if __name__ == "__main__":
    app()
