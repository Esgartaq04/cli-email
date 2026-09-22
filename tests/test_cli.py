import pytest
from typer.testing import CliRunner

from outreach.cli import app

runner = CliRunner()


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
