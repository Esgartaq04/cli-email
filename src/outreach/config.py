from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised for missing or malformed configuration. Aborts before any spend."""


@dataclass(frozen=True)
class GateConfig:
    min_independent_sources: int
    require_first_party: bool
    recency_days: int
    first_party_classes: frozenset[str]
    max_findings_per_company: int = 3


@dataclass(frozen=True)
class RankingConfig:
    max_contacts_per_company: int
    exclude_title_patterns: tuple[str, ...]


@dataclass(frozen=True)
class DiscoveryConfig:
    headcount_min: int
    headcount_max: int
    region: str
    greenhouse_tokens: tuple[str, ...]
    max_blog_posts: int = 5
    max_job_postings: int = 2
    ashby_tokens: tuple[str, ...] = ()
    lever_tokens: tuple[str, ...] = ()
    max_changelog_entries: int = 5
    max_press_posts: int = 3
    max_github_repos: int = 5


@dataclass(frozen=True)
class StageConfig:
    seed_max_headcount: int = 49
    growth_max_headcount: int = 499
    maturity_min_age_years: int = 12
    maturity_min_years_since_funding: int = 6
    signal_recency_days: int = 365


@dataclass(frozen=True)
class HunterConfig:
    # Hunter's docs do not say which credit bucket these calls draw from; the
    # costs are config so they can be corrected without a code change.
    enrichment_cost: int = 1
    finder_cost: int = 1
    linkedin_lookup: bool = True
    facts_ttl_days: int = 90


@dataclass(frozen=True)
class PathsConfig:
    db: Path
    cache: Path
    reports: Path


@dataclass(frozen=True)
class Config:
    gate: GateConfig
    ranking: RankingConfig
    discovery: DiscoveryConfig
    paths: PathsConfig
    stage: StageConfig = StageConfig()
    hunter: HunterConfig = HunterConfig()


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    try:
        g, r, d, p = raw["gate"], raw["ranking"], raw["discovery"], raw["paths"]
        # [stage] and [hunter] are optional: an absent section means the dataclass defaults.
        st, h = raw.get("stage", {}), raw.get("hunter", {})
        sd, hd = StageConfig(), HunterConfig()
        return Config(
            gate=GateConfig(
                min_independent_sources=int(g["min_independent_sources"]),
                require_first_party=bool(g["require_first_party"]),
                recency_days=int(g["recency_days"]),
                first_party_classes=frozenset(g["first_party_classes"]),
                max_findings_per_company=int(g.get("max_findings_per_company", 3)),
            ),
            ranking=RankingConfig(
                max_contacts_per_company=int(r["max_contacts_per_company"]),
                exclude_title_patterns=tuple(r["exclude_title_patterns"]),
            ),
            discovery=DiscoveryConfig(
                headcount_min=int(d["headcount_min"]),
                headcount_max=int(d["headcount_max"]),
                region=str(d["region"]),
                greenhouse_tokens=tuple(d["greenhouse_tokens"]),
                max_blog_posts=int(d.get("max_blog_posts", 5)),
                max_job_postings=int(d.get("max_job_postings", 2)),
                ashby_tokens=tuple(d.get("ashby_tokens", ())),
                lever_tokens=tuple(d.get("lever_tokens", ())),
                max_changelog_entries=int(d.get("max_changelog_entries", 5)),
                max_press_posts=int(d.get("max_press_posts", 3)),
                max_github_repos=int(d.get("max_github_repos", 5)),
            ),
            paths=PathsConfig(
                db=Path(p["db"]), cache=Path(p["cache"]), reports=Path(p["reports"])
            ),
            stage=StageConfig(
                seed_max_headcount=int(st.get("seed_max_headcount", sd.seed_max_headcount)),
                growth_max_headcount=int(st.get("growth_max_headcount", sd.growth_max_headcount)),
                maturity_min_age_years=int(st.get("maturity_min_age_years", sd.maturity_min_age_years)),
                maturity_min_years_since_funding=int(st.get(
                    "maturity_min_years_since_funding", sd.maturity_min_years_since_funding)),
                signal_recency_days=int(st.get("signal_recency_days", sd.signal_recency_days)),
            ),
            hunter=HunterConfig(
                enrichment_cost=int(h.get("enrichment_cost", hd.enrichment_cost)),
                finder_cost=int(h.get("finder_cost", hd.finder_cost)),
                linkedin_lookup=bool(h.get("linkedin_lookup", hd.linkedin_lookup)),
                facts_ttl_days=int(h.get("facts_ttl_days", hd.facts_ttl_days)),
            ),
        )
    except KeyError as exc:
        raise ConfigError(f"missing config key: {exc.args[0]}") from exc


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Add it to .env before running — "
            "the pipeline aborts here so no credits are spent on a broken run."
        )
    return value
