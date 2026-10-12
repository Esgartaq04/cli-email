from __future__ import annotations

import re
from pathlib import Path

import pytest

from outreach.config import (
    DEFAULT_PEOPLE,
    Config,
    ConfigError,
    DiscoveryConfig,
    HNConfig,
    HunterConfig,
    PathsConfig,
    PeopleList,
    RunConfig,
    ignored_warning,
    load_config,
    require_env,
)

MINIMAL = '[paths]\ndb = "d.db"\nreports = "r"\n'

# The config.toml this project shipped before the contact finder (the exact text
# of `git show 224f9e0:config.toml`): what a returning user still has on disk.
PRE_PIVOT = """\
[gate]
min_independent_sources = 1
require_first_party = true
recency_days = 180
first_party_classes = ["job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page", "press", "about", "hn_post"]

[ranking]
max_contacts_per_company = 3
exclude_title_patterns = ["recruit", "talent", "sourcer"]

[discovery]
headcount_min = 1
headcount_max = 2000
region = "US"
greenhouse_tokens = ["stripe", "plaid", "brex"]
ashby_tokens = []
lever_tokens = []
max_blog_posts = 5      # engineering posts read per company (each fetch waits out the 2s per-host limit)
max_job_postings = 2    # matching job postings read per company

# Optional sections; the values shown are the defaults.
# [stage]
# seed_max_headcount = 49
# growth_max_headcount = 499
# maturity_min_age_years = 12
# maturity_min_years_since_funding = 6
# signal_recency_days = 365
#
# [hunter]
# enrichment_cost = 1
# finder_cost = 1
# linkedin_lookup = true
# facts_ttl_days = 90

# [hn]                            # discovery from HN's "Who is hiring?" thread
# enabled = true
# max_new_companies = 15          # HN companies researched per run, best stage hints first
# llm_fallback_max = 20           # LLM parses per run for posts the rules can't parse
# recheck_days = 30               # skip HN companies researched this recently

[paths]
db = "data/pipeline.db"
cache = "data/cache"
reports = "reports"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_minimal_config_needs_only_paths(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.paths == PathsConfig(Path("d.db"), Path("r"))
    assert cfg.discovery == DiscoveryConfig()
    assert (cfg.run.max_companies, cfg.run.contacts_per_company) == (10, 3)
    assert (cfg.hunter.email_cost, cfg.hunter.verify_cost, cfg.hunter.verified_max_age_days) == (10, 5, 90)
    assert (cfg.hn.enabled, cfg.hn.max_new_companies, cfg.hn.llm_fallback_max) == (True, 15, 20)
    assert cfg.people == DEFAULT_PEOPLE and (cfg.jobs_people, cfg.companies_people) == ("hiring", "sponsors")
    assert cfg.ignored == () and ignored_warning(cfg) is None


def test_paths_are_required(tmp_path):
    with pytest.raises(ConfigError, match="paths.db"):
        load_config(write(tmp_path, "[run]\nmax_companies = 5\n"))
    with pytest.raises(ConfigError, match="paths.reports"):
        load_config(write(tmp_path, '[paths]\ndb = "d.db"\n'))


def test_a_path_must_be_text(tmp_path):
    with pytest.raises(ConfigError, match="paths.db"):
        load_config(write(tmp_path, '[paths]\ndb = 5\nreports = "r"\n'))


def test_settings_in_the_file_override_the_defaults(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + """
[discovery]
greenhouse_tokens = ["g1", "g2"]
ashby_tokens = ["a"]
lever_tokens = ["l"]

[hn]
enabled = false
max_new_companies = 5
llm_fallback_max = 0

[hunter]
email_cost = 1.5
verify_cost = 0
verified_max_age_days = 30

[run]
max_companies = 4
contacts_per_company = 10

[jobs]
people = "sponsors"

[companies]
people = "hiring"
"""))
    assert cfg.discovery == DiscoveryConfig(("g1", "g2"), ("a",), ("l",))
    assert cfg.hn == HNConfig(enabled=False, max_new_companies=5, llm_fallback_max=0)
    assert cfg.hunter == HunterConfig(email_cost=15, verify_cost=0, verified_max_age_days=30)
    assert cfg.run == RunConfig(max_companies=4, contacts_per_company=10)
    assert (cfg.jobs_people, cfg.companies_people) == ("sponsors", "hiring")


def test_costs_are_read_in_tenths(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + "[hunter]\nemail_cost = 2\nverify_cost = 0.5\n"))
    assert (cfg.hunter.email_cost, cfg.hunter.verify_cost) == (20, 5)


def test_a_cost_finer_than_a_tenth_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="hunter.verify_cost"):
        load_config(write(tmp_path, MINIMAL + "[hunter]\nverify_cost = 0.25\n"))


@pytest.mark.parametrize("line", [
    "email_cost = -1", 'email_cost = "1"', "email_cost = true", "email_cost = inf", "email_cost = nan",
])
def test_a_cost_that_is_not_an_amount_of_credits_is_an_error(tmp_path, line):
    with pytest.raises(ConfigError, match="hunter.email_cost"):
        load_config(write(tmp_path, MINIMAL + f"[hunter]\n{line}\n"))


def test_a_people_list_in_the_file_replaces_the_default_of_that_name(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + '[people.hiring]\ntiers = [["cto"]]\n'))
    assert cfg.people["hiring"] == PeopleList("hiring", (("cto",),), ())
    assert cfg.people["sponsors"] == DEFAULT_PEOPLE["sponsors"]


def test_a_new_people_list_can_be_a_commands_default(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL + """
[people.mentors]
tiers = [["mentor", "advisor"], ["founder"]]
exclude_titles = ["intern"]

[companies]
people = "mentors"
"""))
    assert cfg.people["mentors"] == PeopleList("mentors", (("mentor", "advisor"), ("founder",)), ("intern",))
    assert set(cfg.people) == {"hiring", "sponsors", "mentors"}
    assert cfg.companies_people == "mentors"


def test_the_default_people_list_must_exist(tmp_path):
    with pytest.raises(ConfigError, match="companies.people"):
        load_config(write(tmp_path, MINIMAL + '[companies]\npeople = "nobody"\n'))
    with pytest.raises(ConfigError, match="jobs.people"):
        load_config(write(tmp_path, MINIMAL + '[jobs]\npeople = "nobody"\n'))


@pytest.mark.parametrize("body", [
    'tiers = ["cto"]',               # titles, not lists of titles
    "tiers = []",
    "tiers = [[]]",
    'tiers = [[""]]',
    'tiers = [["cto", 5]]',
    'tiers = "cto"',
    'exclude_titles = ["recruit"]',  # no tiers at all
])
def test_tiers_must_be_lists_of_titles(tmp_path, body):
    with pytest.raises(ConfigError, match="people.hiring.tiers"):
        load_config(write(tmp_path, MINIMAL + f"[people.hiring]\n{body}\n"))


def test_the_ends_of_the_allowed_ranges_are_allowed(tmp_path):
    fewest = load_config(write(tmp_path, MINIMAL + "[run]\nmax_companies = 1\ncontacts_per_company = 1\n"))
    most = load_config(write(tmp_path, MINIMAL + "[run]\ncontacts_per_company = 10\n"))
    assert (fewest.run.max_companies, fewest.run.contacts_per_company,
            most.run.contacts_per_company) == (1, 1, 10)


@pytest.mark.parametrize("value", [0, 11])
def test_contacts_per_company_is_between_1_and_10(tmp_path, value):
    with pytest.raises(ConfigError, match="run.contacts_per_company") as raised:
        load_config(write(tmp_path, MINIMAL + f"[run]\ncontacts_per_company = {value}\n"))
    assert str(raised.value).endswith("(Hunter's free plan returns at most 10 people per search)")


@pytest.mark.parametrize("value", [0, -3])
def test_max_companies_is_at_least_1(tmp_path, value):
    with pytest.raises(ConfigError, match="run.max_companies"):
        load_config(write(tmp_path, MINIMAL + f"[run]\nmax_companies = {value}\n"))


@pytest.mark.parametrize("section,key", [
    ("hn", "max_new_companies"), ("hn", "llm_fallback_max"), ("hunter", "verified_max_age_days"),
])
def test_a_count_cannot_be_negative(tmp_path, section, key):
    with pytest.raises(ConfigError, match=f"{section}.{key}"):
        load_config(write(tmp_path, MINIMAL + f"[{section}]\n{key} = -1\n"))


@pytest.mark.parametrize("text,name", [
    ('[run]\nmax_companies = "ten"\n', "run.max_companies"),
    ("[run]\nmax_companies = 2.5\n", "run.max_companies"),
    ("[run]\ncontacts_per_company = true\n", "run.contacts_per_company"),
    ('[hn]\nenabled = "yes"\n', "hn.enabled"),
    ("[hunter]\nverified_max_age_days = 1.5\n", "hunter.verified_max_age_days"),
    ('[discovery]\ngreenhouse_tokens = "stripe"\n', "discovery.greenhouse_tokens"),
    ("[discovery]\nlever_tokens = [1]\n", "discovery.lever_tokens"),
    ("[jobs]\npeople = 3\n", "jobs.people"),
    ('[people.hiring]\ntiers = [["cto"]]\nexclude_titles = "recruit"\n', "people.hiring.exclude_titles"),
    ("run = 5\n", "run"),                       # a section that isn't a [section]
    ('[people]\nhiring = "cto"\n', "people.hiring"),
])
def test_a_setting_of_the_wrong_type_is_an_error(tmp_path, text, name):
    # Every such message opens with the dotted key, so the user can find the line.
    with pytest.raises(ConfigError, match=rf"^{re.escape(name)} must be"):
        load_config(write(tmp_path, text + MINIMAL))


@pytest.mark.parametrize("text,name", [
    ("[run]\nmax_companys = 5\n", "run.max_companys"),
    ("[colour]\nred = 1\n", "colour"),
    ('colour = "red"\n', "colour"),
    ("[discovery]\nworkday_tokens = []\n", "discovery.workday_tokens"),
    ('[people.hiring]\ntiers = [["cto"]]\ntitles = ["cto"]\n', "people.hiring.titles"),
])
def test_an_unknown_setting_is_an_error(tmp_path, text, name):
    with pytest.raises(ConfigError, match=f"unknown config setting: {name}"):
        load_config(write(tmp_path, text + MINIMAL))


def test_the_pre_pivot_config_loads_with_ignored_settings(tmp_path):
    cfg = load_config(write(tmp_path, PRE_PIVOT))
    assert cfg.ignored == ("[gate]", "[ranking]", "discovery.headcount_min", "discovery.headcount_max",
                           "discovery.region", "discovery.max_blog_posts", "discovery.max_job_postings",
                           "paths.cache")
    assert cfg.discovery.greenhouse_tokens == ("stripe", "plaid", "brex")
    assert ignored_warning(cfg).startswith(
        "Warning: config.toml settings this version no longer uses were ignored: [gate], [ranking]")


def test_every_legacy_setting_is_ignored_in_file_order(tmp_path):
    # All 17 legacy settings, in an order of their own rather than the order the
    # loader lists them in, so this checks file order and not only the names.
    cfg = load_config(write(tmp_path, """\
[paths]
cache = "data/cache"
db = "d.db"
reports = "r"

[hn]
recheck_days = 30

[hunter]
facts_ttl_days = 90
linkedin_lookup = true
finder_cost = 1
enrichment_cost = 1

[discovery]
max_github_repos = 5
max_press_posts = 3
max_changelog_entries = 5
max_job_postings = 2
max_blog_posts = 5
region = "US"
headcount_max = 2000
headcount_min = 1

[stage]
seed_max_headcount = 49

[ranking]
max_contacts_per_company = 3

[gate]
min_independent_sources = 1
"""))
    assert cfg.ignored == (
        "paths.cache", "hn.recheck_days",
        "hunter.facts_ttl_days", "hunter.linkedin_lookup", "hunter.finder_cost", "hunter.enrichment_cost",
        "discovery.max_github_repos", "discovery.max_press_posts", "discovery.max_changelog_entries",
        "discovery.max_job_postings", "discovery.max_blog_posts", "discovery.region",
        "discovery.headcount_max", "discovery.headcount_min",
        "[stage]", "[ranking]", "[gate]")


def test_the_warning_names_every_ignored_setting():
    cfg = Config(paths=PathsConfig(Path("d.db"), Path("r")), ignored=("[gate]", "paths.cache"))
    assert ignored_warning(cfg) == (
        "Warning: config.toml settings this version no longer uses were ignored: [gate], paths.cache")


def test_the_shipped_config_loads_cleanly():
    cfg = load_config(Path(__file__).resolve().parent.parent / "config.toml")
    assert cfg.discovery.greenhouse_tokens == ("stripe", "plaid", "brex")
    # It spells out every default, so the commented file and the code can't drift apart.
    assert (cfg.hn, cfg.hunter, cfg.run) == (HNConfig(), HunterConfig(), RunConfig())
    assert cfg.people == DEFAULT_PEOPLE
    assert (cfg.jobs_people, cfg.companies_people) == ("hiring", "sponsors")
    assert cfg.ignored == ()


def test_require_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="HUNTER_API_KEY"):
        require_env("HUNTER_API_KEY")
