import contextlib
import re
import socket
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from outreach import cli
from outreach.cli import app, build_context
from outreach.config import DEFAULT_PEOPLE, ConfigError, load_config
from outreach.contacts.fake import FakePeopleFinder
from outreach.contacts.hunter import HunterAPIError
from outreach.llm.anthropic_client import LLMFormatError
from outreach.llm.fake import FakeLLM
from outreach.sources.companies import (CompanyQuery, FakeCompanySearch, FoundCompany,
                                        HunterCompanySearch)
from outreach.sources.hn import FakeHNSource
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.runs import RunRepo
from outreach.types import FoundPerson, Location, PersonRef, PostingRef, RunFilters
from tests.pipeline.factories import hn_thread

_TEST_CONFIG = """\
[paths]
db = "data/pipeline.db"
reports = "reports"

[discovery]
greenhouse_tokens = []
"""

runner = CliRunner()
DB = Path("data/pipeline.db")
SPONSORS_FIRST_TIER = DEFAULT_PEOPLE["sponsors"].tiers[0]
HIRING_FIRST_TIER = DEFAULT_PEOPLE["hiring"].tiers[0]
NO_MATCH = ('No company matched these filters. Try another sector ("outreach sectors <word>") '
            "or a wider size or location.")
NO_JOB_MATCH = ("No company matched. Try another role, fewer filters, or add board tokens "
                "in config.toml.")
NO_CONTACT_EMAIL = ("Warning: OUTREACH_CONTACT_EMAIL is not set -- requests will identify this "
                    "tool but name no one to contact about them.")
NO_TOKENS_NO_HN = ("Warning: no board tokens in config.toml and HN discovery is off -- companies "
                   "can only come from Hunter's hiring search.")
NO_BALANCE = ("The credit balance could not be checked, so nothing was bought: "
              "Hunter account returned HTTP 401")
RUN_ERRORS_WARNING = "Warning: 1 error(s) during this run; the report's run details list them."
# The recorded "Who is hiring?" thread, read for the role "Backend Engineer" in the US.
HN_FUNNEL = ("HN: Ask HN: Who is hiring? (September 2026) — 11 posts, 8 match the role, "
             "5 parsed, 3 would need the LLM fallback, 4 kept after the cap.")
SOLO_POST = (303, 'Solo Labs | Backend Engineer | Remote (US) | REMOTE<p>We build streaming payroll '
                  'for restaurants. <a href="https://sololabs.ai">sololabs.ai</a>')
FERNWOOD_POST = (304, 'Fernwood | Backend Engineer | Remote (US) | REMOTE<p>We make field-service '
                      'software. <a href="https://fernwood.dev">fernwood.dev</a>')


@pytest.fixture(autouse=True)
def isolated_from_the_users_setup(monkeypatch, tmp_path):
    """Never let a test see the user's real .env, config or data.

    `build_context` calls `load_dotenv()`, which walks up from the package
    directory and finds the repo's real .env, so a test that deletes API
    keys from the environment gets them straight back and runs the live
    pipeline with real credentials. The relative config and data paths are
    pointed at a scratch directory for the same reason.
    """
    monkeypatch.setattr("outreach.cli.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(_TEST_CONFIG, encoding="utf-8")


@pytest.fixture(autouse=True)
def refuse_real_connections(monkeypatch):
    """A second line of defence: whatever a command builds, no socket can be opened,
    so not even a mistake in a test can reach Hunter, Anthropic, HN or a job board."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


# --- helpers -----------------------------------------------------------------------

def _fakes(monkeypatch):
    """Fake adapters on, and no key from the user's own environment in reach."""
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)


def _no_keys_no_fakes(monkeypatch):
    for name in ("OUTREACH_FAKE_ADAPTERS", "ANTHROPIC_API_KEY", "HUNTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _context_is_never_built(monkeypatch):
    """For a test that exits before the context: building it would be a failure."""
    def boom(*args, **kwargs):
        raise AssertionError("build_context was called")
    monkeypatch.setattr("outreach.cli.build_context", boom)


def _company(letter: str) -> FoundCompany:
    return FoundCompany(f"{letter}.example", f"{letter.upper()} Co", "Software Development", "51-200")


def _hunter_finds(monkeypatch, *letters, people=3, balance=500, queries=None):
    """Hunter's company search finds `letters`, each with `people` to buy, and the account has
    `balance`. The searches made are appended to `queries`."""
    def search(self, query):
        if queries is not None:
            queries.append(query)
        return [_company(letter) for letter in letters]
    monkeypatch.setattr(FakeCompanySearch, "search", search)
    monkeypatch.setattr(FakePeopleFinder, "count_people", lambda self, domain: people)
    monkeypatch.setattr(FakePeopleFinder, "balance_tenths", lambda self: balance)


def _someone_to_buy(monkeypatch, first_tier, searched=None):
    """Hunter has one person per company, found by the first tier of the people list and
    confirmed a day ago, so the only cost is the one email (1 credit)."""
    def search_people(self, domain, titles, limit):
        if searched is not None:
            searched.append(tuple(titles))
        if tuple(titles) != first_tier:
            return []
        return [FoundPerson("Dana Okafor", "Head of Partnerships", f"dana@{domain}", None,
                            "valid", date.today())]
    monkeypatch.setattr(FakePeopleFinder, "search_people", search_people)


def _hn_has(monkeypatch, thread, reads=None):
    """The fake HN source returns `thread`; each time it is read, 1 is appended to `reads`."""
    def latest_thread(self):
        if reads is not None:
            reads.append(1)
        return thread
    monkeypatch.setattr(FakeHNSource, "latest_thread", latest_thread)


def _boards_have(monkeypatch, *postings):
    monkeypatch.setattr(FakeJobBoardSource, "search",
                        lambda self, role_terms, region: list(postings))


def _board_posting(domain, mode):
    where = "Remote" if mode == "remote" else "Austin, TX"
    return PostingRef(domain.split(".")[0].title(), domain, "Backend Engineer",
                      f"https://boards.example/{domain}/1", where, mode, "full_time")


def _report_path(output: str) -> Path:
    match = re.search(r"Report: (.+)$", output, re.MULTILINE)
    assert match, output
    return Path(match.group(1))


def _count(table: str) -> int:
    with contextlib.closing(connect(DB)) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _start_run(kind="companies", people="sponsors", filters=RunFilters(), schema_version=None):
    with contextlib.closing(connect(DB)) as conn:
        run_id = RunRepo(conn).start(kind, "" if kind == "companies" else "Backend Engineer",
                                     filters, people, datetime(2026, 9, 21, 9))
        if schema_version is not None:
            conn.execute("UPDATE runs SET schema_version = ? WHERE id = ?", (schema_version, run_id))
            conn.commit()
    return run_id


# --- build_context -----------------------------------------------------------------

def test_real_adapters_build_one_source_per_configured_board(monkeypatch, tmp_path):
    """Construction only -- no request is made, so this touches no network.
    A board with an empty token list is left out rather than built to search
    nothing. The people finder is Hunter, which needs its key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.delenv("OUTREACH_FAKE_ADAPTERS", raising=False)
    config = tmp_path / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "greenhouse_tokens = []",
        'greenhouse_tokens = ["g"]\nlever_tokens = ["l1", "l2"]'), encoding="utf-8")

    ctx = build_context(load_config(Path("config.toml")), need_llm=True)

    assert [type(s).__name__ for s in ctx.job_board.sources] == [
        "GreenhouseBoardSource", "LeverBoardSource"]
    assert ctx.job_board.sources[1].tokens == ("l1", "l2")
    assert type(ctx.llm).__name__ == "AnthropicLLM"
    assert type(ctx.people).__name__ == "HunterProvider"
    assert type(ctx.company_search).__name__ == "HunterCompanySearch"


def test_real_adapters_need_a_hunter_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.delenv("OUTREACH_FAKE_ADAPTERS", raising=False)

    with pytest.raises(ConfigError, match="HUNTER_API_KEY"):
        build_context(load_config(Path("config.toml")), need_llm=True)


def test_a_context_asks_only_for_the_keys_it_needs(monkeypatch):
    """Construction only. A companies run has no use for the LLM, and the emailed and
    report commands have no use for either service."""
    _no_keys_no_fakes(monkeypatch)
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    config = load_config(Path("config.toml"))

    ctx = build_context(config, need_llm=False)
    assert ctx.llm is None
    assert type(ctx.people).__name__ == "HunterProvider"

    monkeypatch.delenv("HUNTER_API_KEY")
    ctx = build_context(config, need_llm=False, need_hunter=False)
    assert (ctx.llm, ctx.people, ctx.company_search) == (None, None, None)


def test_fake_adapters_find_people_without_any_credits(monkeypatch):
    """A fake run can never buy anyone: the fake finder starts with no balance."""
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")

    ctx = build_context(load_config(Path("config.toml")), need_llm=False)

    assert type(ctx.people).__name__ == "FakePeopleFinder"
    assert ctx.people.balance_tenths() == 0
    assert type(ctx.company_search).__name__ == "FakeCompanySearch"


def test_fake_mode_cannot_reach_the_network(monkeypatch):
    """The fetcher in fake mode answers 404 to everything from a mock transport, so even
    a page a test forgot to fake can't be fetched. (The host cannot resolve, and the
    autouse fixture refuses sockets, so a real transport would fail rather than answer 404.)"""
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    ctx = build_context(load_config(Path("config.toml")), need_llm=True)

    fetched = ctx.fetcher.get("https://nothing.invalid/careers")

    assert (fetched.outcome, fetched.status) == ("http_error", 404)
    assert isinstance(ctx.fetcher.client._transport, httpx.MockTransport)


# --- companies: checks before anything runs ----------------------------------------

def test_companies_needs_only_the_hunter_key(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("HUNTER_API_KEY", "test")

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 0, result.output


def test_missing_hunter_key_aborts_before_any_network_call(monkeypatch, tmp_path):
    _no_keys_no_fakes(monkeypatch)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 2
    assert "HUNTER_API_KEY" in result.output
    assert not (tmp_path / "data").exists()  # the keys are checked before the database is opened


def test_companies_without_a_sector_exits_2(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["companies", "--dry-run"])

    assert result.exit_code == 2
    assert "--sector is required" in result.output


def test_unknown_sector_exits_2_before_anything_runs(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["companies", "--sector", "software developement"])

    assert result.exit_code == 2
    assert "Did you mean: Software Development" in result.output


@pytest.mark.parametrize("flags, message", [
    (["--size", "5"], 'Unknown size band "5"'),
    (["--location", "Atlantis"], 'Can\'t read location "Atlantis"'),
])
def test_bad_size_and_location_exit_2(monkeypatch, flags, message):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", *flags])

    assert result.exit_code == 2
    assert message in result.output


def test_unknown_people_list_exits_2(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(
        app, ["companies", "--sector", "Software Development", "--people", "nobody"])

    assert result.exit_code == 2
    assert 'Unknown people list "nobody". Known lists: hiring, sponsors.' in result.output


def test_a_dry_run_cannot_resume(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["companies", "--resume", "1", "--dry-run"])

    assert result.exit_code == 2
    assert "--dry-run" in result.output and "--resume" in result.output


def test_a_broken_config_exits_2(monkeypatch):
    _fakes(monkeypatch)
    Path("config.toml").write_text("[paths\ndb = ", encoding="utf-8")

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 2
    assert "config.toml" in result.output


def test_ignored_config_settings_warn(monkeypatch):
    _fakes(monkeypatch)
    Path("config.toml").write_text(_TEST_CONFIG + "\n[gate]\nrecency_days = 180\n", encoding="utf-8")

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert ("Warning: config.toml settings this version no longer uses were ignored: [gate]"
            in result.stderr)
    assert "Warning" not in result.stdout


# --- companies: dry run ------------------------------------------------------------

def test_a_sector_with_a_comma_is_one_sector(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(
        app, ["companies", "--sector", "Health, Wellness and Fitness", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Filters: Health, Wellness and Fitness · any size · United States" in result.stdout


def test_sectors_and_sizes_repeat_as_flags(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, [
        "companies", "--sector", "Banking", "--sector", "Health, Wellness and Fitness",
        "--size", "11-50", "--size", "51-200", "--location", "Austin, TX", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert ("Filters: Banking; Health, Wellness and Fitness · 11-50, 51-200 · Austin, TX"
            in result.stdout)


def test_companies_dry_run_reports_the_maximum_cost(monkeypatch):
    _fakes(monkeypatch)
    queries = []
    _hunter_finds(monkeypatch, "a", "b", "c", people=3, balance=500, queries=queries)

    result = runner.invoke(app, ["companies", "--sector", "Software Development",
                                 "--size", "51-200", "--location", "TX", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Filters: Software Development · 51-200 · Texas",
        "Hunter found 3 companies.",
        "Skipped: 0 on an earlier list, 0 where you've emailed someone, 0 with no one matching.",
        "Dry run: up to 3 companies would be bought, at most 13.5 credits; 50 left.",
    ]
    assert queries == [CompanyQuery(("Software Development",), ("51-200",), Location("US", "TX"))]


def test_a_search_without_a_location_asks_for_the_us_not_for_anywhere(monkeypatch):
    _fakes(monkeypatch)
    queries = []
    _hunter_finds(monkeypatch, "a", queries=queries)

    dry = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])
    real = runner.invoke(app, ["companies", "--sector", "Software Development"])

    assert dry.exit_code == 0 and real.exit_code == 0
    assert queries == [CompanyQuery(("Software Development",), (), Location("US"))] * 2


def test_a_dry_run_starts_no_run_and_writes_nothing(monkeypatch, tmp_path):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert [_count(table) for table in ("runs", "companies", "run_company_info", "contacts")] == [0] * 4
    assert not (tmp_path / "reports").exists()


def test_companies_dry_run_counts_what_it_would_skip(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", "c", people=3, balance=500)
    monkeypatch.setattr(FakePeopleFinder, "count_people",
                        lambda self, domain: 0 if domain == "b.example" else 3)
    assert runner.invoke(app, ["emailed", "add", "dana@a.example"]).exit_code == 0

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[2:] == [
        "Skipped: 0 on an earlier list, 1 where you've emailed someone, 1 with no one matching.",
        "Dry run: up to 1 companies would be bought, at most 4.5 credits; 50 left.",
    ]


def test_companies_dry_run_with_an_unreadable_balance_exits_1(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", "c")

    def unreadable(self):
        raise HunterAPIError("Hunter account returned HTTP 401")
    monkeypatch.setattr(FakePeopleFinder, "balance_tenths", unreadable)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Dry run: up to 3 companies would be bought, at most 13.5 credits, but the credit "
        "balance could not be checked: Hunter account returned HTTP 401")
    assert "Dry run" not in result.stdout
    assert "Hunter found 3 companies." in result.stdout


def test_companies_dry_run_with_a_failing_email_count_exits_1(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")

    def rate_limited(self, domain):
        raise HunterAPIError("Hunter email-count returned HTTP 429")
    monkeypatch.setattr(FakePeopleFinder, "count_people", rate_limited)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 1
    assert result.stderr.strip() == "Hunter's email count failed: Hunter email-count returned HTTP 429"
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no traceback
    assert "Hunter found 2 companies." in result.stdout
    assert "Dry run" not in result.stdout and "Skipped" not in result.stdout


@pytest.mark.parametrize("extra", [["--dry-run"], []])
def test_companies_with_no_match_says_why(monkeypatch, extra):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", *extra])

    assert result.exit_code == 0, result.output
    assert NO_MATCH in result.stdout.splitlines()
    if extra:
        assert "Hunter found 0 companies." in result.stdout
        assert "Dry run:" not in result.stdout  # nothing to price, so no balance is read
    else:  # the run is still recorded, and says so before saying why it is empty
        assert result.stdout.startswith("Run 1: contacts bought at 0 companies, 0 credits used.")


@pytest.mark.parametrize("extra", [["--dry-run"], []])
def test_a_hunter_search_failure_exits_1(monkeypatch, extra):
    _fakes(monkeypatch)

    def rate_limited(self, query):
        raise HunterAPIError("Hunter discover returned HTTP 429")
    monkeypatch.setattr(FakeCompanySearch, "search", rate_limited)

    result = runner.invoke(app, ["companies", "--sector", "Software Development", *extra])

    assert result.exit_code == 1
    assert result.stderr.strip() == "Hunter's company search failed: Hunter discover returned HTTP 429"
    assert _count("runs") == 0  # the search comes before the run is recorded


def test_a_dry_run_with_hunter_unreachable_exits_1_without_a_traceback(monkeypatch):
    """Offline, or a DNS failure or a timeout, reaches the user as a message, not a traceback."""
    _fakes(monkeypatch)

    def offline(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
    hunter = HunterCompanySearch("sk-secret", client=httpx.Client(transport=httpx.MockTransport(offline)))
    monkeypatch.setattr(FakeCompanySearch, "search", lambda self, query: hunter.search(query))

    result = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])

    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Hunter's company search failed: Hunter discover could not be reached (ConnectError)")
    assert "sk-secret" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no traceback


# --- companies: real run -----------------------------------------------------------

def test_companies_run_writes_a_report(monkeypatch, tmp_path):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")
    _someone_to_buy(monkeypatch, SPONSORS_FIRST_TIER)

    result = runner.invoke(app, ["companies", "--sector", "Software Development"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 2 companies, 2 credits used. Report: ")
    assert "ran out" not in result.stdout and NO_MATCH not in result.stdout
    assert result.stderr == ""  # a run with nothing wrong says nothing about errors
    path = _report_path(result.stdout)
    assert path.exists() and path.parent == Path("reports")
    assert [p.name for p in (tmp_path / "reports").iterdir()] == [path.name]
    html = path.read_text(encoding="utf-8")
    assert "A Co" in html and "dana@a.example" in html


def test_companies_run_says_when_credits_ran_out(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", balance=0)

    result = runner.invoke(app, ["companies", "--sector", "Software Development"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Run 1: contacts bought at 0 companies, 0 credits used. Report: ")
    assert lines[1:] == ["2 companies were not bought because credits ran out."]


def _balance_unreadable(monkeypatch):
    def unreadable(self):
        raise HunterAPIError("Hunter account returned HTTP 401")
    monkeypatch.setattr(FakePeopleFinder, "balance_tenths", unreadable)


def _count_fails_for(monkeypatch, domain):
    """Hunter refuses the free email count for one company; the others are counted as 3."""
    def count_people(self, asked):
        if asked == domain:
            raise HunterAPIError("Hunter email-count returned HTTP 429")
        return 3
    monkeypatch.setattr(FakePeopleFinder, "count_people", count_people)


def test_companies_run_with_an_unreadable_balance_exits_1(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")
    _balance_unreadable(monkeypatch)

    result = runner.invoke(app, ["companies", "--sector", "Software Development"])

    assert result.exit_code == 1
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 0 companies, 0 credits used. Report: ")
    assert result.stderr.splitlines() == [NO_BALANCE]
    assert _report_path(result.stdout).exists()  # the report is written before the run ends in an error


def test_companies_run_with_a_failing_company_warns_and_exits_0(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")
    _someone_to_buy(monkeypatch, SPONSORS_FIRST_TIER)
    _count_fails_for(monkeypatch, "a.example")

    result = runner.invoke(app, ["companies", "--sector", "Software Development"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 1 companies, 1 credits used. Report: ")
    assert result.stderr.splitlines() == [RUN_ERRORS_WARNING]
    assert _report_path(result.stdout).exists()


def test_companies_uses_the_configured_people_list_unless_told_otherwise(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a")
    Path("config.toml").write_text(_TEST_CONFIG + '\n[companies]\npeople = "hiring"\n', encoding="utf-8")

    default = runner.invoke(app, ["companies", "--sector", "Software Development"])
    named = runner.invoke(app, ["companies", "--sector", "Software Development",
                                "--people", "sponsors"])

    assert default.exit_code == 0 and named.exit_code == 0
    with contextlib.closing(connect(DB)) as conn:
        runs = RunRepo(conn)
        assert (runs.info(1).people, runs.info(2).people) == ("hiring", "sponsors")


# --- companies --resume ------------------------------------------------------------

def test_resume_buys_at_the_runs_own_companies_with_its_own_people_and_filters(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", balance=0)
    first = runner.invoke(app, ["companies", "--sector", "Software Development", "--size", "51-200",
                                "--location", "TX", "--people", "hiring"])
    assert first.exit_code == 0, first.output
    assert "2 companies were not bought" in first.stdout

    # Credits arrive. Resuming must not search again, and --people names another list.
    def no_search(self, query):
        raise AssertionError("a resume must not search Hunter again")
    monkeypatch.setattr(FakeCompanySearch, "search", no_search)
    monkeypatch.setattr(FakePeopleFinder, "balance_tenths", lambda self: 500)
    searched = []
    _someone_to_buy(monkeypatch, HIRING_FIRST_TIER, searched)
    seen = []
    real = cli.run_companies

    def spy(ctx, filters, people, resume_run_id=None):
        seen.append((filters, people.name, resume_run_id))
        return real(ctx, filters, people, resume_run_id)
    monkeypatch.setattr("outreach.cli.run_companies", spy)

    result = runner.invoke(app, ["companies", "--resume", "1", "--people", "sponsors"])

    assert result.exit_code == 0, result.output
    assert seen == [(RunFilters(("Software Development",), ("51-200",), Location("US", "TX")),
                     "hiring", 1)]
    assert searched and set(searched) <= set(DEFAULT_PEOPLE["hiring"].tiers)
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 2 companies, 2 credits used. Report: ")
    assert NO_MATCH not in result.stdout  # a resume reports no search, so "found nothing" is not true
    assert _count("runs") == 1


@pytest.mark.parametrize("run_id", [1, 99])  # a jobs run, and no run at all
def test_resuming_what_is_not_a_company_run_exits_1(monkeypatch, run_id):
    _fakes(monkeypatch)
    _start_run(kind="jobs", people="hiring")

    result = runner.invoke(app, ["companies", "--resume", str(run_id)])

    assert result.exit_code == 1
    assert result.stderr.strip() == f"Run {run_id} is not a company run."


def test_resuming_a_run_whose_people_list_is_gone_exits_2(monkeypatch):
    _fakes(monkeypatch)
    _start_run(people="gone")

    result = runner.invoke(app, ["companies", "--resume", "1"])

    assert result.exit_code == 2
    assert 'Unknown people list "gone". Known lists: hiring, sponsors.' in result.stderr


# --- jobs: checks before anything runs ---------------------------------------------

def test_jobs_needs_the_anthropic_key(monkeypatch, tmp_path):
    _no_keys_no_fakes(monkeypatch)
    monkeypatch.setenv("HUNTER_API_KEY", "test")  # only the Anthropic key is missing

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run"])

    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output
    assert not (tmp_path / "data").exists()  # the keys are checked before the database is opened


@pytest.mark.parametrize("flags", [[], ["--role", "  "]])
def test_jobs_without_a_role_exits_2(monkeypatch, flags):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["jobs", *flags, "--dry-run"])

    assert result.exit_code == 2
    assert "--role is required" in result.output


def test_bad_work_mode_exits_before_anything_runs(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(
        app, ["jobs", "--role", "Backend Engineer", "--work-mode", "sometimes", "--dry-run"])

    assert result.exit_code == 2
    assert "sometimes" in result.output


@pytest.mark.parametrize("args, config, message", [
    # Each case would fail at a later check too; the first check in the order is the one heard.
    pytest.param(["--work-mode", "sometimes"], _TEST_CONFIG, "--role is required", id="role"),
    pytest.param(["--role", "X", "--work-mode", "sometimes", "--sector", "software developement"],
                 "[paths\ndb = ", "sometimes", id="work-mode"),
    pytest.param(["--role", "X", "--sector", "software developement"], "[paths\ndb = ",
                 "Did you mean: Software Development", id="filters"),
    pytest.param(["--role", "X", "--people", "nobody"], "[paths\ndb = ", "config.toml",
                 id="config"),
    pytest.param(["--role", "X", "--people", "nobody"], _TEST_CONFIG,
                 'Unknown people list "nobody"', id="people"),
])
def test_jobs_checks_its_arguments_in_a_fixed_order(monkeypatch, args, config, message):
    _context_is_never_built(monkeypatch)
    Path("config.toml").write_text(config, encoding="utf-8")

    result = runner.invoke(app, ["jobs", *args, "--dry-run"])

    assert result.exit_code == 2
    assert message in result.output


def test_a_jobs_dry_run_cannot_resume(monkeypatch):
    _context_is_never_built(monkeypatch)

    result = runner.invoke(app, ["jobs", "--resume", "1", "--dry-run"])

    assert result.exit_code == 2
    assert "--dry-run" in result.output and "--resume" in result.output


# --- jobs: warnings ----------------------------------------------------------------

@pytest.mark.parametrize("off", ["flag", "config"])
def test_warns_when_there_are_no_tokens_and_hn_is_off(monkeypatch, off):
    _fakes(monkeypatch)
    flags = ["--no-hn"] if off == "flag" else []
    if off == "config":
        Path("config.toml").write_text(_TEST_CONFIG + "\n[hn]\nenabled = false\n", encoding="utf-8")

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run", *flags])

    assert result.exit_code == 0, result.output
    assert NO_TOKENS_NO_HN in result.stderr.splitlines()
    assert "Warning" not in result.stdout


@pytest.mark.parametrize("tokens, flags", [
    ('lever_tokens = ["acme"]', ["--no-hn"]),  # HN is off, but a board is searched
    ("", []),  # no board, but HN is searched
])
def test_there_is_no_token_warning_while_a_source_remains(monkeypatch, tokens, flags):
    _fakes(monkeypatch)
    Path("config.toml").write_text(
        _TEST_CONFIG.replace("greenhouse_tokens = []", f"greenhouse_tokens = []\n{tokens}"),
        encoding="utf-8")

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run", *flags])

    assert result.exit_code == 0, result.output
    assert NO_TOKENS_NO_HN not in result.stderr


def test_the_contact_email_warning_is_for_jobs_only(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.delenv("OUTREACH_CONTACT_EMAIL", raising=False)
    jobs = ["jobs", "--role", "Backend Engineer", "--dry-run"]

    blank = runner.invoke(app, jobs)
    companies = runner.invoke(app, ["companies", "--sector", "Software Development", "--dry-run"])
    monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "   ")
    spaces = runner.invoke(app, jobs)
    monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "me@example.com")
    given = runner.invoke(app, jobs)

    assert all(r.exit_code == 0 for r in (blank, companies, spaces, given))
    assert NO_CONTACT_EMAIL in blank.stderr.splitlines()
    assert NO_CONTACT_EMAIL in spaces.stderr.splitlines()
    assert "OUTREACH_CONTACT_EMAIL" not in companies.stderr
    assert "OUTREACH_CONTACT_EMAIL" not in given.stderr
    assert "Warning" not in blank.stdout


def test_a_contact_email_in_dot_env_is_not_warned_about(monkeypatch):
    """.env is read when the context is built, so the address is looked for after that."""
    _fakes(monkeypatch)
    monkeypatch.delenv("OUTREACH_CONTACT_EMAIL", raising=False)
    monkeypatch.setattr(
        "outreach.cli.load_dotenv",
        lambda *args, **kwargs: monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "me@example.com"))

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "OUTREACH_CONTACT_EMAIL" not in result.stderr


# --- jobs: dry run -----------------------------------------------------------------

def test_jobs_dry_run_reports_the_hn_funnel_without_llm_calls(monkeypatch):
    _fakes(monkeypatch)
    _hn_has(monkeypatch, hn_thread())
    asked = []

    def parse_job_post(self, text):
        asked.append(text)
        raise AssertionError("a dry run called the LLM fallback")
    monkeypatch.setattr(FakeLLM, "parse_job_post", parse_job_post)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert HN_FUNNEL in lines
    assert ("Found: 0 from job boards, 4 from HN, 0 from Hunter's hiring search; "
            "0 left out by your filters.") in lines
    assert asked == []


@pytest.mark.parametrize("extra", [["--dry-run"], []])
def test_no_hn_flag_disables_discovery(monkeypatch, extra):
    _fakes(monkeypatch)
    reads = []
    _hn_has(monkeypatch, hn_thread(), reads)

    flagged = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--no-hn", *extra])
    assert flagged.exit_code == 0, flagged.output
    assert reads == [] and "HN:" not in flagged.stdout

    unflagged = runner.invoke(app, ["jobs", "--role", "Backend Engineer", *extra])
    assert unflagged.exit_code == 0, unflagged.output
    assert reads == [1]
    if extra:
        assert "HN: Ask HN: Who is hiring?" in unflagged.stdout


def test_jobs_dry_run_counts_each_source(monkeypatch):
    _fakes(monkeypatch)
    queries = []
    _hunter_finds(monkeypatch, "h", people=3, balance=500, queries=queries)
    asked = []

    def count_people(self, domain):
        asked.append(domain)
        return 3
    monkeypatch.setattr(FakePeopleFinder, "count_people", count_people)
    _boards_have(monkeypatch, _board_posting("lakeside.example", "remote"),
                 _board_posting("onsite.example", "onsite"))
    _hn_has(monkeypatch, hn_thread(item_ids=[], extra=[SOLO_POST, FERNWOOD_POST]))

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--work-mode", "remote",
                                 "--location", "US", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "Filters: any sector · any size · United States · remote",
        "HN: Ask HN: Who is hiring? (September 2026) — 2 posts, 2 match the role, 2 parsed, "
        "0 would need the LLM fallback, 2 kept after the cap.",
        "Found: 1 from job boards, 2 from HN, 1 from Hunter's hiring search; "
        "1 left out by your filters.",
        "Skipped: 0 on an earlier list, 0 where you've emailed someone, 0 with no one matching.",
        "Dry run: up to 4 companies would be bought, at most 18 credits; 50 left.",
    ]
    # Priced in the order a run buys at them, and without the company the work mode left out.
    assert asked == ["lakeside.example", "sololabs.ai", "fernwood.dev", "h.example"]
    assert queries == [CompanyQuery((), (), Location("US"), hiring_titles=("Backend Engineer",))]


def test_a_jobs_dry_run_starts_no_run_and_writes_nothing(monkeypatch, tmp_path):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")
    _boards_have(monkeypatch, _board_posting("lakeside.example", "remote"))
    _hn_has(monkeypatch, hn_thread())

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert [_count(table) for table in ("runs", "companies", "run_company_info", "contacts",
                                        "run_postings", "run_hn_posts")] == [0] * 6
    assert not (tmp_path / "reports").exists()


def test_a_jobs_dry_run_says_which_source_failed(monkeypatch):
    _fakes(monkeypatch)

    def rate_limited(self, query):
        raise HunterAPIError("Hunter discover returned HTTP 429")
    monkeypatch.setattr(FakeCompanySearch, "search", rate_limited)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--dry-run"])

    assert result.exit_code == 0, result.output  # the other sources still count, as in a real run
    assert "Warning: hunter: Hunter discover returned HTTP 429" in result.stderr.splitlines()
    assert ("Found: 0 from job boards, 0 from HN, 0 from Hunter's hiring search; "
            "0 left out by your filters.") in result.stdout.splitlines()


_LLM_URL = "https://api.anthropic.com/v1/messages"
_REJECTED = "Client error '401 Unauthorized' for url '" + _LLM_URL + "'"


@pytest.mark.parametrize("extra", [["--dry-run"], []])
@pytest.mark.parametrize("error", [
    pytest.param(lambda: httpx.HTTPStatusError(
        _REJECTED, request=httpx.Request("POST", _LLM_URL), response=httpx.Response(401)),
        id="rejected"),
    pytest.param(lambda: httpx.ConnectError("All connection attempts failed"), id="unreachable"),
    pytest.param(lambda: LLMFormatError("model reply did not parse as JSON after one retry: 'x'"),
                 id="not-json"),
])
def test_a_failed_role_expansion_exits_1_without_a_traceback(monkeypatch, extra, error):
    _fakes(monkeypatch)
    monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "me@example.com")  # so stderr has one line only
    failure = error()

    def expand_titles(self, role_title):
        raise failure
    monkeypatch.setattr(FakeLLM, "expand_titles", expand_titles)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", *extra])

    assert result.exception is None or isinstance(result.exception, SystemExit)  # no traceback
    assert result.exit_code == 1
    assert result.stderr.strip() == f"Expanding the role into job titles failed: {failure}"
    assert "Run 1" not in result.stdout and "Dry run" not in result.stdout
    assert _count("runs") == 0  # the role is expanded before the run is recorded


@pytest.mark.parametrize("extra", [["--dry-run"], []])
def test_jobs_with_no_match_says_why(monkeypatch, extra):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", *extra])

    assert result.exit_code == 0, result.output
    assert NO_JOB_MATCH in result.stdout.splitlines()
    if extra:
        assert "Dry run:" not in result.stdout  # nothing to price, so no balance is read
    else:  # the run is still recorded, and says so before saying why it is empty
        assert result.stdout.startswith("Run 1: contacts bought at 0 companies, 0 credits used.")


# --- jobs: real run ----------------------------------------------------------------

def test_jobs_run_writes_a_report(monkeypatch, tmp_path):
    _fakes(monkeypatch)
    queries = []
    _hunter_finds(monkeypatch, "a", "b", queries=queries)
    _someone_to_buy(monkeypatch, HIRING_FIRST_TIER)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 2 companies, 2 credits used. Report: ")
    assert "ran out" not in result.stdout and NO_JOB_MATCH not in result.stdout
    path = _report_path(result.stdout)
    assert path.exists() and path.parent == Path("reports")
    assert [p.name for p in (tmp_path / "reports").iterdir()] == [path.name]
    html = path.read_text(encoding="utf-8")
    assert "A Co" in html and "dana@a.example" in html
    assert queries == [CompanyQuery((), (), Location("US"), hiring_titles=("Backend Engineer",))]
    with contextlib.closing(connect(DB)) as conn:
        info = RunRepo(conn).info(1)
    assert (info.kind, info.role, info.people) == ("jobs", "Backend Engineer", "hiring")


@pytest.mark.parametrize("flags, stored", [
    ([], ("hybrid", "onsite", "remote")),
    (["--work-mode", "remote, hybrid"], ("hybrid", "remote")),
])
def test_jobs_stores_the_work_modes_sorted(monkeypatch, flags, stored):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a")

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--sector", "Banking",
                                 "--size", "51-200", "--location", "TX", *flags])

    assert result.exit_code == 0, result.output
    with contextlib.closing(connect(DB)) as conn:
        info = RunRepo(conn).info(1)
    assert info.filters == RunFilters(("Banking",), ("51-200",), Location("US", "TX"), stored)


def test_jobs_says_when_credits_ran_out(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", balance=0)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("Run 1: contacts bought at 0 companies, 0 credits used. Report: ")
    assert lines[1:] == ["2 companies were not bought because credits ran out."]


def test_jobs_run_with_an_unreadable_balance_exits_1(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "me@example.com")  # so stderr has one line only
    _hunter_finds(monkeypatch, "a", "b")
    _balance_unreadable(monkeypatch)

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer"])

    assert result.exit_code == 1
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 0 companies, 0 credits used. Report: ")
    assert result.stderr.splitlines() == [NO_BALANCE]
    assert _report_path(result.stdout).exists()


def test_jobs_run_with_a_failing_company_warns_and_exits_0(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("OUTREACH_CONTACT_EMAIL", "me@example.com")  # so stderr has one line only
    _hunter_finds(monkeypatch, "a", "b")
    _someone_to_buy(monkeypatch, HIRING_FIRST_TIER)
    _count_fails_for(monkeypatch, "a.example")

    result = runner.invoke(app, ["jobs", "--role", "Backend Engineer"])

    assert result.exit_code == 0, result.output
    # The company's failure, and the fake HN source having no thread to read.
    assert result.stderr.splitlines() == [RUN_ERRORS_WARNING.replace("1 error(s)", "2 error(s)")]
    html = _report_path(result.stdout).read_text(encoding="utf-8")
    assert "Hunter email-count returned HTTP 429" in html  # the report's run details list it


def test_jobs_uses_the_configured_people_list_unless_told_otherwise(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a")
    Path("config.toml").write_text(_TEST_CONFIG + '\n[jobs]\npeople = "sponsors"\n',
                                   encoding="utf-8")

    default = runner.invoke(app, ["jobs", "--role", "Backend Engineer"])
    named = runner.invoke(app, ["jobs", "--role", "Backend Engineer", "--people", "hiring"])

    assert default.exit_code == 0 and named.exit_code == 0
    with contextlib.closing(connect(DB)) as conn:
        runs = RunRepo(conn)
        assert (runs.info(1).people, runs.info(2).people) == ("sponsors", "hiring")


# --- jobs --resume -----------------------------------------------------------------

def test_jobs_resume_buys_with_the_runs_own_role_people_and_filters(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b", balance=0)
    first = runner.invoke(app, [
        "jobs", "--role", "Backend Engineer", "--sector", "Software Development",
        "--size", "51-200", "--location", "TX", "--work-mode", "remote", "--people", "sponsors"])
    assert first.exit_code == 0, first.output
    assert "2 companies were not bought" in first.stdout

    # Credits arrive. Resuming must not search again, and the flags name other things.
    searches = []

    def search(self, query):
        searches.append(query)
        return []
    monkeypatch.setattr(FakeCompanySearch, "search", search)
    monkeypatch.setattr(FakePeopleFinder, "balance_tenths", lambda self: 500)
    searched = []
    _someone_to_buy(monkeypatch, SPONSORS_FIRST_TIER, searched)
    seen = []
    real = cli.run_jobs

    def spy(ctx, role, filters, work_modes, people, *, use_hn=True, resume_run_id=None):
        seen.append((role, filters, work_modes, people.name, resume_run_id))
        return real(ctx, role, filters, work_modes, people, use_hn=use_hn,
                    resume_run_id=resume_run_id)
    monkeypatch.setattr("outreach.cli.run_jobs", spy)

    result = runner.invoke(
        app, ["jobs", "--resume", "1", "--people", "hiring", "--sector", "Banking"])

    assert result.exit_code == 0, result.output
    assert seen == [("Backend Engineer",
                     RunFilters(("Software Development",), ("51-200",), Location("US", "TX"),
                                ("remote",)),
                     frozenset({"remote"}), "sponsors", 1)]
    assert searches == []
    assert searched and set(searched) <= set(DEFAULT_PEOPLE["sponsors"].tiers)
    assert result.stdout.splitlines()[0].startswith(
        "Run 1: contacts bought at 2 companies, 2 credits used. Report: ")
    assert NO_JOB_MATCH not in result.stdout  # a resume reports no search, so not "found nothing"
    assert _count("runs") == 1


@pytest.mark.parametrize("run_id", [1, 99])  # a companies run, and no run at all
def test_resuming_what_is_not_a_job_run_exits_1(monkeypatch, run_id):
    _fakes(monkeypatch)
    _start_run(kind="companies", people="sponsors")

    result = runner.invoke(app, ["jobs", "--resume", str(run_id)])

    assert result.exit_code == 1
    assert result.stderr.strip() == f"Run {run_id} is not a job run."


def test_resuming_a_job_run_whose_people_list_is_gone_exits_2(monkeypatch):
    _fakes(monkeypatch)
    _start_run(kind="jobs", people="gone")

    result = runner.invoke(app, ["jobs", "--resume", "1"])

    assert result.exit_code == 2
    assert 'Unknown people list "gone". Known lists: hiring, sponsors.' in result.stderr


def test_a_resume_has_no_search_to_warn_about(monkeypatch):
    _fakes(monkeypatch)
    _start_run(kind="jobs", people="hiring")

    result = runner.invoke(app, ["jobs", "--resume", "1", "--no-hn"])

    assert result.exit_code == 0, result.output
    assert NO_TOKENS_NO_HN not in result.stderr
    assert NO_JOB_MATCH not in result.stdout


# --- sectors -----------------------------------------------------------------------

def test_sectors_lists_matches_and_size_bands():
    result = runner.invoke(app, ["sectors", "software"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "Software Development" in lines
    assert all("software" in line.lower() for line in lines[:-1])
    assert lines[-1] == ("Size bands: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, "
                         "5001-10000, 10001+")


def test_sectors_without_a_word_lists_them_all():
    result = runner.invoke(app, ["sectors"])

    assert result.exit_code == 0, result.output
    assert "Software Development" in result.stdout.splitlines()
    assert "Size bands:" in result.stdout


def test_sectors_with_no_match_exits_1():
    result = runner.invoke(app, ["sectors", "qwertyuiop"])

    assert result.exit_code == 1
    assert result.stderr.strip() == 'No sector contains "qwertyuiop".'
    assert "Size bands" not in result.output


# --- emailed -----------------------------------------------------------------------

def test_emailed_add_list_remove_round_trip(monkeypatch):
    _no_keys_no_fakes(monkeypatch)  # these commands need no key at all

    added = runner.invoke(app, ["emailed", "add", " Dana@Kestrel.example ", "--on", "2026-10-11"])
    listed = runner.invoke(app, ["emailed", "list"])
    removed = runner.invoke(app, ["emailed", "remove", "dana@kestrel.example"])
    after = runner.invoke(app, ["emailed", "list"])

    assert added.exit_code == 0 and added.stdout.strip() == "Marked 1 address as emailed on 2026-10-11."
    assert listed.stdout.splitlines() == ["2026-10-11  dana@kestrel.example"]
    assert removed.exit_code == 0 and removed.stdout.strip() == "Removed 1."
    assert after.stdout.strip() == "No one is marked as emailed yet."


def test_emailed_add_defaults_to_today_and_pluralizes(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "add", "a@x.example", "b@x.example", "A@x.example"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"Marked 2 addresses as emailed on {date.today().isoformat()}."


def test_emailed_list_says_who_a_stored_contact_is(monkeypatch):
    _fakes(monkeypatch)
    with contextlib.closing(connect(DB)) as conn:
        company = CompanyRepo(conn).upsert("kestrel.example", "Kestrel", None, None)
        ContactRepo(conn).upsert(
            company, PersonRef("Dana Okafor", "CTO", None, "dana@kestrel.example", "verified"),
            "hunter", datetime(2026, 9, 21, 9))
    runner.invoke(app, ["emailed", "add", "dana@kestrel.example", "lee@elsewhere.example",
                        "--on", "2026-10-11"])

    result = runner.invoke(app, ["emailed", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "2026-10-11  dana@kestrel.example  Dana Okafor · CTO · Kestrel",
        "2026-10-11  lee@elsewhere.example",
    ]


def test_emailed_add_rejects_a_non_address_and_writes_nothing(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "add", "dana@kestrel.example", "not-an-address"])

    assert result.exit_code == 2
    assert result.stderr.strip() == '"not-an-address" is not an email address'
    assert runner.invoke(app, ["emailed", "list"]).stdout.strip() == "No one is marked as emailed yet."


def test_emailed_add_rejects_a_bad_date_and_writes_nothing(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "add", "dana@kestrel.example", "--on", "10/11/2026"])

    assert result.exit_code == 2
    assert result.stderr.strip() == '--on must be a date like 2026-10-11, not "10/11/2026"'
    assert runner.invoke(app, ["emailed", "list"]).stdout.strip() == "No one is marked as emailed yet."


@pytest.mark.parametrize("on", ["2026-13-01", "2026-1-5", "20261011"])
def test_emailed_add_wants_exactly_year_month_day(monkeypatch, on):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "add", "dana@kestrel.example", "--on", on])

    assert result.exit_code == 2
    assert f'not "{on}"' in result.stderr


def test_emailed_remove_of_an_unknown_address_exits_1(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "remove", "nobody@x.example"])

    assert result.exit_code == 1
    assert result.stdout.strip() == "Removed 0."
    assert result.stderr.strip() == "Not on the list: nobody@x.example"


def test_emailed_remove_names_the_addresses_that_were_not_there(monkeypatch):
    _fakes(monkeypatch)
    runner.invoke(app, ["emailed", "add", "dana@kestrel.example"])

    result = runner.invoke(
        app, ["emailed", "remove", "Dana@Kestrel.example", "nobody@x.example", "dana@kestrel.example"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "Removed 1."
    assert result.stderr.strip() == "Not on the list: nobody@x.example"


def test_emailed_remove_rejects_a_non_address(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["emailed", "remove", "nope"])

    assert result.exit_code == 2
    assert result.stderr.strip() == '"nope" is not an email address'


# --- report ------------------------------------------------------------------------

def test_report_rerenders_a_v4_run(monkeypatch):
    _fakes(monkeypatch)
    _hunter_finds(monkeypatch, "a", "b")
    _someone_to_buy(monkeypatch, SPONSORS_FIRST_TIER)
    ran = runner.invoke(app, ["companies", "--sector", "Software Development"])
    assert ran.exit_code == 0, ran.output
    path = _report_path(ran.stdout)
    path.unlink()
    _no_keys_no_fakes(monkeypatch)  # a report needs no key, and no adapter

    result = runner.invoke(app, ["report", "1"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == f"Report: {path}"
    assert "B Co" in path.read_text(encoding="utf-8")


def test_report_refuses_an_older_run(monkeypatch):
    _fakes(monkeypatch)
    _start_run(schema_version=3)

    result = runner.invoke(app, ["report", "1"])

    assert result.exit_code == 1
    assert result.stderr.strip() == (
        "Run 1 was made by an older version of this tool; see its saved HTML report.")
    assert not Path("reports").exists()


def test_report_rejects_an_unknown_run(monkeypatch):
    _fakes(monkeypatch)

    result = runner.invoke(app, ["report", "99"])

    assert result.exit_code == 1
    assert result.stderr.strip() == "No run with id 99"
    assert not Path("reports").exists()


def test_the_commands_that_read_the_config_stop_on_a_broken_one(monkeypatch):
    _no_keys_no_fakes(monkeypatch)
    Path("config.toml").write_bytes(b"\xff\xfe not text")

    results = [runner.invoke(app, args) for args in (["emailed", "list"], ["report", "1"])]

    assert [r.exit_code for r in results] == [2, 2]
    assert all("config.toml" in r.stderr for r in results)
