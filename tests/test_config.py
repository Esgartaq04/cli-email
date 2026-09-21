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
