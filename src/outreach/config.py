from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from outreach.credits import tenths


class ConfigError(RuntimeError):
    """Raised for missing or malformed configuration. Aborts before any spend."""


@dataclass(frozen=True)
class PathsConfig:
    db: Path
    reports: Path


@dataclass(frozen=True)
class DiscoveryConfig:
    # Job-board tokens searched by `outreach jobs`; company runs don't read them.
    # A board with no tokens is left out of the search (see cli.build_context).
    greenhouse_tokens: tuple[str, ...] = ()
    ashby_tokens: tuple[str, ...] = ()
    lever_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class HNConfig:
    # Discovery from HN's monthly "Who is hiring?" thread. The cap applies before
    # any paid call, so it bounds what a thread of 300+ posts can cost; the
    # fallback limit bounds LLM calls.
    enabled: bool = True
    max_new_companies: int = 15
    llm_fallback_max: int = 20


@dataclass(frozen=True)
class HunterConfig:
    # Costs are in tenths of a credit (see outreach.credits) and written in
    # credits in config.toml. They are settings so that a change in Hunter's
    # pricing is an edit to one file, not to the code.
    email_cost: int = 10
    verify_cost: int = 5
    # A "valid" status Hunter gave an address longer ago than this is checked
    # again instead of trusted: a bounce costs more than half a credit.
    verified_max_age_days: int = 90


@dataclass(frozen=True)
class RunConfig:
    # Counts the companies a person was bought at, so a company whose search
    # comes back empty (which costs nothing) doesn't use up a slot.
    max_companies: int = 10
    # Also the size of each Hunter search; the free plan returns at most 10.
    contacts_per_company: int = 3


@dataclass(frozen=True)
class PeopleList:
    """Who to look for at a company, most important first.

    A tier is one Hunter search by job title. A run searches tier by tier and
    stops once it has enough people, so someone in a lower tier is paid for only
    when the tiers above left a slot open. Titles that can name one person (a
    "Founder & CTO") share a tier so that person isn't revealed twice. Anyone
    matching `exclude_titles` is dropped even though they were already paid for.
    """

    name: str
    tiers: tuple[tuple[str, ...], ...]
    exclude_titles: tuple[str, ...] = ()


DEFAULT_PEOPLE: dict[str, PeopleList] = {
    "hiring": PeopleList("hiring", (
        ("founder", "co-founder", "cto", "chief technology officer"),
        ("vp engineering", "vp of engineering", "head of engineering", "director of engineering"),
        ("engineering manager", "tech lead"),
    ), ("recruit", "talent", "sourcer")),
    "sponsors": PeopleList("sponsors", (
        ("partnerships", "sponsorships", "university relations", "university recruiting",
         "campus recruiting", "developer relations", "community"),
        ("events", "marketing manager", "head of marketing", "brand"),
        ("founder", "co-founder", "cto", "engineering manager"),
    )),
}


@dataclass(frozen=True)
class Config:
    paths: PathsConfig
    discovery: DiscoveryConfig = DiscoveryConfig()
    hn: HNConfig = HNConfig()
    hunter: HunterConfig = HunterConfig()
    run: RunConfig = RunConfig()
    people: dict[str, PeopleList] = field(default_factory=lambda: dict(DEFAULT_PEOPLE))
    # The list each command uses unless the user names another one.
    jobs_people: str = "hiring"
    companies_people: str = "sponsors"
    # Settings an older config.toml still carries, which this version ignores.
    ignored: tuple[str, ...] = ()


# A config.toml written for an earlier version keeps loading: its settings that
# no longer do anything are recorded in Config.ignored and reported once (see
# ignored_warning), rather than failing a run that is otherwise fine.
_LEGACY_SECTIONS = ("gate", "ranking", "stage")
_LEGACY_KEYS = {
    "discovery": ("headcount_min", "headcount_max", "region", "max_blog_posts",
                  "max_job_postings", "max_changelog_entries", "max_press_posts",
                  "max_github_repos"),
    "hunter": ("enrichment_cost", "finder_cost", "linkedin_lookup", "facts_ttl_days"),
    "hn": ("recheck_days",),
    "paths": ("cache",),
}
# Every other setting must be one of these: a typo such as `max_companys` is an
# error, because ignoring it would silently run with the default limit instead.
_KNOWN_KEYS = {
    "paths": ("db", "reports"),
    "discovery": ("greenhouse_tokens", "ashby_tokens", "lever_tokens"),
    "hn": ("enabled", "max_new_companies", "llm_fallback_max"),
    "hunter": ("email_cost", "verify_cost", "verified_max_age_days"),
    "run": ("max_companies", "contacts_per_company"),
    "jobs": ("people",),
    "companies": ("people",),
}
_PEOPLE_KEYS = ("tiers", "exclude_titles")


def _table(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a [{name}] section, got {value!r}")
    return value


def _scan(raw: dict) -> tuple[str, ...]:
    """Reject what isn't a setting; return the legacy settings found, in file order."""
    ignored: list[str] = []
    for section, body in raw.items():
        if section in _LEGACY_SECTIONS:
            ignored.append(f"[{section}]")  # the section is never read, so never validated
        elif section == "people":
            pass  # its lists are named by the user, so _people checks them
        elif section in _KNOWN_KEYS:
            for key in _table(body, section):
                if key in _LEGACY_KEYS.get(section, ()):
                    ignored.append(f"{section}.{key}")
                elif key not in _KNOWN_KEYS[section]:
                    raise ConfigError(f"unknown config setting: {section}.{key}")
        else:
            raise ConfigError(f"unknown config setting: {section}")
    return tuple(ignored)


def _is_text(item: object) -> bool:
    return isinstance(item, str) and bool(item.strip())


def _text(table: dict, section: str, key: str, default: str | None = None) -> str:
    """A non-empty string. Without a default the key is required."""
    if key not in table:
        if default is None:
            raise ConfigError(f"missing config key: {section}.{key}")
        return default
    if not _is_text(table[key]):
        raise ConfigError(f"{section}.{key} must be text in quotes, got {table[key]!r}")
    return table[key]


def _whole(table: dict, section: str, key: str, default: int, *, low: int = 0,
           high: int | None = None, note: str = "") -> int:
    """A whole number from `low` (up to `high`). True is not one, and neither is 2.5."""
    name, value = f"{section}.{key}", table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be a whole number, got {value!r}")
    if value < low or (high is not None and value > high):
        limit = f"at least {low}" if high is None else f"between {low} and {high}"
        message = f"{name} must be {limit}, got {value}"
        raise ConfigError(f"{message} {note}" if note else message)
    return value


def _cost(table: dict, key: str, default: int) -> int:
    """A price written in credits (0.5), kept in tenths (5)."""
    if key not in table:
        return default
    try:
        return tenths(table[key])
    except ValueError as exc:
        raise ConfigError(f"hunter.{key} must be 0 or more credits in steps of 0.1, "
                          f"such as 0.5 or 1, got {table[key]!r}") from exc


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(_is_text(item) for item in value):
        raise ConfigError(f"{name} must be a list of non-empty strings, got {value!r}")
    return tuple(value)


def _tiers(value: object, name: str) -> tuple[tuple[str, ...], ...]:
    # An empty tier would be a search for nobody, so it is an error, not a no-op.
    well_formed = isinstance(value, list) and bool(value) and all(
        isinstance(tier, list) and bool(tier) and all(_is_text(title) for title in tier)
        for tier in value)
    if not well_formed:
        raise ConfigError(f'{name} must be a non-empty list of tiers, each a non-empty list '
                          f'of job titles, such as [["cto"], ["engineering manager"]]; got {value!r}')
    return tuple(tuple(tier) for tier in value)


def _people(raw: dict) -> dict[str, PeopleList]:
    # A list in the file replaces the default of the same name outright: it does
    # not inherit that default's exclude_titles. Defaults it doesn't name stay.
    people = dict(DEFAULT_PEOPLE)
    for name, entry in _table(raw.get("people", {}), "people").items():
        key = f"people.{name}"
        body = _table(entry, key)
        for setting in body:
            if setting not in _PEOPLE_KEYS:
                raise ConfigError(f"unknown config setting: {key}.{setting}")
        if "tiers" not in body:
            raise ConfigError(f"missing config key: {key}.tiers")
        people[name] = PeopleList(
            name, _tiers(body["tiers"], f"{key}.tiers"),
            _strings(body.get("exclude_titles", []), f"{key}.exclude_titles"))
    return people


def _paths(raw: dict) -> PathsConfig:
    table = raw.get("paths", {})
    return PathsConfig(db=Path(_text(table, "paths", "db")),
                       reports=Path(_text(table, "paths", "reports")))


def _discovery(raw: dict) -> DiscoveryConfig:
    table = raw.get("discovery", {})
    return DiscoveryConfig(
        greenhouse_tokens=_strings(table.get("greenhouse_tokens", []), "discovery.greenhouse_tokens"),
        ashby_tokens=_strings(table.get("ashby_tokens", []), "discovery.ashby_tokens"),
        lever_tokens=_strings(table.get("lever_tokens", []), "discovery.lever_tokens"),
    )


def _hn(raw: dict) -> HNConfig:
    table, defaults = raw.get("hn", {}), HNConfig()
    enabled = table.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"hn.enabled must be true or false, got {enabled!r}")
    return HNConfig(
        enabled=enabled,
        max_new_companies=_whole(table, "hn", "max_new_companies", defaults.max_new_companies),
        llm_fallback_max=_whole(table, "hn", "llm_fallback_max", defaults.llm_fallback_max),
    )


def _hunter(raw: dict) -> HunterConfig:
    table, defaults = raw.get("hunter", {}), HunterConfig()
    return HunterConfig(
        email_cost=_cost(table, "email_cost", defaults.email_cost),
        verify_cost=_cost(table, "verify_cost", defaults.verify_cost),
        verified_max_age_days=_whole(
            table, "hunter", "verified_max_age_days", defaults.verified_max_age_days),
    )


def _run(raw: dict) -> RunConfig:
    table, defaults = raw.get("run", {}), RunConfig()
    return RunConfig(
        max_companies=_whole(table, "run", "max_companies", defaults.max_companies, low=1),
        contacts_per_company=_whole(
            table, "run", "contacts_per_company", defaults.contacts_per_company, low=1, high=10,
            note="(Hunter's free plan returns at most 10 people per search)"),
    )


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path} is not UTF-8 text: {exc}") from exc
    ignored = _scan(raw)
    people = _people(raw)
    # Each command's default list is checked once the file's own lists are in, so
    # a list defined in the file can be a command's default. The fallbacks are
    # the defaults on Config itself.
    jobs_people = _text(raw.get("jobs", {}), "jobs", "people", Config.jobs_people)
    companies_people = _text(raw.get("companies", {}), "companies", "people", Config.companies_people)
    for key, name in (("jobs.people", jobs_people), ("companies.people", companies_people)):
        if name not in people:
            raise ConfigError(f"{key} names a people list that doesn't exist: {name!r} "
                              f"(defined: {', '.join(people)})")
    return Config(
        paths=_paths(raw), discovery=_discovery(raw), hn=_hn(raw), hunter=_hunter(raw),
        run=_run(raw), people=people, jobs_people=jobs_people,
        companies_people=companies_people, ignored=ignored,
    )


def ignored_warning(config: Config) -> str | None:
    if not config.ignored:
        return None
    return ("Warning: config.toml settings this version no longer uses were ignored: "
            + ", ".join(config.ignored))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Add it to .env before running — "
            "the pipeline aborts here so no credits are spent on a broken run."
        )
    return value
