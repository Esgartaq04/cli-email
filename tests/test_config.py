import pytest
from pathlib import Path
from outreach.config import load_config, require_env, ConfigError

def test_load_config_reads_defaults(tmp_path: Path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[gate]
min_independent_sources = 2
require_first_party = true
recency_days = 180
first_party_classes = ["job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page"]

[ranking]
max_contacts_per_company = 3
exclude_title_patterns = ["recruit", "talent", "sourcer"]

[discovery]
headcount_min = 20
headcount_max = 1000
region = "US"
greenhouse_tokens = ["acme"]

[paths]
db = "data/pipeline.db"
cache = "data/cache"
reports = "reports"
""")
    cfg = load_config(cfg_file)
    assert cfg.gate.min_independent_sources == 2
    assert cfg.gate.recency_days == 180
    assert cfg.ranking.max_contacts_per_company == 3
    assert cfg.discovery.headcount_max == 1000
    assert cfg.discovery.region == "US"

def test_require_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="HUNTER_API_KEY"):
        require_env("HUNTER_API_KEY")


BASE = """
[gate]
min_independent_sources = 2
require_first_party = true
recency_days = 180
first_party_classes = ["job_posting", "careers_page"]

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


def write(tmp_path: Path, toml: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(toml)
    return path


def test_new_sections_are_optional_and_default(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.stage.growth_max_headcount == 499
    assert cfg.hunter.facts_ttl_days == 90 and cfg.hunter.linkedin_lookup is True
    assert cfg.gate.max_findings_per_company == 3
    assert cfg.discovery.ashby_tokens == () and cfg.discovery.lever_tokens == ()


def test_new_sections_are_read_when_present(tmp_path):
    toml = BASE + '\n[stage]\nseed_max_headcount = 30\n[hunter]\nlinkedin_lookup = false\nfinder_cost = 2\n'
    cfg = load_config(write(tmp_path, toml.replace(
        'greenhouse_tokens = []',
        'greenhouse_tokens = []\nashby_tokens = ["acme"]\nlever_tokens = ["zeta"]')))
    assert cfg.stage.seed_max_headcount == 30
    assert cfg.hunter.linkedin_lookup is False and cfg.hunter.finder_cost == 2
    assert cfg.discovery.ashby_tokens == ("acme",) and cfg.discovery.lever_tokens == ("zeta",)
