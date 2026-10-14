import pytest

_TEST_CONFIG = """\
[paths]
db = "data/pipeline.db"
reports = "reports"

[discovery]
greenhouse_tokens = []
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

    from outreach.cli import build_context
    ctx = build_context()

    assert [type(s).__name__ for s in ctx.job_board.sources] == [
        "GreenhouseBoardSource", "LeverBoardSource"]
    assert ctx.job_board.sources[1].tokens == ("l1", "l2")
    assert type(ctx.people).__name__ == "HunterProvider"


def test_real_adapters_need_a_hunter_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    monkeypatch.delenv("OUTREACH_FAKE_ADAPTERS", raising=False)

    from outreach.cli import build_context
    from outreach.config import ConfigError
    with pytest.raises(ConfigError, match="HUNTER_API_KEY"):
        build_context()


def test_fake_adapters_find_people_without_any_credits(monkeypatch):
    """A fake run can never buy anyone: the fake finder starts with no balance."""
    monkeypatch.setenv("OUTREACH_FAKE_ADAPTERS", "1")

    from outreach.cli import build_context
    ctx = build_context()

    assert type(ctx.people).__name__ == "FakePeopleFinder"
    assert ctx.people.balance_tenths() == 0
