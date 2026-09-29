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


def test_empty_greenhouse_tokens_warns_instead_of_silently_finding_nothing(monkeypatch):
    """config.toml ships with `greenhouse_tokens = []` -- without a warning,
    a first real run silently discovers zero companies and writes an empty
    report with no indication why."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("HUNTER_API_KEY", "test")
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")
    result = runner.invoke(app, ["run", "--role", "Backend Engineer",
                                 "--sector", "fintech", "--dry-run"])
    assert "greenhouse_tokens is empty" in result.output


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
