from __future__ import annotations

import os
from dataclasses import replace
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
from outreach.core.workmode import parse_work_modes
from outreach.credits import format_credits
from outreach.llm.anthropic_client import LLMFormatError
from outreach.net.fetcher import Fetcher, build_user_agent
from outreach.pipeline.companies import run_companies
from outreach.pipeline.context import RunContext
from outreach.pipeline.contacts import max_cost, preview_contacts
from outreach.pipeline.jobs import SOURCES, find_job_prospects, run_jobs
from outreach.pipeline.summary import RunSummary
from outreach.render.contact_report import build_contact_view, write_contact_report
from outreach.sectors import load_sectors
from outreach.sources.companies import CompanyQuery
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.emailed import EmailedRepo
from outreach.store.runs import (CompanyInfoRepo, FetchAttemptRepo, HNPostRepo, PostingRepo,
                                 RunContactRepo, RunRepo)
from outreach.types import RunFilters, WorkMode

# An uncaught error must never print its locals: they hold the API keys. Newer typer releases
# default to False; older ones the project still allows default to True.
app = typer.Typer(help="Find the right people to email at the right companies.",
                  pretty_exceptions_show_locals=False)
emailed_app = typer.Typer(help="Keep track of who you've emailed.",
                          pretty_exceptions_show_locals=False)
app.add_typer(emailed_app, name="emailed")

CONFIG_PATH = Path("config.toml")
# Runs before this schema have no kind, people list or filters, so no report can be built from them.
_FIRST_REPORTABLE_SCHEMA = 4
_NO_COMPANY_MATCHED = ('No company matched these filters. Try another sector '
                       '("outreach sectors <word>") or a wider size or location.')
_NO_JOB_MATCHED = ("No company matched. Try another role, fewer filters, "
                   "or add board tokens in config.toml.")
_DRY_RUN_WITH_RESUME = ("--dry-run can't be combined with --resume: a resume buys at companies "
                        "an earlier run already found.")


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


def _print_dry_run_cost(ctx: RunContext, people: PeopleList, domains: Sequence[str],
                        might_add: int = 0) -> None:
    """The last lines of a dry run: who would be skipped, then the most the rest can cost.

    `might_add` is how many more companies a real run could find than the ones in `domains`
    (a job run's HN posts that only the LLM fallback can read); they are priced as if each
    would be bought, up to the run's cap, so that "at most" stays true."""
    try:
        preview = preview_contacts(ctx, people, domains)
    except HunterAPIError as exc:
        # The free Email Count calls can be rate limited or refused; a real run survives
        # that company by company, and a dry run must not end in a traceback either.
        _fail(f"Hunter's email count failed: {exc}", 1)
    typer.echo(f"Skipped: {preview.earlier_list} on an earlier list, {preview.emailed} where "
               f"you've emailed someone, {preview.no_one} with no one matching.")
    companies = min(ctx.config.run.max_companies, preview.to_buy + might_add)
    if companies > preview.to_buy:
        typer.echo(f"Includes up to {companies - preview.to_buy} more HN companies that the LLM "
                   "fallback might add in a real run.")
    estimate = (f"Dry run: up to {companies} companies would be bought, "
                f"at most {format_credits(max_cost(ctx, companies))} credits")
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


_CREDITS_ERROR = "credits: "  # how the run's own summary records an unreadable balance


def _end_run(summary: RunSummary) -> None:
    """What a finished run says last, once its report is written: whether it went wrong.

    A run that could not read the balance bought nothing, which is a failure, not a quiet
    success; any other error was survived company by company and is a warning."""
    for error in summary.errors:
        if error.startswith(_CREDITS_ERROR):
            _fail("The credit balance could not be checked, so nothing was bought: "
                  f"{error[len(_CREDITS_ERROR):]}", 1)
    if summary.errors:
        typer.echo(f"Warning: {len(summary.errors)} error(s) during this run; the report's "
                   "run details list them.", err=True)


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
        _fail(_DRY_RUN_WITH_RESUME)
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
    _end_run(summary)


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


# --- jobs ----------------------------------------------------------------------------

@app.command()
def jobs(
    role: str | None = typer.Option(
        None, "--role", help='The job title to look for, such as "Backend Engineer".'),
    sector: list[str] | None = typer.Option(
        None, "--sector",
        help='A sector name; repeat it for more ("outreach sectors" lists them).'),
    size: list[str] | None = typer.Option(
        None, "--size", help="A company size band such as 51-200; repeat it for more."),
    location: str | None = typer.Option(
        None, "--location",
        help='Where the job and the company are: "TX", "Austin, TX" or "Canada". '
             'Default: the US.'),
    work_mode: str | None = typer.Option(
        None, "--work-mode",
        help="Comma-separated remote, hybrid, onsite. Default: all three."),
    people: str | None = typer.Option(
        None, "--people", help="The people list to buy. Default: jobs.people in config.toml."),
    no_hn: bool = typer.Option(
        False, "--no-hn", help='Skip discovery from HN\'s "Who is hiring?" thread.'),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count the companies and price the run without starting it."),
    resume: int | None = typer.Option(
        None, "--resume",
        help="Finish an earlier job run, with the role, filters and people list it used."),
) -> None:
    """Find companies hiring for a role and buy the contacts there."""
    if resume is None and not (role and role.strip()):
        _fail("--role is required")
    if resume is not None and dry_run:
        _fail(_DRY_RUN_WITH_RESUME)
    try:
        work_modes = parse_work_modes(work_mode)
    except ValueError as exc:
        _fail(str(exc))
    filters = replace(_parse_filters(sector, size, location), work_modes=tuple(sorted(work_modes)))
    config = _load_config()
    if resume is None:
        people_list = _people_list(config, people or config.jobs_people)
    ctx = _context(config, need_llm=True)

    if resume is not None:
        # The run is finished with what it was started with, not with this call's flags.
        info = ctx.runs.info(resume)
        if info is None or info.kind != "jobs":
            _fail(f"Run {resume} is not a job run.", 1)
        role, filters, people_list = info.role, info.filters, _people_list(config, info.people)
        work_modes = frozenset(filters.work_modes)
    # Only now: building the context is what reads .env, where the address may be.
    _warn_about_the_setup(config, searching=resume is None, use_hn=not no_hn)

    if dry_run:
        _jobs_dry_run(ctx, role, filters, work_modes, people_list, use_hn=not no_hn)
        return
    try:
        summary = run_jobs(ctx, role, filters, work_modes, people_list, use_hn=not no_hn,
                           resume_run_id=resume)
    except _LLM_ERRORS as exc:
        _role_expansion_failed(exc)
    _print_run(summary, _write_report(ctx, summary.run_id, summary))
    # A resume runs no search, so it has nothing to say about what was found.
    if resume is None and not any(_found_counts(summary)):
        typer.echo(_NO_JOB_MATCHED)
    _end_run(summary)


# What expanding the role can raise: the request failed or was refused, or the reply isn't titles.
_LLM_ERRORS = (httpx.HTTPError, LLMFormatError)


def _role_expansion_failed(exc: Exception) -> NoReturn:
    """The first paid call of a job run failed, before any run was recorded."""
    _fail(f"Expanding the role into job titles failed: {exc}", 1)


def _warn_about_the_setup(config: Config, *, searching: bool, use_hn: bool) -> None:
    """Warn about the two setups that make a job run worse. Neither stops it."""
    if not os.environ.get("OUTREACH_CONTACT_EMAIL", "").strip():
        # The user agent still identifies the tool, but without a contact address a
        # site operator who notices this traffic has no one to reach about it.
        typer.echo(
            "Warning: OUTREACH_CONTACT_EMAIL is not set -- requests will "
            "identify this tool but name no one to contact about them.", err=True)
    discovery = config.discovery
    boards = discovery.greenhouse_tokens or discovery.ashby_tokens or discovery.lever_tokens
    if searching and not boards and not (use_hn and config.hn.enabled):
        typer.echo(
            "Warning: no board tokens in config.toml and HN discovery is off -- "
            "companies can only come from Hunter's hiring search.", err=True)


def _found_counts(summary: RunSummary) -> tuple[int, ...]:
    """How many companies each source found, in the order of `SOURCES`. A resume found none."""
    return tuple(summary.found.get(source, 0) for source in SOURCES)


def _jobs_dry_run(ctx: RunContext, role: str, filters: RunFilters,
                  work_modes: frozenset[WorkMode], people: PeopleList, *, use_hn: bool) -> None:
    """Everything a run would decide before spending a credit, and nothing it would store.

    Expanding the role is the one paid call, as in a run. Reading HN's posts needs no
    LLM here: the posts only the fallback could read are counted instead, and priced as
    companies a real run might add."""
    typer.echo(f"Filters: {describe_filters(filters)}")
    try:
        terms = ctx.llm.expand_titles(role)
    except _LLM_ERRORS as exc:
        _role_expansion_failed(exc)
    summary = RunSummary(run_id=0)  # there is no run, so no id
    prospects = find_job_prospects(ctx, terms, filters, work_modes, summary,
                                   use_hn=use_hn, dry_run=True)
    # A source that failed made the counts below an undercount; a run keeps these in its report.
    for error in summary.errors:
        typer.echo(f"Warning: {error}", err=True)
    if summary.hn_thread_title is not None:
        typer.echo(f"HN: {summary.hn_thread_title} — {summary.hn_posts_read} posts, "
                   f"{summary.hn_role_matched} match the role, {summary.hn_parsed} parsed, "
                   f"{summary.hn_needs_llm} would need the LLM fallback, "
                   f"{summary.hn_kept} kept after the cap.")
    board, hn, hiring = _found_counts(summary)
    typer.echo(f"Found: {board} from job boards, {hn} from HN, {hiring} from Hunter's hiring "
               f"search; {summary.left_out} left out by your filters.")
    kept = [prospect.domain for prospect in prospects if prospect.left_out is None]
    if not kept:
        typer.echo(_NO_JOB_MATCHED)
        return
    # The posts only the LLM fallback can read are not priced above, yet a real run may keep
    # companies from them, within the fallback's budget and HN's cap.
    hn = ctx.config.hn
    might_add = min(summary.hn_needs_llm, hn.llm_fallback_max,
                    max(0, hn.max_new_companies - summary.hn_kept))
    _print_dry_run_cost(ctx, people, kept, might_add)


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
