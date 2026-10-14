from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import NoReturn, Sequence

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import (Config, ConfigError, PeopleList, ignored_warning, load_config,
                             require_env)
from outreach.contacts.hunter import HunterAPIError
from outreach.core.addresses import normalize_address
from outreach.core.filters import (SIZE_BANDS, FilterError, describe_filters, parse_location,
                                   parse_sectors, parse_sizes, search_sectors)
from outreach.credits import format_credits
from outreach.net.fetcher import Fetcher, build_user_agent
from outreach.pipeline.companies import run_companies
from outreach.pipeline.context import RunContext
from outreach.pipeline.contacts import max_cost, preview_contacts
from outreach.pipeline.summary import RunSummary
from outreach.render.contact_report import build_contact_view, write_contact_report
from outreach.sectors import load_sectors
from outreach.sources.companies import CompanyQuery
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.emailed import EmailedRepo
from outreach.store.runs import (CompanyInfoRepo, FetchAttemptRepo, HNPostRepo, PostingRepo,
                                 RunContactRepo, RunRepo)
from outreach.types import RunFilters

app = typer.Typer(help="Find the right people to email at the right companies.")
emailed_app = typer.Typer(help="Keep track of who you've emailed.")
app.add_typer(emailed_app, name="emailed")

CONFIG_PATH = Path("config.toml")
# Runs before this schema have no kind, people list or filters, so no report can be built from them.
_FIRST_REPORTABLE_SCHEMA = 4
_NO_COMPANY_MATCHED = ('No company matched these filters. Try another sector '
                       '("outreach sectors <word>") or a wider size or location.')


def build_context(config: Config, *, need_llm: bool, need_hunter: bool = True) -> RunContext:
    """Assemble what a command touches. Keys are required only for the adapters asked for.

    With OUTREACH_FAKE_ADAPTERS=1 every adapter is a fake, no key is checked, and
    the fetcher answers 404 to everything from a mock transport, so nothing a fake
    run does -- or a test patches -- can reach the network.
    """
    load_dotenv()
    use_fakes = os.environ.get("OUTREACH_FAKE_ADAPTERS") == "1"
    # The contact address is deliberately not in the source: it is a
    # politeness signal for site operators, not a secret, but committing it
    # would publish a scrapeable address every time this repo is shared.
    user_agent = build_user_agent(os.environ.get("OUTREACH_CONTACT_EMAIL"))
    llm = people = company_search = None

    if use_fakes:
        from outreach.contacts.fake import FakePeopleFinder
        from outreach.llm.fake import FakeLLM
        from outreach.sources.companies import FakeCompanySearch
        from outreach.sources.hn import FakeHNSource
        from outreach.sources.jobboards.fake import FakeJobBoardSource
        llm, job_board, hn = FakeLLM(), FakeJobBoardSource([]), FakeHNSource()
        # No credits, so a fake run can never buy anyone.
        people = FakePeopleFinder(balance=0)
        company_search = FakeCompanySearch()
        fetcher = Fetcher(
            httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))),
            sleep=lambda seconds: None, user_agent=user_agent)
    else:
        if need_llm:
            from outreach.llm.anthropic_client import AnthropicLLM
            llm = AnthropicLLM(require_env("ANTHROPIC_API_KEY"))
        if need_hunter:
            from outreach.contacts.hunter import HunterProvider
            from outreach.sources.companies import HunterCompanySearch
            hunter_key = require_env("HUNTER_API_KEY")
            people, company_search = HunterProvider(hunter_key), HunterCompanySearch(hunter_key)
        fetcher = Fetcher(httpx.Client(), user_agent=user_agent)
        from outreach.sources.hn import HNThreadSource
        from outreach.sources.jobboards.ashby import AshbyBoardSource
        from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
        from outreach.sources.jobboards.lever import LeverBoardSource
        from outreach.sources.jobboards.multi import MultiBoardSource
        hn = HNThreadSource(fetcher)
        discovery = config.discovery
        # A board with no tokens has nothing to search; leaving it out keeps
        # an unconfigured board from being reported as a failing one.
        job_board = MultiBoardSource([
            cls(fetcher, tokens) for cls, tokens in (
                (GreenhouseBoardSource, discovery.greenhouse_tokens),
                (AshbyBoardSource, discovery.ashby_tokens),
                (LeverBoardSource, discovery.lever_tokens),
            ) if tokens])

    conn = connect(config.paths.db)
    return RunContext(
        config=config, today=date.today(),
        companies=CompanyRepo(conn), contacts=ContactRepo(conn), runs=RunRepo(conn),
        postings=PostingRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        hn_posts=HNPostRepo(conn), company_info=CompanyInfoRepo(conn),
        run_contacts=RunContactRepo(conn), emailed=EmailedRepo(conn),
        fetcher=fetcher, llm=llm, job_board=job_board, hn=hn, people=people,
        company_search=company_search,
    )


# --- what the commands share ---------------------------------------------------------

def _fail(message: str, code: int = 2) -> NoReturn:
    """Print `message` to stderr and end the command. 2 is a usage error, 1 a failed run."""
    typer.echo(message, err=True)
    raise typer.Exit(code=code)


def _load_config() -> Config:
    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as exc:
        _fail(str(exc))
    warning = ignored_warning(config)
    if warning is not None:
        typer.echo(warning, err=True)
    return config


def _context(config: Config, *, need_llm: bool, need_hunter: bool = True) -> RunContext:
    """`build_context`, with a missing key ending the command as a usage error."""
    try:
        return build_context(config, need_llm=need_llm, need_hunter=need_hunter)
    except ConfigError as exc:
        _fail(str(exc))


def _offline_context() -> RunContext:
    """The config's paths and the database, with no key and no adapter to call."""
    return _context(_load_config(), need_llm=False, need_hunter=False)


def _parse_filters(sector: list[str] | None, size: list[str] | None,
                   location: str | None) -> RunFilters:
    try:
        return RunFilters(parse_sectors(sector or [], load_sectors()), parse_sizes(size or []),
                          parse_location(location))
    except FilterError as exc:
        _fail(str(exc))


def _people_list(config: Config, name: str) -> PeopleList:
    people = config.people.get(name)
    if people is None:
        _fail(f'Unknown people list "{name}". Known lists: {", ".join(sorted(config.people))}.')
    return people


def _print_dry_run_cost(ctx: RunContext, people: PeopleList, domains: Sequence[str]) -> None:
    """The last two lines of a dry run: who would be skipped, then the most the rest can cost."""
    preview = preview_contacts(ctx, people, domains)
    typer.echo(f"Skipped: {preview.earlier_list} on an earlier list, {preview.emailed} where "
               f"you've emailed someone, {preview.no_one} with no one matching.")
    estimate = (f"Dry run: up to {preview.to_buy} companies would be bought, "
                f"at most {format_credits(max_cost(ctx, preview.to_buy))} credits")
    try:
        balance = ctx.people.balance_tenths()
    except Exception as exc:
        # This is the command someone runs because they are unsure they can afford a
        # run, so an unreadable balance still prints the estimate. It exits non-zero
        # so that a script driving this doesn't mistake the estimate for a full answer.
        _fail(f"{estimate}, but the credit balance could not be checked: {exc}", 1)
    typer.echo(f"{estimate}; {format_credits(balance)} left.")


def _write_report(ctx: RunContext, run_id: int, summary: RunSummary | None) -> Path:
    view = build_contact_view(ctx, run_id, summary)
    return write_contact_report(view, ctx.config.paths.reports)


def _print_run(summary: RunSummary, report: Path) -> None:
    typer.echo(f"Run {summary.run_id}: contacts bought at {summary.bought} companies, "
               f"{format_credits(summary.spent)} credits used. Report: {report}")
    if summary.out_of_credits:
        typer.echo(f"{summary.out_of_credits} companies were not bought because credits ran out.")


# --- companies -----------------------------------------------------------------------

@app.command()
def companies(
    sector: list[str] | None = typer.Option(
        None, "--sector", help='A sector name; repeat it for more ("outreach sectors" lists them).'),
    size: list[str] | None = typer.Option(
        None, "--size", help="A company size band such as 51-200; repeat it for more."),
    location: str | None = typer.Option(
        None, "--location",
        help='Where the company is based: "TX", "Austin, TX" or "Canada". Default: the US.'),
    people: str | None = typer.Option(
        None, "--people",
        help="The people list to buy. Default: companies.people in config.toml."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count the companies and price the run without starting it."),
    resume: int | None = typer.Option(
        None, "--resume",
        help="Finish an earlier company run, with the filters and people list it used."),
) -> None:
    """Find companies in Hunter's directory and buy the contacts there."""
    if resume is None and not sector:
        _fail("--sector is required")
    if resume is not None and dry_run:
        _fail("--dry-run can't be combined with --resume: a resume buys at companies "
              "an earlier run already found.")
    filters = _parse_filters(sector, size, location)
    config = _load_config()
    if resume is None:
        people_list = _people_list(config, people or config.companies_people)
    ctx = _context(config, need_llm=False)

    if resume is not None:
        # The run is finished with what it was started with, not with this call's flags.
        info = ctx.runs.info(resume)
        if info is None or info.kind != "companies":
            _fail(f"Run {resume} is not a company run.", 1)
        filters, people_list = info.filters, _people_list(config, info.people)

    if dry_run:
        _companies_dry_run(ctx, filters, people_list)
        return
    try:
        summary = run_companies(ctx, filters, people_list, resume)
    except HunterAPIError as exc:
        _company_search_failed(exc)
    _print_run(summary, _write_report(ctx, summary.run_id, summary))
    # A resume runs no search, so it has nothing to say about what Hunter found.
    if resume is None and summary.found.get("hunter", 0) == 0:
        typer.echo(_NO_COMPANY_MATCHED)


def _company_search_failed(exc: HunterAPIError) -> NoReturn:
    _fail(f"Hunter's company search failed: {exc}", 1)


def _companies_dry_run(ctx: RunContext, filters: RunFilters, people: PeopleList) -> None:
    """Everything a run would decide before spending a credit, and nothing it would store."""
    typer.echo(f"Filters: {describe_filters(filters)}")
    try:
        # Searched directly, not through run_companies, which would record a run.
        found = ctx.company_search.search(
            CompanyQuery(filters.sectors, filters.sizes, filters.location))
    except HunterAPIError as exc:
        _company_search_failed(exc)
    typer.echo(f"Hunter found {len(found)} companies.")
    if not found:
        typer.echo(_NO_COMPANY_MATCHED)
        return
    _print_dry_run_cost(ctx, people, [company.domain for company in found])


# --- sectors -------------------------------------------------------------------------

@app.command()
def sectors(
    word: str | None = typer.Argument(None, help="Show only the sectors containing this word."),
) -> None:
    """List Hunter's sector names, and the company size bands."""
    names = search_sectors(word, load_sectors())
    if not names:
        _fail(f'No sector contains "{word}".', 1)
    for name in names:
        typer.echo(name)
    typer.echo(f"Size bands: {', '.join(SIZE_BANDS)}")


# --- emailed -------------------------------------------------------------------------

def _addresses(texts: Sequence[str]) -> list[str]:
    """Each address in the form it is stored, once.

    A bad one ends the command here, before anything is written."""
    try:
        return list(dict.fromkeys(normalize_address(text) for text in texts))
    except ValueError as exc:
        _fail(str(exc))


def _day(text: str) -> date:
    try:
        day = date.fromisoformat(text)
    except ValueError:
        day = None
    # fromisoformat also reads "20261011" and "2026-W41-7"; only YYYY-MM-DD is asked for.
    if day is None or day.isoformat() != text:
        _fail(f'--on must be a date like 2026-10-11, not "{text}"')
    return day


@emailed_app.command("add")
def emailed_add(
    addresses: list[str] = typer.Argument(..., help="The addresses you've emailed."),
    on: str | None = typer.Option(
        None, "--on", help="The day you emailed them, as 2026-10-11. Default: today."),
) -> None:
    """Mark addresses as emailed, so their companies are skipped from now on."""
    stored = _addresses(addresses)
    day = None if on is None else _day(on)
    ctx = _offline_context()
    if day is None:
        day = ctx.today
    ctx.emailed.add(stored, day, datetime.now())
    noun = "address" if len(stored) == 1 else "addresses"
    typer.echo(f"Marked {len(stored)} {noun} as emailed on {day.isoformat()}.")


@emailed_app.command("list")
def emailed_list() -> None:
    """Show who is marked as emailed, newest first."""
    entries = _offline_context().emailed.all()
    if not entries:
        typer.echo("No one is marked as emailed yet.")
    for entry in entries:
        line = f"{entry.emailed_on.isoformat()}  {entry.address}"
        known = [part for part in (entry.full_name, entry.title, entry.company) if part]
        typer.echo(f"{line}  {' · '.join(known)}" if known else line)


@emailed_app.command("remove")
def emailed_remove(
    addresses: list[str] = typer.Argument(..., help="The addresses to take off the list."),
) -> None:
    """Take addresses off the emailed list."""
    stored = _addresses(addresses)
    removed = _offline_context().emailed.remove(stored)
    typer.echo(f"Removed {len(removed)}.")
    for address in stored:
        if address not in removed:
            typer.echo(f"Not on the list: {address}", err=True)
    if not removed:
        raise typer.Exit(code=1)


# --- report --------------------------------------------------------------------------

@app.command()
def report(
    run_id: int = typer.Argument(..., help="The run's id, as printed when it finished."),
) -> None:
    """Write a run's report again from what is stored."""
    ctx = _offline_context()
    info = ctx.runs.info(run_id)
    if info is None:
        _fail(f"No run with id {run_id}", 1)
    if info.schema_version < _FIRST_REPORTABLE_SCHEMA:
        _fail(f"Run {run_id} was made by an older version of this tool; "
              "see its saved HTML report.", 1)
    typer.echo(f"Report: {_write_report(ctx, run_id, None)}")


if __name__ == "__main__":
    app()
