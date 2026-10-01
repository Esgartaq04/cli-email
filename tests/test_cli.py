import pytest
from typer.testing import CliRunner

from outreach.cli import app

runner = CliRunner()

_TEST_CONFIG = """
[gate]
min_independent_sources = 2
require_first_party = true
recency_days = 180
first_party_classes = ["job_posting", "careers_page", "eng_blog"]

[ranking]
max_contacts_per_company = 3
exclude_title_patterns = ["recruit"]

[discovery]
headcount_min = 20
headcount_max = 1000
region = "US"
greenhouse_tokens = []

[paths]
db = "data/pipeline.db"
cache = "data/cache"
reports = "reports"
"""


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


def test_missing_api_key_aborts_before_any_network_call(monkeypatch, tmp_path):
    """Fail fast: a broken run must cost zero credits."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.delenv("OUTREACH_FAKE_ADAPTERS", raising=False)
    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech"])
    assert result.exit_code != 0
    assert "ANTHROPIC_API_KEY" in result.output or "HUNTER_API_KEY" in result.output


def test_dry_run_reports_planned_spend_and_writes_no_report(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech", "--dry-run"])
    assert result.exit_code == 0
    assert "would use" in result.output.lower()


def test_empty_token_lists_warn_about_all_three_boards(monkeypatch):
    """config.toml ships with empty token lists -- without a warning, a first
    real run silently discovers zero companies and writes an empty report
    with no indication why."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech", "--dry-run"])
    assert "no greenhouse_tokens, ashby_tokens or lever_tokens" in result.output


def test_one_configured_board_silences_the_empty_token_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    config = tmp_path / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "greenhouse_tokens = []", 'greenhouse_tokens = []\nlever_tokens = ["acme"]'),
        encoding="utf-8")
    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech", "--dry-run"])
    assert "discover zero companies" not in result.output


def test_bad_work_mode_exits_before_anything_runs(monkeypatch):
    """The flag is validated before `build_context`, so a typo costs nothing --
    not even the API-key check."""
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")

    def must_not_build(*args, **kwargs):
        raise AssertionError("build_context ran before --work-mode was validated")

    monkeypatch.setattr("outreach.cli.build_context", must_not_build)
    result = runner.invoke(app, ["run", "--role", "X", "--sector", "y",
                                 "--work-mode", "sometimes", "--dry-run"])
    assert result.exit_code == 2
    assert "sometimes" in result.output


def _fake_postings(monkeypatch, postings):
    """Make the fake job board return `postings` (it returns none by default)."""
    from outreach.sources.jobboards.fake import FakeJobBoardSource
    monkeypatch.setattr(FakeJobBoardSource, "search",
                        lambda self, role_terms, region: list(postings))


def _posting(domain, work_mode="unknown", employment_type="unknown"):
    from outreach.types import PostingRef
    return PostingRef(company_name=domain, company_domain=domain, title="Backend Engineer",
                      url=f"https://{domain}/jobs/1", location="Remote",
                      work_mode=work_mode, employment_type=employment_type)


def test_dry_run_itemizes_credit_buckets(monkeypatch):
    _fake_adapters(monkeypatch)
    result = runner.invoke(app, ["run", "--role", "Backend Engineer", "--sector", "fintech",
                                 "--work-mode", "remote", "--dry-run"])
    assert result.exit_code == 0
    assert "enrichment" in result.output
    assert "contact-search" in result.output
    assert "LinkedIn-lookup" in result.output


def test_dry_run_counts_only_companies_with_a_matching_posting(monkeypatch):
    """Two companies match --work-mode remote, one is onsite-only and one is a
    contract role: only the first two are spent on. cap=3, 1 credit each:
    2 enrichment + 2 contact-search + 2*3 LinkedIn lookups = 10."""
    _fake_adapters(monkeypatch)
    _fake_postings(monkeypatch, [
        _posting("a.com", "remote"), _posting("a.com", "onsite"),
        _posting("b.com", "unknown"),
        _posting("c.com", "onsite"),
        _posting("d.com", "remote", employment_type="other"),
    ])
    result = runner.invoke(app, ["run", "--role", "Backend Engineer", "--sector", "fintech",
                                 "--work-mode", "remote", "--dry-run"])
    assert result.exit_code == 0
    assert ("Dry run: 2 companies matched. A full run would use up to 2 enrichment + "
            "2 contact-search + 6 LinkedIn-lookup credits (10 total); 0 remain.") in result.output


def test_dry_run_without_work_mode_keeps_every_mode(monkeypatch):
    _fake_adapters(monkeypatch)
    _fake_postings(monkeypatch, [_posting("a.com", "onsite"), _posting("b.com", "hybrid")])
    result = runner.invoke(app, ["run", "--role", "Backend Engineer", "--sector", "fintech",
                                 "--dry-run"])
    assert "2 companies matched" in result.output


def test_dry_run_charges_no_linkedin_lookups_when_disabled(monkeypatch, tmp_path):
    _fake_adapters(monkeypatch)
    _fake_postings(monkeypatch, [_posting("a.com"), _posting("b.com")])
    config = tmp_path / "config.toml"
    config.write_text(config.read_text(encoding="utf-8")
                      + "\n[hunter]\nlinkedin_lookup = false\n", encoding="utf-8")
    result = runner.invoke(app, ["run", "--role", "Backend Engineer", "--sector", "fintech",
                                 "--dry-run"])
    assert "0 LinkedIn-lookup credits (4 total)" in result.output


def test_dry_run_guards_remaining_credits_and_reports_incomplete_estimate(monkeypatch):
    """The credit check is the one thing --dry-run exists to report safely.

    A provider failure here must not crash with a raw traceback -- it must
    still say what discovery found, say the estimate is incomplete, and
    exit non-zero so the caller knows not to trust it.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")

    from outreach.contacts.fake import FakeContactProvider

    def boom(self):
        raise RuntimeError("Hunter account endpoint returned 500")

    monkeypatch.setattr(FakeContactProvider, "remaining_credits", boom)

    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech", "--dry-run"])
    assert result.exit_code != 0
    assert "could not be checked" in result.output.lower()
    assert "companies matched" in result.output.lower()


def _fake_adapters(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")


def _stored_run(tmp_path, schema_version: int) -> int:
    """A finished run row in the test's scratch database, as `run` leaves it."""
    from datetime import datetime
    from pathlib import Path

    from outreach.store.db import connect
    from outreach.store.runs import RunRepo

    conn = connect(tmp_path / Path("data/pipeline.db"))
    runs = RunRepo(conn)
    run_id = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime(2026, 9, 21))
    runs.finish(run_id, datetime(2026, 9, 21, 1))
    conn.execute("UPDATE runs SET schema_version = ? WHERE id = ?", (schema_version, run_id))
    conn.commit()
    conn.close()
    return run_id


def test_report_command_refuses_a_pre_v2_run(monkeypatch, tmp_path):
    """A bottlenecks-era run has no findings or stages to lay out; rendering
    it as a run where nothing was found would misstate what it found."""
    _fake_adapters(monkeypatch)
    run_id = _stored_run(tmp_path, schema_version=1)
    result = runner.invoke(app, ["report", str(run_id)])
    assert result.exit_code == 1
    assert f"Run {run_id} is a pre-v2 run; see its saved HTML report." in result.output
    assert not (tmp_path / "reports").exists()


def test_report_command_rerenders_a_v2_run(monkeypatch, tmp_path):
    _fake_adapters(monkeypatch)
    run_id = _stored_run(tmp_path, schema_version=2)
    result = runner.invoke(app, ["report", str(run_id)])
    assert result.exit_code == 0, result.output
    [path] = (tmp_path / "reports").glob("*.html")
    assert "not recorded" in path.read_text(encoding="utf-8")


def test_report_command_rejects_an_unknown_run(monkeypatch):
    _fake_adapters(monkeypatch)
    result = runner.invoke(app, ["report", "99"])
    assert result.exit_code == 1
    assert "No run with id 99" in result.output


def test_real_adapters_build_one_source_per_configured_board(monkeypatch, tmp_path):
    """Construction only -- no request is made, so this touches no network.
    A board with an empty token list is left out rather than built to search
    nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.delenv("OUTREACH_FAKE_ADAPTERS", raising=False)
    config = tmp_path / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "greenhouse_tokens = []",
        'greenhouse_tokens = ["g"]\nlever_tokens = ["l1", "l2"]'), encoding="utf-8")

    from outreach.cli import build_context
    ctx = build_context()

    assert [type(s).__name__ for s in ctx.job_board.sources] == [
        "GreenhouseBoardSource", "LeverBoardSource"]
    assert ctx.job_board.sources[1].tokens == ("l1", "l2")
