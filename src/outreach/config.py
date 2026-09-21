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


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    try:
        g, r, d, p = raw["gate"], raw["ranking"], raw["discovery"], raw["paths"]
        return Config(
            gate=GateConfig(
                min_independent_sources=int(g["min_independent_sources"]),
                require_first_party=bool(g["require_first_party"]),
                recency_days=int(g["recency_days"]),
                first_party_classes=frozenset(g["first_party_classes"]),
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
            ),
            paths=PathsConfig(
                db=Path(p["db"]), cache=Path(p["cache"]), reports=Path(p["reports"])
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
