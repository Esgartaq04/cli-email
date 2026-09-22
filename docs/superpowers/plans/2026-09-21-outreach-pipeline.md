# Outreach Research Pipeline V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local CLI that takes a job title, sector and headcount band, and produces one self-contained HTML report listing companies, ranked contacts, and evidence-backed bottlenecks.

**Architecture:** Six checkpointed stages (discover → profile → contacts → evidence → gate+synthesize → report) reading and writing SQLite. All I/O sits behind protocols with fakes; `core/` is pure functions only. The LLM has exactly three jobs and never decides whether a bottleneck is good enough — deterministic code does that.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, httpx, pydantic, Jinja2, typer, python-dotenv, pytest.

**Spec:** `docs/superpowers/specs/2026-09-21-outreach-pipeline-design.md`

## Global Constraints

- Python 3.11 or newer. Package lives at `src/outreach/`, installed editable.
- **No test touches a live API.** Every external call goes through a protocol with a fake.
- **`core/` imports nothing that performs I/O** — no httpx, no sqlite3, no open(), no datetime.now(). Time is passed in as a parameter.
- The LLM client exposes exactly three methods: `expand_titles`, `extract_claims`, `write_summary`.
- An email is only ever presented as an email when `email_status == "verified"`. Unverified means the contact ships with `email=None`.
- No `evidence_item` row persists unless its quote passes the substring guard.
- Gate thresholds, source classes, first-party classification, recency window, headcount band, region and contact caps live in `config.toml` — never hardcoded in `core/`.
- HTTP: honest user agent `outreach-pipeline/0.1 (+contact: <OUTREACH_CONTACT_EMAIL>)`, robots.txt respected, per-domain rate limit of 1 request per 2 seconds.
- Secrets in `.env`, which is gitignored. Missing required key aborts at startup before any network call.
- Contact cap: 3 per company. Default headcount band 20–1000. Default region `US`. Default recency window 180 days.

## Review Focus

Five input classes the spec implies but that no obvious happy-path test exercises. Each has a test assigned to the task that owns the code.

1. **HTML whitespace and entities break the substring guard** — extracted text has collapsed spaces and decoded entities, so a model's verbatim quote fails a naive `in` check and *every* claim is silently rejected. Both sides must be whitespace-normalized before comparison. (Task 9)
2. **A company with zero evidence items** — gate must return a `no_evidence` verdict, not raise on an empty sequence or return a truthy pass. (Task 3)
3. **Unknown headcount** — `headcount=None` must not divide by zero in the size factor; the contact still ranks, using the neutral factor. (Task 5)
4. **Domain variants create duplicate companies** — `https://WWW.Foo.com/careers/`, `foo.com`, and `http://foo.com` must collapse to one company, or the run pays twice for the same people. (Task 6)
5. **Quota smaller than the queue** — when remaining enrichment credits are fewer than companies queued, the stage processes in rank order and marks the remainder `skipped_quota`; it must never truncate silently or raise. (Task 12)

---

### Task 1: Project skeleton and configuration

**Files:**
- Create: `pyproject.toml`, `src/outreach/__init__.py`, `src/outreach/config.py`, `config.toml`, `.env.example`, `tests/__init__.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `load_config(path: Path) -> Config`; `Config` with fields `gate: GateConfig`, `ranking: RankingConfig`, `discovery: DiscoveryConfig`, `paths: PathsConfig`; `require_env(name: str) -> str` raising `ConfigError`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.config'`

- [ ] **Step 3: Write minimal implementation**

```toml
# pyproject.toml
[project]
name = "outreach-pipeline"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "jinja2>=3.1",
    "typer>=0.12",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
outreach = "outreach.cli:app"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

```python
# src/outreach/config.py
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
```

Copy the TOML from the test into a real `config.toml` at the repo root, with `greenhouse_tokens = []`. Write `.env.example` containing `ANTHROPIC_API_KEY=` and `HUNTER_API_KEY=`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pip install -e ".[dev]" && pytest tests/test_config.py -v`
Expected: PASS, 2 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml config.toml .env.example src/outreach/__init__.py src/outreach/config.py tests/
git commit -m "feat: project skeleton and fail-fast configuration"
```

---

### Task 2: Domain types

**Files:**
- Create: `src/outreach/types.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: nothing
- Produces: `SourceClass` (str Enum), and frozen dataclasses `Company`, `Contact`, `SourceDocument`, `EvidenceItem`, `Bottleneck`, `FetchAttempt`, `PostingRef`, `PersonRef`. Every later task imports from here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_types.py
from datetime import date
from outreach.types import SourceClass, EvidenceItem, Contact


def test_source_class_values_are_stable_strings():
    assert SourceClass.JOB_POSTING.value == "job_posting"
    assert SourceClass.NEWS.value == "news"


def test_evidence_item_carries_quote_and_provenance():
    item = EvidenceItem(
        id=1, company_id=7, source_document_id=3,
        claim="reconciliation job exceeds its window",
        quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
        source_class=SourceClass.ENG_BLOG,
        publisher_domain="ledgerline.example",
        published_at=date(2026, 9, 2),
        theme="reconciliation-throughput",
    )
    assert item.source_class is SourceClass.ENG_BLOG
    assert item.published_at.year == 2026


def test_unverified_contact_has_no_email():
    c = Contact(
        id=None, company_id=7, full_name="Deepak Raman",
        title="Engineering Lead, Payments", profile_url="https://example.com/p/1",
        email=None, email_status="unverified", provider="hunter",
        looked_up_at=None, contacted_at=None,
    )
    assert c.email is None
    assert c.email_status == "unverified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.types'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal

EmailStatus = Literal["verified", "unverified", "not_found"]
StageStatus = Literal["pending", "ok", "failed", "skipped_quota"]


class SourceClass(str, Enum):
    JOB_POSTING = "job_posting"
    CAREERS_PAGE = "careers_page"
    ENG_BLOG = "eng_blog"
    CHANGELOG = "changelog"
    GITHUB = "github"
    STATUS_PAGE = "status_page"
    NEWS = "news"


@dataclass(frozen=True)
class Company:
    id: int | None
    canonical_domain: str
    name: str
    headcount: int | None
    headcount_source: str | None


@dataclass(frozen=True)
class Contact:
    id: int | None
    company_id: int
    full_name: str
    title: str
    profile_url: str | None
    email: str | None
    email_status: EmailStatus
    provider: str | None
    looked_up_at: datetime | None
    contacted_at: datetime | None


@dataclass(frozen=True)
class SourceDocument:
    id: int | None
    company_id: int
    url: str
    source_class: SourceClass
    publisher_domain: str
    published_at: date | None
    fetched_at: datetime
    http_status: int
    content_hash: str


@dataclass(frozen=True)
class EvidenceItem:
    id: int | None
    company_id: int
    source_document_id: int
    claim: str
    quote: str
    source_class: SourceClass
    publisher_domain: str
    published_at: date | None
    theme: str


@dataclass(frozen=True)
class Bottleneck:
    id: int | None
    company_id: int
    claim: str
    summary: str
    passed: bool
    reason: str
    evidence_ids: tuple[int, ...]


@dataclass(frozen=True)
class FetchAttempt:
    company_id: int
    source_class: SourceClass
    url: str
    outcome: str
    http_status: int | None
    document_count: int


@dataclass(frozen=True)
class PostingRef:
    company_name: str
    company_domain: str
    title: str
    url: str
    location: str


@dataclass(frozen=True)
class PersonRef:
    full_name: str
    title: str
    profile_url: str | None
    email: str | None
    email_status: EmailStatus
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_types.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/types.py tests/test_types.py
git commit -m "feat: shared domain types"
```

---

### Task 3: The evidence gate

This is the component the whole design exists to protect. Write it first and get the truth table right.

**Files:**
- Create: `src/outreach/core/__init__.py`, `src/outreach/core/gate.py`
- Test: `tests/core/test_gate.py`

**Interfaces:**
- Consumes: `EvidenceItem`, `SourceClass` (Task 2); `GateConfig` (Task 1)
- Produces: `GateVerdict(passed: bool, reason: str, evidence_ids: tuple[int, ...])`; `evaluate(items: Sequence[EvidenceItem], config: GateConfig, today: date) -> GateVerdict`. Reason is one of `"passed"`, `"no_evidence"`, `"insufficient_independent_sources"`, `"no_first_party_source"`, `"all_evidence_stale"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_gate.py
from datetime import date, timedelta
import pytest

from outreach.config import GateConfig
from outreach.core.gate import evaluate
from outreach.types import EvidenceItem, SourceClass

TODAY = date(2026, 9, 21)
FIRST_PARTY = frozenset({
    "job_posting", "careers_page", "eng_blog", "changelog", "github", "status_page",
})
CFG = GateConfig(
    min_independent_sources=2,
    require_first_party=True,
    recency_days=180,
    first_party_classes=FIRST_PARTY,
)


def item(eid: int, cls: SourceClass, domain: str, age_days: int) -> EvidenceItem:
    return EvidenceItem(
        id=eid, company_id=1, source_document_id=eid,
        claim="c", quote="q", source_class=cls, publisher_domain=domain,
        published_at=TODAY - timedelta(days=age_days), theme="t",
    )


def test_no_evidence_returns_no_evidence():
    v = evaluate([], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "no_evidence"
    assert v.evidence_ids == ()


def test_two_postings_same_board_count_as_one_source():
    items = [
        item(1, SourceClass.JOB_POSTING, "acme.example", 5),
        item(2, SourceClass.JOB_POSTING, "acme.example", 9),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "insufficient_independent_sources"


def test_same_class_different_publishers_are_independent():
    items = [
        item(1, SourceClass.NEWS, "wire-a.example", 5),
        item(2, SourceClass.NEWS, "wire-b.example", 9),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "no_first_party_source"


def test_two_independent_but_all_stale_fails():
    items = [
        item(1, SourceClass.ENG_BLOG, "acme.example", 400),
        item(2, SourceClass.NEWS, "wire.example", 500),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is False
    assert v.reason == "all_evidence_stale"


def test_two_independent_fresh_with_first_party_passes():
    items = [
        item(1, SourceClass.ENG_BLOG, "acme.example", 19),
        item(2, SourceClass.NEWS, "wire.example", 40),
    ]
    v = evaluate(items, CFG, TODAY)
    assert v.passed is True
    assert v.reason == "passed"
    assert v.evidence_ids == (1, 2)


def test_single_fresh_first_party_source_fails():
    v = evaluate([item(1, SourceClass.CHANGELOG, "acme.example", 3)], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "insufficient_independent_sources"


def test_missing_published_date_never_counts_as_fresh():
    stale_but_dated = item(1, SourceClass.ENG_BLOG, "acme.example", 400)
    undated = EvidenceItem(
        id=2, company_id=1, source_document_id=2, claim="c", quote="q",
        source_class=SourceClass.NEWS, publisher_domain="wire.example",
        published_at=None, theme="t",
    )
    v = evaluate([stale_but_dated, undated], CFG, TODAY)
    assert v.passed is False
    assert v.reason == "all_evidence_stale"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.core'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/core/gate.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from outreach.config import GateConfig
from outreach.types import EvidenceItem


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    reason: str
    evidence_ids: tuple[int, ...]


def _independence_key(item: EvidenceItem) -> tuple[str, str]:
    return (item.source_class.value, item.publisher_domain)


def evaluate(
    items: Sequence[EvidenceItem], config: GateConfig, today: date
) -> GateVerdict:
    """Decide whether a cluster of claims is a shippable bottleneck.

    Pure: no I/O, no clock. `today` is supplied by the caller.
    """
    ids = tuple(i.id for i in items if i.id is not None)

    if not items:
        return GateVerdict(False, "no_evidence", ())

    if len({_independence_key(i) for i in items}) < config.min_independent_sources:
        return GateVerdict(False, "insufficient_independent_sources", ids)

    if config.require_first_party and not any(
        i.source_class.value in config.first_party_classes for i in items
    ):
        return GateVerdict(False, "no_first_party_source", ids)

    cutoff = today - timedelta(days=config.recency_days)
    if not any(i.published_at is not None and i.published_at >= cutoff for i in items):
        return GateVerdict(False, "all_evidence_stale", ids)

    return GateVerdict(True, "passed", ids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_gate.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/core/ tests/core/
git commit -m "feat: deterministic evidence gate with full truth table"
```

---

### Task 4: Theme clustering

**Files:**
- Create: `src/outreach/core/clustering.py`
- Test: `tests/core/test_clustering.py`

**Interfaces:**
- Consumes: `EvidenceItem` (Task 2)
- Produces: `normalize_theme(raw: str) -> str`; `cluster_by_theme(items: Sequence[EvidenceItem]) -> dict[str, list[EvidenceItem]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_clustering.py
from datetime import date
from outreach.core.clustering import normalize_theme, cluster_by_theme
from outreach.types import EvidenceItem, SourceClass


def item(eid: int, theme: str) -> EvidenceItem:
    return EvidenceItem(
        id=eid, company_id=1, source_document_id=eid, claim="c", quote="q",
        source_class=SourceClass.ENG_BLOG, publisher_domain="acme.example",
        published_at=date(2026, 9, 1), theme=theme,
    )


def test_normalize_theme_lowercases_and_hyphenates():
    assert normalize_theme("  Reconciliation Throughput ") == "reconciliation-throughput"
    assert normalize_theme("Onboarding_Speed") == "onboarding-speed"
    assert normalize_theme("data--quality") == "data-quality"


def test_cluster_groups_equivalent_labels():
    items = [item(1, "Reconciliation Throughput"), item(2, "reconciliation-throughput"),
             item(3, "Merchant Onboarding")]
    clusters = cluster_by_theme(items)
    assert set(clusters) == {"reconciliation-throughput", "merchant-onboarding"}
    assert [i.id for i in clusters["reconciliation-throughput"]] == [1, 2]


def test_empty_input_gives_empty_clusters():
    assert cluster_by_theme([]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_clustering.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.core.clustering'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/core/clustering.py
from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence

from outreach.types import EvidenceItem

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_theme(raw: str) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single hyphen.

    Deliberately dumb: exact match after normalization, no embeddings and no
    similarity threshold. A split cluster fails the gate and surfaces as a
    no-bottleneck company, which is the safe direction to fail in.
    """
    return _NON_ALNUM.sub("-", raw.strip().lower()).strip("-")


def cluster_by_theme(items: Sequence[EvidenceItem]) -> dict[str, list[EvidenceItem]]:
    clusters: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in items:
        clusters[normalize_theme(item.theme)].append(item)
    return dict(clusters)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_clustering.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/core/clustering.py tests/core/test_clustering.py
git commit -m "feat: exact-match theme clustering"
```

---

### Task 5: Contact ranking

**Files:**
- Create: `src/outreach/core/ranking.py`
- Test: `tests/core/test_ranking.py`

**Interfaces:**
- Consumes: `RankingConfig` (Task 1)
- Produces: `ContactScore(score: float, tier: int, explanation: str)`; `score_title(title, headcount, role_keywords, config) -> ContactScore | None` (None means excluded); `rank_contacts(people: Sequence[PersonRef], headcount, role_keywords, config) -> list[tuple[PersonRef, ContactScore]]` sorted descending and truncated to `config.max_contacts_per_company`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_ranking.py
from outreach.config import RankingConfig
from outreach.core.ranking import score_title, rank_contacts
from outreach.types import PersonRef

CFG = RankingConfig(
    max_contacts_per_company=3,
    exclude_title_patterns=("recruit", "talent", "sourcer"),
)


def person(name: str, title: str) -> PersonRef:
    return PersonRef(full_name=name, title=title, profile_url=None,
                     email=None, email_status="not_found")


def test_recruiters_are_excluded():
    assert score_title("Technical Recruiter", 50, ["backend"], CFG) is None
    assert score_title("Head of Talent", 50, ["backend"], CFG) is None


def test_founder_at_small_company_outranks_founder_at_large_one():
    small = score_title("Co-founder & CTO", 48, ["backend"], CFG)
    large = score_title("Co-founder & CTO", 900, ["backend"], CFG)
    assert small.tier == 1 and large.tier == 1
    assert small.score > large.score


def test_tier_one_outranks_tier_three_at_same_size():
    vp = score_title("VP Engineering", 200, ["backend"], CFG)
    lead = score_title("Engineering Manager", 200, ["backend"], CFG)
    assert vp.score > lead.score


def test_role_relevance_breaks_ties_within_a_tier():
    relevant = score_title("Engineering Manager, Backend", 200, ["backend"], CFG)
    other = score_title("Engineering Manager, Design Systems", 200, ["backend"], CFG)
    assert relevant.score > other.score


def test_unknown_headcount_uses_neutral_factor_and_does_not_raise():
    s = score_title("Co-founder & CTO", None, ["backend"], CFG)
    assert s is not None
    assert s.tier == 1
    assert s.score > 0


def test_rank_contacts_sorts_and_truncates_to_cap():
    people = [
        person("A", "Staff Engineer"),
        person("B", "Co-founder & CTO"),
        person("C", "Technical Recruiter"),
        person("D", "VP Engineering"),
        person("E", "Engineering Manager"),
    ]
    ranked = rank_contacts(people, 60, ["backend"], CFG)
    assert len(ranked) == 3
    assert [p.full_name for p, _ in ranked] == ["B", "D", "E"]
    assert all(p.full_name != "C" for p, _ in ranked)


def test_explanation_mentions_tier_and_headcount():
    s = score_title("Head of Engineering", 75, ["backend"], CFG)
    assert "tier 1" in s.explanation
    assert "75" in s.explanation
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.core.ranking'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/core/ranking.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from outreach.config import RankingConfig
from outreach.types import PersonRef

# Ordered most senior first; first match wins.
_TIERS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("founder", "cto", "chief technology", "vp engineering", "vp of engineering",
         "head of engineering", "head of platform", "head of product engineering")),
    (2, ("director", "senior manager", "head of")),
    (3, ("engineering manager", "team lead", "tech lead", "eng manager")),
    (4, ("staff engineer", "principal engineer", "senior engineer", "senior software")),
)

_TIER_WEIGHT = {1: 100.0, 2: 60.0, 3: 40.0, 4: 20.0}
_NEUTRAL_HEADCOUNT = 500
_MAX_SIZE_FACTOR = 5.0
_RELEVANCE_BONUS = 25.0


@dataclass(frozen=True)
class ContactScore:
    score: float
    tier: int
    explanation: str


def _tier_for(title_lower: str) -> int | None:
    for tier, patterns in _TIERS:
        if any(p in title_lower for p in patterns):
            return tier
    return None


def _size_factor(tier: int, headcount: int | None) -> float:
    """Small companies make senior people genuinely reachable; large ones don't.

    Unknown headcount uses the neutral value rather than dividing by None.
    """
    if tier > 2:
        return 1.0
    effective = headcount if headcount and headcount > 0 else _NEUTRAL_HEADCOUNT
    return min(_MAX_SIZE_FACTOR, max(1.0, _NEUTRAL_HEADCOUNT / effective))


def score_title(
    title: str,
    headcount: int | None,
    role_keywords: Sequence[str],
    config: RankingConfig,
) -> ContactScore | None:
    lowered = title.lower()
    if any(pattern in lowered for pattern in config.exclude_title_patterns):
        return None

    tier = _tier_for(lowered)
    if tier is None:
        return None

    factor = _size_factor(tier, headcount)
    relevant = any(k.lower() in lowered for k in role_keywords)
    score = _TIER_WEIGHT[tier] * factor + (_RELEVANCE_BONUS if relevant else 0.0)

    headcount_text = str(headcount) if headcount else "headcount unknown"
    parts = [f"tier {tier}", f"{headcount_text} employees" if headcount else headcount_text]
    if relevant:
        parts.append("org matches role")
    return ContactScore(score=score, tier=tier, explanation=" · ".join(parts))


def rank_contacts(
    people: Sequence[PersonRef],
    headcount: int | None,
    role_keywords: Sequence[str],
    config: RankingConfig,
) -> list[tuple[PersonRef, ContactScore]]:
    scored: list[tuple[PersonRef, ContactScore]] = []
    for person in people:
        result = score_title(person.title, headcount, role_keywords, config)
        if result is not None:
            scored.append((person, result))
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    return scored[: config.max_contacts_per_company]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_ranking.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/core/ranking.py tests/core/test_ranking.py
git commit -m "feat: deterministic contact ranking with recruiter exclusion"
```

---

### Task 6: Domain canonicalization and company dedupe

**Files:**
- Create: `src/outreach/core/dedupe.py`
- Test: `tests/core/test_dedupe.py`

**Interfaces:**
- Consumes: `PostingRef` (Task 2)
- Produces: `canonical_domain(value: str) -> str`; `dedupe_postings(postings: Sequence[PostingRef]) -> dict[str, list[PostingRef]]` keyed by canonical domain

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_dedupe.py
import pytest
from outreach.core.dedupe import canonical_domain, dedupe_postings
from outreach.types import PostingRef


@pytest.mark.parametrize("raw", [
    "https://WWW.Foo.com/careers/",
    "http://foo.com",
    "foo.com",
    "  FOO.com  ",
    "https://foo.com:443/jobs?x=1",
    "//www.foo.com/",
])
def test_domain_variants_collapse_to_one_value(raw):
    assert canonical_domain(raw) == "foo.com"


def test_subdomains_are_preserved_when_not_www():
    assert canonical_domain("https://jobs.foo.com/x") == "jobs.foo.com"


def test_empty_value_raises():
    with pytest.raises(ValueError):
        canonical_domain("   ")


def test_dedupe_groups_postings_by_canonical_domain():
    postings = [
        PostingRef("Foo", "https://WWW.Foo.com/", "Backend Engineer", "u1", "Chicago"),
        PostingRef("Foo Inc", "foo.com", "Platform Engineer", "u2", "Remote"),
        PostingRef("Bar", "bar.example", "Backend Engineer", "u3", "NYC"),
    ]
    grouped = dedupe_postings(postings)
    assert set(grouped) == {"foo.com", "bar.example"}
    assert len(grouped["foo.com"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_dedupe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.core.dedupe'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/core/dedupe.py
from __future__ import annotations

from collections import defaultdict
from typing import Sequence
from urllib.parse import urlsplit

from outreach.types import PostingRef


def canonical_domain(value: str) -> str:
    """Reduce any URL or bare domain to a comparable host.

    Domain variants are the main way one company gets researched twice and
    billed twice, so every path into the company table goes through here.
    """
    raw = value.strip().lower()
    if not raw:
        raise ValueError("cannot canonicalize an empty domain")

    if "//" not in raw:
        raw = "//" + raw
    host = urlsplit(raw).hostname or ""
    if not host:
        raise ValueError(f"no host found in {value!r}")

    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def dedupe_postings(postings: Sequence[PostingRef]) -> dict[str, list[PostingRef]]:
    grouped: dict[str, list[PostingRef]] = defaultdict(list)
    for posting in postings:
        grouped[canonical_domain(posting.company_domain)].append(posting)
    return dict(grouped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_dedupe.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/core/dedupe.py tests/core/test_dedupe.py
git commit -m "feat: canonical domain handling and posting dedupe"
```

---

### Task 7: SQLite schema, connection, and document cache

**Files:**
- Create: `src/outreach/store/__init__.py`, `src/outreach/store/schema.sql`, `src/outreach/store/db.py`, `src/outreach/store/cache.py`
- Test: `tests/store/test_db.py`, `tests/store/test_cache.py`

**Interfaces:**
- Consumes: `PathsConfig` (Task 1)
- Produces: `connect(path: Path) -> sqlite3.Connection` (applies schema, enables foreign keys, `row_factory = sqlite3.Row`); `DocumentCache(root: Path)` with `store(content: bytes) -> str` returning content hash, `path_for(content_hash) -> Path`, `read(content_hash) -> str`, `has(content_hash) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_db.py
from outreach.store.db import connect


def test_connect_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "contacts", "source_documents", "runs", "run_companies",
            "evidence_items", "bottlenecks", "fetch_attempts"} <= names


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    connect(path).close()
    conn = connect(path)
    assert conn.execute("SELECT count(*) c FROM companies").fetchone()["c"] == 0


def test_company_domain_is_unique(tmp_path):
    import sqlite3
    import pytest
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A2')")
```

```python
# tests/store/test_cache.py
from outreach.store.cache import DocumentCache


def test_store_returns_stable_hash_and_reads_back(tmp_path):
    cache = DocumentCache(tmp_path)
    h1 = cache.store(b"hello world")
    h2 = cache.store(b"hello world")
    assert h1 == h2
    assert cache.has(h1)
    assert cache.read(h1) == "hello world"


def test_different_content_gives_different_hash(tmp_path):
    cache = DocumentCache(tmp_path)
    assert cache.store(b"a") != cache.store(b"b")


def test_has_is_false_for_unknown_hash(tmp_path):
    assert DocumentCache(tmp_path).has("0" * 64) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.store'`

- [ ] **Step 3: Write minimal implementation**

```sql
-- src/outreach/store/schema.sql
CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  canonical_domain TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  headcount INTEGER,
  headcount_source TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  full_name TEXT NOT NULL,
  title TEXT NOT NULL,
  profile_url TEXT,
  email TEXT,
  email_status TEXT NOT NULL,
  provider TEXT,
  looked_up_at TEXT,
  contacted_at TEXT,
  UNIQUE (company_id, full_name)
);

CREATE TABLE IF NOT EXISTS source_documents (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  url TEXT NOT NULL,
  source_class TEXT NOT NULL,
  publisher_domain TEXT NOT NULL,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE (url, content_hash)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  role_title TEXT NOT NULL,
  sector TEXT NOT NULL,
  region TEXT NOT NULL,
  headcount_min INTEGER NOT NULL,
  headcount_max INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_companies (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  UNIQUE (run_id, company_id, stage)
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
  claim TEXT NOT NULL,
  quote TEXT NOT NULL,
  source_class TEXT NOT NULL,
  publisher_domain TEXT NOT NULL,
  published_at TEXT,
  theme TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bottlenecks (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  claim TEXT NOT NULL,
  summary TEXT NOT NULL,
  passed INTEGER NOT NULL,
  reason TEXT NOT NULL,
  evidence_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_attempts (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  source_class TEXT NOT NULL,
  url TEXT NOT NULL,
  outcome TEXT NOT NULL,
  http_status INTEGER,
  document_count INTEGER NOT NULL DEFAULT 0
);
```

```python
# src/outreach/store/db.py
from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = resources.files("outreach.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    return conn
```

```python
# src/outreach/store/cache.py
from __future__ import annotations

import hashlib
from pathlib import Path


class DocumentCache:
    """Content-addressed store for fetched documents.

    Keeps SQLite small, and the files double as test fixtures — the gate's
    tests read the same bytes a real run saw.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, content_hash: str) -> Path:
        return self.root / content_hash[:2] / content_hash

    def store(self, content: bytes) -> str:
        content_hash = hashlib.sha256(content).hexdigest()
        target = self.path_for(content_hash)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return content_hash

    def has(self, content_hash: str) -> bool:
        return self.path_for(content_hash).exists()

    def read(self, content_hash: str) -> str:
        return self.path_for(content_hash).read_text(encoding="utf-8", errors="replace")
```

Add `[tool.setuptools.package-data] outreach = ["store/*.sql", "render/templates/*.j2"]` to `pyproject.toml`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/ -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/store/ tests/store/ pyproject.toml
git commit -m "feat: sqlite schema, connection, and content-addressed document cache"
```

---

### Task 8: Repositories and per-company checkpointing

**Files:**
- Create: `src/outreach/store/dimensions.py`, `src/outreach/store/runs.py`
- Test: `tests/store/test_dimensions.py`, `tests/store/test_runs.py`

**Interfaces:**
- Consumes: `connect` (Task 7), all types (Task 2)
- Produces:
  - `CompanyRepo(conn)`: `upsert(domain, name, headcount, headcount_source) -> int`, `get(company_id) -> Company`
  - `ContactRepo(conn)`: `upsert(company_id, PersonRef, provider, looked_up_at) -> int`, `for_company(company_id) -> list[Contact]`, `already_resolved(company_id, full_name) -> Contact | None`, `mark_contacted(contact_id, when)`
  - `DocumentRepo(conn)`: `insert(SourceDocument) -> int`, `for_company(company_id) -> list[SourceDocument]`
  - `RunRepo(conn)`: `create(role_title, sector, region, band, started_at) -> int`, `finish(run_id, when)`, `set_stage(run_id, company_id, stage, status, error=None)`, `stage_status(run_id, company_id, stage) -> str | None`, `pending_companies(run_id, stage) -> list[int]`
  - `EvidenceRepo(conn)`: `insert(run_id, EvidenceItem) -> int`, `for_company(run_id, company_id) -> list[EvidenceItem]`
  - `BottleneckRepo(conn)`: `insert(run_id, Bottleneck) -> int`, `for_run(run_id) -> list[Bottleneck]`
  - `FetchAttemptRepo(conn)`: `insert(run_id, FetchAttempt)`, `for_company(run_id, company_id) -> list[FetchAttempt]`

- [ ] **Step 1: Write the failing test**

```python
# tests/store/test_dimensions.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.types import PersonRef


def repos(tmp_path):
    conn = connect(tmp_path / "t.db")
    return CompanyRepo(conn), ContactRepo(conn)


def test_company_upsert_is_stable_across_runs(tmp_path):
    companies, _ = repos(tmp_path)
    first = companies.upsert("foo.com", "Foo", 48, "careers page")
    second = companies.upsert("foo.com", "Foo Inc", 52, "careers page")
    assert first == second
    assert companies.get(first).headcount == 52


def test_contact_resolved_in_earlier_run_is_found_again(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("Marisol Okonkwo", "CTO", None,
                       "m@foo.com", "verified")
    contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    found = contacts.already_resolved(cid, "Marisol Okonkwo")
    assert found is not None
    assert found.email == "m@foo.com"
    assert found.email_status == "verified"


def test_unverified_contact_persists_without_email(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("Deepak Raman", "Eng Lead", "https://p/1", None, "unverified")
    contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    stored = contacts.for_company(cid)[0]
    assert stored.email is None
    assert stored.email_status == "unverified"


def test_mark_contacted_is_visible_on_next_read(tmp_path):
    companies, contacts = repos(tmp_path)
    cid = companies.upsert("foo.com", "Foo", 48, "careers page")
    person = PersonRef("A B", "CTO", None, "a@foo.com", "verified")
    contact_id = contacts.upsert(cid, person, "hunter", datetime(2026, 9, 1))
    contacts.mark_contacted(contact_id, datetime(2026, 9, 5))
    assert contacts.for_company(cid)[0].contacted_at is not None
```

```python
# tests/store/test_runs.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo
from outreach.store.runs import RunRepo


def test_stage_checkpoint_round_trips(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    cid = companies.upsert("foo.com", "Foo", 48, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())

    runs.set_stage(rid, cid, "profile", "ok")
    assert runs.stage_status(rid, cid, "profile") == "ok"

    runs.set_stage(rid, cid, "profile", "failed", error="boom")
    assert runs.stage_status(rid, cid, "profile") == "failed"


def test_pending_companies_excludes_completed_ones(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    runs.set_stage(rid, a, "discover", "ok")
    runs.set_stage(rid, b, "discover", "ok")
    runs.set_stage(rid, a, "contacts", "ok")
    assert runs.pending_companies(rid, "contacts") == [b]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/store/test_dimensions.py tests/store/test_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.store.dimensions'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/store/dimensions.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from outreach.types import Company, Contact, PersonRef, SourceClass, SourceDocument


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class CompanyRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(
        self, domain: str, name: str, headcount: int | None, headcount_source: str | None
    ) -> int:
        self.conn.execute(
            """INSERT INTO companies (canonical_domain, name, headcount, headcount_source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(canonical_domain) DO UPDATE SET
                 name = excluded.name,
                 headcount = COALESCE(excluded.headcount, companies.headcount),
                 headcount_source = COALESCE(excluded.headcount_source,
                                             companies.headcount_source)""",
            (domain, name, headcount, headcount_source),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM companies WHERE canonical_domain = ?", (domain,)
        ).fetchone()
        return int(row["id"])

    def get(self, company_id: int) -> Company:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
        return Company(row["id"], row["canonical_domain"], row["name"],
                       row["headcount"], row["headcount_source"])


class ContactRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert(
        self, company_id: int, person: PersonRef, provider: str, looked_up_at: datetime
    ) -> int:
        email = person.email if person.email_status == "verified" else None
        self.conn.execute(
            """INSERT INTO contacts (company_id, full_name, title, profile_url, email,
                                     email_status, provider, looked_up_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(company_id, full_name) DO UPDATE SET
                 title = excluded.title,
                 profile_url = COALESCE(excluded.profile_url, contacts.profile_url),
                 email = COALESCE(excluded.email, contacts.email),
                 email_status = excluded.email_status,
                 looked_up_at = excluded.looked_up_at""",
            (company_id, person.full_name, person.title, person.profile_url, email,
             person.email_status, provider, looked_up_at.isoformat()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM contacts WHERE company_id = ? AND full_name = ?",
            (company_id, person.full_name),
        ).fetchone()
        return int(row["id"])

    def _row_to_contact(self, row: sqlite3.Row) -> Contact:
        return Contact(row["id"], row["company_id"], row["full_name"], row["title"],
                       row["profile_url"], row["email"], row["email_status"],
                       row["provider"], _dt(row["looked_up_at"]), _dt(row["contacted_at"]))

    def for_company(self, company_id: int) -> list[Contact]:
        rows = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? ORDER BY id", (company_id,)
        ).fetchall()
        return [self._row_to_contact(r) for r in rows]

    def already_resolved(self, company_id: int, full_name: str) -> Contact | None:
        row = self.conn.execute(
            "SELECT * FROM contacts WHERE company_id = ? AND full_name = ?",
            (company_id, full_name),
        ).fetchone()
        return self._row_to_contact(row) if row else None

    def mark_contacted(self, contact_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE contacts SET contacted_at = ? WHERE id = ?",
                          (when.isoformat(), contact_id))
        self.conn.commit()


class DocumentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, doc: SourceDocument) -> int:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO source_documents
               (company_id, url, source_class, publisher_domain, published_at,
                fetched_at, http_status, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc.company_id, doc.url, doc.source_class.value, doc.publisher_domain,
             doc.published_at.isoformat() if doc.published_at else None,
             doc.fetched_at.isoformat(), doc.http_status, doc.content_hash),
        )
        self.conn.commit()
        if cur.lastrowid:
            return int(cur.lastrowid)
        row = self.conn.execute(
            "SELECT id FROM source_documents WHERE url = ? AND content_hash = ?",
            (doc.url, doc.content_hash),
        ).fetchone()
        return int(row["id"])

    def for_company(self, company_id: int) -> list[SourceDocument]:
        rows = self.conn.execute(
            "SELECT * FROM source_documents WHERE company_id = ? ORDER BY id",
            (company_id,)).fetchall()
        return [
            SourceDocument(
                r["id"], r["company_id"], r["url"], SourceClass(r["source_class"]),
                r["publisher_domain"],
                datetime.fromisoformat(r["published_at"]).date() if r["published_at"] else None,
                datetime.fromisoformat(r["fetched_at"]), r["http_status"], r["content_hash"],
            ) for r in rows
        ]
```

```python
# src/outreach/store/runs.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from outreach.types import Bottleneck, EvidenceItem, FetchAttempt, SourceClass


class RunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, role_title: str, sector: str, region: str,
               band: tuple[int, int], started_at: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (role_title, sector, region, headcount_min,
                                 headcount_max, started_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'running')""",
            (role_title, sector, region, band[0], band[1], started_at.isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish(self, run_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE runs SET finished_at = ?, status = 'done' WHERE id = ?",
                          (when.isoformat(), run_id))
        self.conn.commit()

    def set_stage(self, run_id: int, company_id: int, stage: str,
                  status: str, error: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO run_companies (run_id, company_id, stage, status, error)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id, company_id, stage) DO UPDATE SET
                 status = excluded.status, error = excluded.error""",
            (run_id, company_id, stage, status, error),
        )
        self.conn.commit()

    def stage_status(self, run_id: int, company_id: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM run_companies WHERE run_id=? AND company_id=? AND stage=?",
            (run_id, company_id, stage)).fetchone()
        return row["status"] if row else None

    def pending_companies(self, run_id: int, stage: str) -> list[int]:
        """Companies that reached the run but have no successful row for `stage`."""
        rows = self.conn.execute(
            """SELECT DISTINCT company_id FROM run_companies rc WHERE run_id = ?
               AND NOT EXISTS (
                 SELECT 1 FROM run_companies done
                 WHERE done.run_id = rc.run_id AND done.company_id = rc.company_id
                   AND done.stage = ? AND done.status = 'ok')
               ORDER BY company_id""",
            (run_id, stage)).fetchall()
        return [int(r["company_id"]) for r in rows]


class EvidenceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, item: EvidenceItem) -> int:
        cur = self.conn.execute(
            """INSERT INTO evidence_items (run_id, company_id, source_document_id, claim,
                                           quote, source_class, publisher_domain,
                                           published_at, theme)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, item.company_id, item.source_document_id, item.claim, item.quote,
             item.source_class.value, item.publisher_domain,
             item.published_at.isoformat() if item.published_at else None, item.theme),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def for_company(self, run_id: int, company_id: int) -> list[EvidenceItem]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_items WHERE run_id=? AND company_id=? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [
            EvidenceItem(
                r["id"], r["company_id"], r["source_document_id"], r["claim"], r["quote"],
                SourceClass(r["source_class"]), r["publisher_domain"],
                datetime.fromisoformat(r["published_at"]).date() if r["published_at"] else None,
                r["theme"],
            ) for r in rows
        ]


class BottleneckRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, b: Bottleneck) -> int:
        cur = self.conn.execute(
            """INSERT INTO bottlenecks (run_id, company_id, claim, summary, passed,
                                        reason, evidence_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, b.company_id, b.claim, b.summary, int(b.passed), b.reason,
             ",".join(str(i) for i in b.evidence_ids)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def for_run(self, run_id: int) -> list[Bottleneck]:
        rows = self.conn.execute(
            "SELECT * FROM bottlenecks WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [
            Bottleneck(
                r["id"], r["company_id"], r["claim"], r["summary"], bool(r["passed"]),
                r["reason"],
                tuple(int(x) for x in r["evidence_ids"].split(",") if x),
            ) for r in rows
        ]


class FetchAttemptRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, attempt: FetchAttempt) -> None:
        self.conn.execute(
            """INSERT INTO fetch_attempts (run_id, company_id, source_class, url,
                                           outcome, http_status, document_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, attempt.company_id, attempt.source_class.value, attempt.url,
             attempt.outcome, attempt.http_status, attempt.document_count),
        )
        self.conn.commit()

    def for_company(self, run_id: int, company_id: int) -> list[FetchAttempt]:
        rows = self.conn.execute(
            "SELECT * FROM fetch_attempts WHERE run_id=? AND company_id=? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [
            FetchAttempt(r["company_id"], SourceClass(r["source_class"]), r["url"],
                         r["outcome"], r["http_status"], r["document_count"])
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/store/ -v`
Expected: PASS, 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/store/dimensions.py src/outreach/store/runs.py tests/store/
git commit -m "feat: repositories with cross-run dimensions and stage checkpoints"
```

---

### Task 9: LLM protocol, fake, and extraction with the substring guard

**Files:**
- Create: `src/outreach/llm/__init__.py`, `src/outreach/llm/base.py`, `src/outreach/llm/fake.py`, `src/outreach/extraction/__init__.py`, `src/outreach/extraction/extract.py`
- Test: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `SourceDocument`, `EvidenceItem` (Task 2); `DocumentCache` (Task 7); `EvidenceRepo` (Task 8)
- Produces: `RawClaim(claim, quote, theme)`; `LLMClient` Protocol with `expand_titles(role_title) -> list[str]`, `extract_claims(text) -> list[RawClaim]`, `write_summary(claim, quotes) -> str`; `FakeLLM(claims_by_text_hash, titles, summary)`; `normalize_ws(text) -> str`; `ExtractionResult(accepted: int, rejected: int)`; `extract_and_persist(run_id: int, doc: SourceDocument, text: str, llm: LLMClient, evidence_repo: EvidenceRepo, document_id: int) -> ExtractionResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_extraction.py
from datetime import datetime, date

from outreach.extraction.extract import extract_and_persist, normalize_ws
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, DocumentRepo
from outreach.store.runs import EvidenceRepo, RunRepo
from outreach.types import SourceClass, SourceDocument

DOC_TEXT = """
Our nightly   reconciliation job now regularly
exceeds its 6-hour window. We are splitting it into per-ledger workers.
"""


def setup(tmp_path):
    conn = connect(tmp_path / "t.db")
    company_id = CompanyRepo(conn).upsert("acme.example", "Acme", 48, "careers")
    run_id = RunRepo(conn).create("Backend Engineer", "fintech", "US",
                                  (20, 1000), datetime.now())
    doc = SourceDocument(None, company_id, "https://acme.example/blog/1",
                         SourceClass.ENG_BLOG, "acme.example", date(2026, 9, 2),
                         datetime.now(), 200, "hash1")
    doc_id = DocumentRepo(conn).insert(doc)
    return conn, run_id, company_id, doc_id, doc


def test_normalize_ws_collapses_runs_and_newlines():
    assert normalize_ws("a   b\n\tc ") == "a b c"


def test_quote_with_different_whitespace_still_matches(tmp_path):
    """The failure this prevents: HTML text extraction collapses whitespace,
    so a naive `in` check rejects every claim and the pipeline goes silent."""
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation exceeds its window",
        quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
        theme="reconciliation-throughput",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 1
    assert result.rejected == 0
    assert len(repo.for_company(run_id, company_id)) == 1


def test_invented_quote_is_rejected_and_not_persisted(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="they are migrating to Kubernetes",
        quote="We are migrating the whole platform to Kubernetes this quarter.",
        theme="infra-migration",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1
    assert repo.for_company(run_id, company_id) == []


def test_mixed_batch_keeps_only_real_quotes(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[
        RawClaim("real", "splitting it into per-ledger workers", "reconciliation-throughput"),
        RawClaim("fake", "we have no engineers left", "attrition"),
    ])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert (result.accepted, result.rejected) == (1, 1)
    assert repo.for_company(run_id, company_id)[0].claim == "real"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.llm'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/llm/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class RawClaim:
    claim: str
    quote: str
    theme: str


class LLMClient(Protocol):
    """Exactly three jobs. The gate is not one of them."""

    def expand_titles(self, role_title: str) -> list[str]: ...

    def extract_claims(self, text: str) -> list[RawClaim]: ...

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str: ...
```

```python
# src/outreach/llm/fake.py
from __future__ import annotations

from typing import Sequence

from outreach.llm.base import RawClaim


class FakeLLM:
    """Records fixed responses. No test touches a live API."""

    def __init__(self, claims: list[RawClaim] | None = None,
                 titles: list[str] | None = None, summary: str = "summary text") -> None:
        self._claims = claims or []
        self._titles = titles or []
        self._summary = summary
        self.extract_calls = 0

    def expand_titles(self, role_title: str) -> list[str]:
        return self._titles or [role_title]

    def extract_claims(self, text: str) -> list[RawClaim]:
        self.extract_calls += 1
        return list(self._claims)

    def write_summary(self, claim: str, quotes: Sequence[str]) -> str:
        return self._summary
```

```python
# src/outreach/extraction/extract.py
from __future__ import annotations

import re
from dataclasses import dataclass

from outreach.llm.base import LLMClient
from outreach.store.runs import EvidenceRepo
from outreach.types import EvidenceItem, SourceDocument

_WS = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends.

    Applied to BOTH sides of the substring guard. Without this, extracted
    HTML text (collapsed spaces, stray newlines) never matches a model's
    quote and the pipeline silently produces nothing.
    """
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class ExtractionResult:
    accepted: int
    rejected: int


def extract_and_persist(
    run_id: int,
    doc: SourceDocument,
    text: str,
    llm: LLMClient,
    evidence_repo: EvidenceRepo,
    document_id: int,
) -> ExtractionResult:
    """Pull claims from one document, keeping only those whose quote is real.

    A rejected quote is expected output, not an error. The count is reported
    so a drifting extraction prompt is visible in diagnostics.
    """
    haystack = normalize_ws(text)
    accepted = rejected = 0

    for raw in llm.extract_claims(text):
        if normalize_ws(raw.quote) not in haystack:
            rejected += 1
            continue
        evidence_repo.insert(run_id, EvidenceItem(
            id=None, company_id=doc.company_id, source_document_id=document_id,
            claim=raw.claim, quote=raw.quote, source_class=doc.source_class,
            publisher_domain=doc.publisher_domain, published_at=doc.published_at,
            theme=raw.theme,
        ))
        accepted += 1

    return ExtractionResult(accepted=accepted, rejected=rejected)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_extraction.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/llm/ src/outreach/extraction/ tests/test_extraction.py
git commit -m "feat: three-method LLM protocol and substring-guarded extraction"
```

---

### Task 10: Polite HTTP fetcher

**Files:**
- Create: `src/outreach/net/__init__.py`, `src/outreach/net/fetcher.py`
- Test: `tests/test_fetcher.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `FetchOutcome(url, status, body, outcome)` where outcome is `"ok" | "http_error" | "blocked_by_robots" | "network_error"`; `Fetcher(client, rate_limit_seconds=2.0, sleep=time.sleep)` with `get(url) -> FetchOutcome`; `USER_AGENT` constant

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fetcher.py
import httpx
from outreach.net.fetcher import Fetcher, USER_AGENT


def handler_factory(routes):
    def handler(request: httpx.Request) -> httpx.Response:
        return routes[request.url.path](request)
    return handler


def build(routes, **kwargs):
    transport = httpx.MockTransport(handler_factory(routes))
    client = httpx.Client(transport=transport)
    sleeps: list[float] = []
    fetcher = Fetcher(client, rate_limit_seconds=2.0, sleep=sleeps.append, **kwargs)
    return fetcher, sleeps


def test_successful_fetch_returns_body_and_sends_honest_user_agent():
    seen = {}

    def ok(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="hello")

    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(404),
                        "/page": ok})
    out = fetcher.get("https://acme.example/page")
    assert out.outcome == "ok"
    assert out.body == "hello"
    assert seen["ua"] == USER_AGENT


def test_robots_disallow_blocks_the_fetch():
    fetcher, _ = build({
        "/robots.txt": lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private"),
        "/private": lambda r: httpx.Response(200, text="secret"),
    })
    out = fetcher.get("https://acme.example/private")
    assert out.outcome == "blocked_by_robots"
    assert out.body is None


def test_http_error_is_reported_not_raised():
    fetcher, _ = build({"/robots.txt": lambda r: httpx.Response(404),
                        "/missing": lambda r: httpx.Response(404, text="nope")})
    out = fetcher.get("https://acme.example/missing")
    assert out.outcome == "http_error"
    assert out.status == 404


def test_second_request_to_same_host_sleeps_for_the_rate_limit():
    fetcher, sleeps = build({"/robots.txt": lambda r: httpx.Response(404),
                             "/a": lambda r: httpx.Response(200, text="a"),
                             "/b": lambda r: httpx.Response(200, text="b")})
    fetcher.get("https://acme.example/a")
    fetcher.get("https://acme.example/b")
    assert any(s > 0 for s in sleeps)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.net'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/net/fetcher.py
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

DEFAULT_USER_AGENT = "outreach-pipeline/0.1"  # contact appended from env


@dataclass(frozen=True)
class FetchOutcome:
    url: str
    status: int | None
    body: str | None
    outcome: str


class Fetcher:
    """One polite request at a time, per host.

    These are companies the user intends to email, so an honest user agent,
    robots.txt and a real rate limit are requirements, not niceties.
    """

    def __init__(
        self,
        client: httpx.Client,
        rate_limit_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.rate_limit_seconds = rate_limit_seconds
        self.sleep = sleep
        self.now = now
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.rate_limit_seconds - (self.now() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[host] = self.now()

    def _robots_for(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        parser = urllib.robotparser.RobotFileParser()
        try:
            self._throttle(host)
            response = self.client.get(f"{scheme}://{host}/robots.txt",
                                       headers={"User-Agent": USER_AGENT}, timeout=10.0)
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        except httpx.HTTPError:
            parser.parse([])
        self._robots[host] = parser
        return parser

    def get(self, url: str) -> FetchOutcome:
        parts = urlsplit(url)
        host = parts.netloc
        if not self._robots_for(parts.scheme, host).can_fetch(USER_AGENT, url):
            return FetchOutcome(url, None, None, "blocked_by_robots")

        try:
            self._throttle(host)
            response = self.client.get(url, headers={"User-Agent": USER_AGENT},
                                       timeout=20.0, follow_redirects=True)
        except httpx.HTTPError:
            return FetchOutcome(url, None, None, "network_error")

        if response.status_code >= 400:
            return FetchOutcome(url, response.status_code, None, "http_error")
        return FetchOutcome(url, response.status_code, response.text, "ok")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetcher.py -v`
Expected: PASS, 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/net/ tests/test_fetcher.py
git commit -m "feat: polite HTTP fetcher honouring robots.txt and per-host rate limits"
```

---

### Task 11: Source protocols, job board adapter, and surface adapters

**Note on discovery:** there is no free cross-company job-search API. V1 ships `GreenhouseBoardSource`, which queries the public Greenhouse board API for each company token in `config.discovery.greenhouse_tokens` and keeps only postings whose title matches the expanded role terms. Tokens are bootstrapped in bulk rather than hand-picked per company. The protocol exists so an aggregator adapter can replace it without touching any other stage.

**Files:**
- Create: `src/outreach/sources/__init__.py`, `src/outreach/sources/base.py`, `src/outreach/sources/jobboards/__init__.py`, `src/outreach/sources/jobboards/greenhouse.py`, `src/outreach/sources/jobboards/fake.py`, `src/outreach/sources/surfaces/__init__.py`, `src/outreach/sources/surfaces/standard.py`
- Test: `tests/sources/test_greenhouse.py`, `tests/sources/test_surfaces.py`, `tests/contract/test_source_contracts.py`

**Interfaces:**
- Consumes: `PostingRef`, `SourceClass` (Task 2); `Fetcher`, `FetchOutcome` (Task 10); `canonical_domain` (Task 6)
- Produces: `JobBoardSource` Protocol with `search(role_terms: Sequence[str], region: str) -> list[PostingRef]`; `GreenhouseBoardSource(fetcher, tokens)`; `FakeJobBoardSource(postings)`; `SurfaceTarget(source_class, url)`; `surface_targets(domain: str, github_org: str | None) -> list[SurfaceTarget]`

- [ ] **Step 1: Write the failing test**

```python
# tests/sources/test_greenhouse.py
import httpx
from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource

BOARD = {
    "jobs": [
        {"title": "Senior Backend Engineer, Payments",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
         "location": {"name": "Chicago, IL"},
         "company_name": "Acme"},
        {"title": "Product Designer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
         "location": {"name": "Chicago, IL"},
         "company_name": "Acme"},
        {"title": "Backend Engineer",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/3",
         "location": {"name": "London, UK"},
         "company_name": "Acme"},
    ]
}


def build():
    def handler(request):
        if request.url.path.endswith("robots.txt"):
            return httpx.Response(404)
        if "acme" in str(request.url):
            return httpx.Response(200, json=BOARD)
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("acme",))


def test_matching_titles_are_returned_and_others_dropped():
    postings = build().search(["backend engineer"], region="US")
    titles = [p.title for p in postings]
    assert "Senior Backend Engineer, Payments" in titles
    assert "Product Designer" not in titles


def test_region_filter_excludes_non_us_locations():
    postings = build().search(["backend engineer"], region="US")
    assert all("London" not in p.location for p in postings)


def test_unknown_token_yields_no_postings_and_does_not_raise():
    def handler(request):
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    source = GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ("ghost",))
    assert source.search(["backend engineer"], region="US") == []
```

```python
# tests/sources/test_surfaces.py
from outreach.sources.surfaces.standard import surface_targets
from outreach.types import SourceClass


def test_targets_cover_every_expected_surface():
    targets = surface_targets("acme.example", github_org="acme")
    classes = {t.source_class for t in targets}
    assert classes == {
        SourceClass.CAREERS_PAGE, SourceClass.ENG_BLOG, SourceClass.CHANGELOG,
        SourceClass.GITHUB, SourceClass.STATUS_PAGE,
    }


def test_github_surface_is_omitted_when_no_org_known():
    targets = surface_targets("acme.example", github_org=None)
    assert all(t.source_class is not SourceClass.GITHUB for t in targets)


def test_all_urls_are_absolute_and_https():
    for target in surface_targets("acme.example", github_org="acme"):
        assert target.url.startswith("https://")
```

```python
# tests/contract/test_source_contracts.py
"""Every JobBoardSource implementation satisfies the same contract."""
import httpx
import pytest

from outreach.net.fetcher import Fetcher
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
from outreach.types import PostingRef


def empty_greenhouse():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    return GreenhouseBoardSource(Fetcher(client, sleep=lambda s: None), ())


@pytest.fixture(params=[lambda: FakeJobBoardSource([]), empty_greenhouse])
def source(request):
    return request.param()


def test_search_returns_a_list_of_posting_refs(source):
    result = source.search(["backend engineer"], region="US")
    assert isinstance(result, list)
    assert all(isinstance(p, PostingRef) for p in result)


def test_search_with_no_terms_does_not_raise(source):
    assert source.search([], region="US") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/sources/ tests/contract/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.sources'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/sources/base.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from outreach.types import PostingRef, SourceClass


class JobBoardSource(Protocol):
    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]: ...


@dataclass(frozen=True)
class SurfaceTarget:
    source_class: SourceClass
    url: str
```

```python
# src/outreach/sources/jobboards/greenhouse.py
from __future__ import annotations

import json
from typing import Sequence

from outreach.core.dedupe import canonical_domain
from outreach.net.fetcher import Fetcher
from outreach.types import PostingRef

_US_HINTS = ("united states", "usa", ", ca", ", ny", ", il", ", tx", ", wa",
             ", ma", ", co", "remote - us", "remote (us")


class GreenhouseBoardSource:
    """Public Greenhouse board API, one company token at a time."""

    def __init__(self, fetcher: Fetcher, tokens: Sequence[str]) -> None:
        self.fetcher = fetcher
        self.tokens = tuple(tokens)

    def _matches_region(self, location: str, region: str) -> bool:
        if region.upper() != "US":
            return True
        lowered = location.lower()
        return any(hint in lowered for hint in _US_HINTS)

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        terms = [t.lower() for t in role_terms]
        found: list[PostingRef] = []

        for token in self.tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            outcome = self.fetcher.get(url)
            if outcome.outcome != "ok" or not outcome.body:
                continue
            try:
                payload = json.loads(outcome.body)
            except json.JSONDecodeError:
                continue

            for job in payload.get("jobs", []):
                title = job.get("title", "")
                location = (job.get("location") or {}).get("name", "")
                if not any(term in title.lower() for term in terms):
                    continue
                if not self._matches_region(location, region):
                    continue
                found.append(PostingRef(
                    company_name=job.get("company_name") or token,
                    company_domain=canonical_domain(f"{token}.com"),
                    title=title,
                    url=job.get("absolute_url", url),
                    location=location,
                ))
        return found
```

```python
# src/outreach/sources/jobboards/fake.py
from __future__ import annotations

from typing import Sequence

from outreach.types import PostingRef


class FakeJobBoardSource:
    def __init__(self, postings: list[PostingRef]) -> None:
        self._postings = postings

    def search(self, role_terms: Sequence[str], region: str) -> list[PostingRef]:
        if not role_terms:
            return []
        return list(self._postings)
```

```python
# src/outreach/sources/surfaces/standard.py
from __future__ import annotations

from outreach.sources.base import SurfaceTarget
from outreach.types import SourceClass


def surface_targets(domain: str, github_org: str | None) -> list[SurfaceTarget]:
    """The fixed set of public surfaces worth checking for every company."""
    targets = [
        SurfaceTarget(SourceClass.CAREERS_PAGE, f"https://{domain}/careers"),
        SurfaceTarget(SourceClass.ENG_BLOG, f"https://{domain}/blog"),
        SurfaceTarget(SourceClass.CHANGELOG, f"https://{domain}/changelog"),
        SurfaceTarget(SourceClass.STATUS_PAGE, f"https://status.{domain}"),
    ]
    if github_org:
        targets.append(SurfaceTarget(
            SourceClass.GITHUB,
            f"https://api.github.com/orgs/{github_org}/events/public"))
    return targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/sources/ tests/contract/ -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/sources/ tests/sources/ tests/contract/
git commit -m "feat: job board and surface source adapters behind protocols"
```

---

### Task 12: Contact provider with quota handling

**Files:**
- Create: `src/outreach/contacts/__init__.py`, `src/outreach/contacts/base.py`, `src/outreach/contacts/fake.py`, `src/outreach/contacts/quota.py`
- Test: `tests/contacts/test_quota.py`, `tests/contacts/test_provider_contract.py`

**Interfaces:**
- Consumes: `PersonRef` (Task 2)
- Produces: `ContactProvider` Protocol with `find(domain, role_keywords) -> list[PersonRef]`, `verify(email) -> str` (an `EmailStatus`), `remaining_credits() -> int`; `FakeContactProvider(people_by_domain, credits)`; `allocate_quota(company_ids: Sequence[int], remaining: int) -> QuotaPlan(process: list[int], skipped: list[int])`

- [ ] **Step 1: Write the failing test**

```python
# tests/contacts/test_quota.py
from outreach.contacts.quota import allocate_quota


def test_all_companies_processed_when_credits_are_sufficient():
    plan = allocate_quota([1, 2, 3], remaining=10)
    assert plan.process == [1, 2, 3]
    assert plan.skipped == []


def test_shortfall_processes_in_rank_order_and_skips_the_rest():
    """Companies arrive already sorted by rank. A shortfall must never
    truncate silently or raise — the remainder is reported as skipped."""
    plan = allocate_quota([1, 2, 3, 4, 5], remaining=2)
    assert plan.process == [1, 2]
    assert plan.skipped == [3, 4, 5]


def test_zero_credits_skips_everything_without_raising():
    plan = allocate_quota([1, 2], remaining=0)
    assert plan.process == []
    assert plan.skipped == [1, 2]


def test_negative_credits_are_treated_as_zero():
    plan = allocate_quota([1], remaining=-5)
    assert plan.process == []
    assert plan.skipped == [1]


def test_empty_queue_is_a_valid_plan():
    plan = allocate_quota([], remaining=5)
    assert plan.process == [] and plan.skipped == []
```

```python
# tests/contacts/test_provider_contract.py
"""Every ContactProvider implementation satisfies the same contract."""
import pytest

from outreach.contacts.fake import FakeContactProvider
from outreach.types import PersonRef


@pytest.fixture
def provider():
    return FakeContactProvider(
        people_by_domain={"acme.example": [
            PersonRef("A B", "CTO", None, "a@acme.example", "verified"),
        ]},
        credits=5,
    )


def test_find_returns_person_refs(provider):
    people = provider.find("acme.example", ["backend"])
    assert all(isinstance(p, PersonRef) for p in people)


def test_unknown_domain_returns_empty_list(provider):
    assert provider.find("nobody.example", ["backend"]) == []


def test_remaining_credits_is_a_non_negative_int(provider):
    assert isinstance(provider.remaining_credits(), int)
    assert provider.remaining_credits() >= 0


def test_verify_returns_a_known_status(provider):
    assert provider.verify("a@acme.example") in {"verified", "unverified", "not_found"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/contacts/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.contacts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/contacts/base.py
from __future__ import annotations

from typing import Protocol, Sequence

from outreach.types import EmailStatus, PersonRef


class ContactProvider(Protocol):
    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]: ...

    def verify(self, email: str) -> EmailStatus: ...

    def remaining_credits(self) -> int: ...
```

```python
# src/outreach/contacts/quota.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class QuotaPlan:
    process: list[int]
    skipped: list[int]


def allocate_quota(company_ids: Sequence[int], remaining: int) -> QuotaPlan:
    """Split a rank-ordered queue against the credits actually available.

    Running out of credits is a normal condition, not an error: the remainder
    is reported so the report can say `skipped — quota` rather than going quiet.
    """
    budget = max(0, remaining)
    ordered = list(company_ids)
    return QuotaPlan(process=ordered[:budget], skipped=ordered[budget:])
```

```python
# src/outreach/contacts/fake.py
from __future__ import annotations

from typing import Sequence

from outreach.types import EmailStatus, PersonRef


class FakeContactProvider:
    def __init__(self, people_by_domain: dict[str, list[PersonRef]], credits: int) -> None:
        self._people = people_by_domain
        self._credits = credits
        self.find_calls: list[str] = []

    def find(self, domain: str, role_keywords: Sequence[str]) -> list[PersonRef]:
        self.find_calls.append(domain)
        return list(self._people.get(domain, []))

    def verify(self, email: str) -> EmailStatus:
        return "verified" if "@" in email else "not_found"

    def remaining_credits(self) -> int:
        return self._credits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/contacts/ -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/contacts/ tests/contacts/
git commit -m "feat: contact provider protocol and quota allocation"
```

---

### Task 13: Pipeline stages and per-company isolation

**Files:**
- Create: `src/outreach/pipeline/__init__.py`, `src/outreach/pipeline/context.py`, `src/outreach/pipeline/runner.py`
- Test: `tests/pipeline/test_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 1–12
- Produces: `RunContext` (holds repos, config, llm, job board, contact provider, fetcher, cache, `today`); `run_pipeline(ctx, role_title, sector, resume_run_id=None) -> RunSummary`; `RunSummary(run_id, role_title, sector, companies, evidenced, no_bottleneck, quotes_accepted, quotes_rejected, skipped_quota, failures, errors)`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_runner.py
from datetime import date

from outreach.pipeline.runner import run_pipeline
from tests.pipeline.factories import build_context  # helper defined below


def test_happy_path_produces_an_evidenced_company():
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.companies == 2
    assert summary.evidenced == 1
    assert summary.no_bottleneck == 1


def test_one_company_failing_does_not_abort_the_run():
    ctx = build_context(explode_on_domain="bad.example")
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.failures == 1
    assert summary.evidenced == 1  # the healthy company still finished


def test_quota_shortfall_marks_companies_skipped_not_missing():
    ctx = build_context(credits=1)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.skipped_quota == 1
    assert summary.companies == 2


def test_rejected_quotes_are_counted_in_the_summary():
    ctx = build_context(include_invented_quote=True)
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    assert summary.quotes_rejected >= 1


def test_rerunning_the_same_run_id_skips_completed_stages():
    ctx = build_context()
    first = run_pipeline(ctx, "Backend Engineer", "fintech")
    calls_before = len(ctx.contact_provider.find_calls)
    run_pipeline(ctx, "Backend Engineer", "fintech", resume_run_id=first.run_id)
    assert len(ctx.contact_provider.find_calls) == calls_before
```

Also create the factory the tests import:

```python
# tests/pipeline/factories.py
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import httpx

from outreach.config import (Config, DiscoveryConfig, GateConfig, PathsConfig,
                             RankingConfig)
from outreach.contacts.fake import FakeContactProvider
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.sources.jobboards.fake import FakeJobBoardSource
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import (BottleneckRepo, EvidenceRepo, FetchAttemptRepo,
                                 RunRepo)
from outreach.types import PersonRef, PostingRef

TODAY = date(2026, 9, 21)

GOOD_BLOG = ("<html><body><p>Our nightly reconciliation job now regularly "
             "exceeds its 6-hour window.</p></body></html>")
GOOD_CHANGELOG = ("<html><body><p>recon-worker: increase lock timeout to 900s "
                  "(temporary)</p></body></html>")
THIN_PAGE = "<html><body><p>We build payments software.</p></body></html>"

CONFIG = Config(
    gate=GateConfig(2, True, 180, frozenset({
        "job_posting", "careers_page", "eng_blog", "changelog", "github",
        "status_page"})),
    ranking=RankingConfig(3, ("recruit", "talent", "sourcer")),
    discovery=DiscoveryConfig(20, 1000, "US", ()),
    paths=PathsConfig(Path("db"), Path("cache"), Path("reports")),
)


def _transport(explode_on_domain: str | None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("robots.txt"):
            return httpx.Response(404)
        if explode_on_domain and explode_on_domain in url:
            raise httpx.ConnectError("boom", request=request)
        if "good.example/blog" in url:
            return httpx.Response(200, text=GOOD_BLOG)
        if "good.example/changelog" in url:
            return httpx.Response(200, text=GOOD_CHANGELOG)
        if "thin.example" in url:
            return httpx.Response(200, text=THIN_PAGE)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def build_context(
    credits: int = 10,
    explode_on_domain: str | None = None,
    include_invented_quote: bool = False,
) -> RunContext:
    root = Path(tempfile.mkdtemp())
    conn = connect(root / "t.db")

    claims = [
        RawClaim("reconciliation exceeds its window",
                 "Our nightly reconciliation job now regularly exceeds its 6-hour window.",
                 "reconciliation-throughput"),
        RawClaim("lock timeout raised as a stopgap",
                 "recon-worker: increase lock timeout to 900s (temporary)",
                 "reconciliation-throughput"),
    ]
    if include_invented_quote:
        claims.append(RawClaim("they are moving to Kubernetes",
                               "We are migrating everything to Kubernetes.",
                               "infra-migration"))

    postings = [
        PostingRef("Good Co", "good.example", "Backend Engineer", "u1", "Chicago, IL"),
        PostingRef("Thin Co", "thin.example", "Backend Engineer", "u2", "Austin, TX"),
    ]
    if explode_on_domain:
        postings.append(
            PostingRef("Bad Co", explode_on_domain, "Backend Engineer", "u3", "NYC, NY"))

    return RunContext(
        config=CONFIG, today=TODAY,
        companies=CompanyRepo(conn), contacts=ContactRepo(conn),
        documents=DocumentRepo(conn), runs=RunRepo(conn), evidence=EvidenceRepo(conn),
        bottlenecks=BottleneckRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        cache=DocumentCache(root / "cache"),
        fetcher=Fetcher(httpx.Client(transport=_transport(explode_on_domain)),
                        sleep=lambda seconds: None),
        llm=FakeLLM(claims=claims, titles=["backend engineer"]),
        job_board=FakeJobBoardSource(postings),
        contact_provider=FakeContactProvider(
            people_by_domain={
                "good.example": [
                    PersonRef("Marisol Okonkwo", "Co-founder & CTO", None,
                              "m@good.example", "verified"),
                    PersonRef("Rita Sourcer", "Technical Recruiter", None, None,
                              "not_found"),
                ],
                "thin.example": [
                    PersonRef("Tomas Reyes", "Head of Engineering", None,
                              "t@thin.example", "verified"),
                ],
            },
            credits=credits,
        ),
    )
```

Note for the implementer: `FakeLLM.extract_claims` returns the same claims for every document, so the `good.example` blog and changelog each yield two claims — two independent first-party source classes, which passes the gate. `thin.example` serves a page containing none of those quotes, so every claim is rejected by the substring guard and it lands in the no-bottleneck branch. That is what makes `test_happy_path_produces_an_evidenced_company` assert 1 and 1.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pipeline/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.pipeline'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/pipeline/runner.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from outreach.contacts.quota import allocate_quota
from outreach.core.clustering import cluster_by_theme
from outreach.core.dedupe import dedupe_postings
from outreach.core.gate import evaluate
from outreach.core.ranking import rank_contacts
from outreach.extraction.extract import extract_and_persist
from outreach.pipeline.context import RunContext
from outreach.sources.surfaces.standard import surface_targets
from outreach.types import Bottleneck, FetchAttempt, PersonRef, SourceDocument

STAGES = ("discover", "profile", "contacts", "evidence", "synthesize")


@dataclass
class RunSummary:
    run_id: int
    role_title: str = ""
    sector: str = ""
    companies: int = 0
    evidenced: int = 0
    no_bottleneck: int = 0
    quotes_accepted: int = 0
    quotes_rejected: int = 0
    skipped_quota: int = 0
    failures: int = 0
    errors: list[str] = field(default_factory=list)


def run_pipeline(
    ctx: RunContext, role_title: str, sector: str, resume_run_id: int | None = None
) -> RunSummary:
    """Six stages, per-company isolation. One bad domain never costs the others."""
    run_id = resume_run_id or ctx.runs.create(
        role_title, sector, ctx.config.discovery.region,
        (ctx.config.discovery.headcount_min, ctx.config.discovery.headcount_max),
        datetime.now(),
    )
    summary = RunSummary(run_id=run_id, role_title=role_title, sector=sector)

    # --- discover -------------------------------------------------------
    terms = ctx.llm.expand_titles(role_title)
    postings = ctx.job_board.search(terms, ctx.config.discovery.region)
    company_ids: list[int] = []
    for domain, group in dedupe_postings(postings).items():
        company_id = ctx.companies.upsert(domain, group[0].company_name, None, None)
        ctx.runs.set_stage(run_id, company_id, "discover", "ok")
        company_ids.append(company_id)
    summary.companies = len(company_ids)

    # --- profile + evidence (per company, isolated) ---------------------
    for company_id in company_ids:
        if ctx.runs.stage_status(run_id, company_id, "evidence") == "ok":
            continue
        try:
            _profile_and_extract(ctx, run_id, company_id, summary)
            ctx.runs.set_stage(run_id, company_id, "evidence", "ok")
        except Exception as exc:  # one company's failure is data, not a crash
            summary.failures += 1
            summary.errors.append(f"{company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "evidence", "failed", error=str(exc))

    # --- contacts (quota-aware) -----------------------------------------
    pending = [c for c in company_ids
               if ctx.runs.stage_status(run_id, c, "contacts") != "ok"]
    plan = allocate_quota(pending, ctx.contact_provider.remaining_credits())
    for company_id in plan.skipped:
        ctx.runs.set_stage(run_id, company_id, "contacts", "skipped_quota")
        summary.skipped_quota += 1
    for company_id in plan.process:
        try:
            _resolve_contacts(ctx, run_id, company_id, role_title)
            ctx.runs.set_stage(run_id, company_id, "contacts", "ok")
        except Exception as exc:
            summary.failures += 1
            summary.errors.append(f"{company_id}: {exc}")
            ctx.runs.set_stage(run_id, company_id, "contacts", "failed", error=str(exc))

    # --- gate + synthesize ----------------------------------------------
    for company_id in company_ids:
        items = ctx.evidence.for_company(run_id, company_id)
        best = None
        for theme, cluster in cluster_by_theme(items).items():
            verdict = evaluate(cluster, ctx.config.gate, ctx.today)
            if verdict.passed and (best is None or len(cluster) > len(best[1])):
                best = (verdict, cluster, theme)

        if best is None:
            reason = evaluate(items, ctx.config.gate, ctx.today).reason
            ctx.bottlenecks.insert(run_id, Bottleneck(
                None, company_id, "", "", False, reason, ()))
            summary.no_bottleneck += 1
            continue

        verdict, cluster, theme = best
        quotes = [i.quote for i in cluster]
        ctx.bottlenecks.insert(run_id, Bottleneck(
            None, company_id, cluster[0].claim,
            ctx.llm.write_summary(cluster[0].claim, quotes),
            True, verdict.reason, verdict.evidence_ids))
        summary.evidenced += 1
        ctx.runs.set_stage(run_id, company_id, "synthesize", "ok")

    ctx.runs.finish(run_id, datetime.now())
    return summary


def _profile_and_extract(ctx: RunContext, run_id: int, company_id: int,
                         summary: RunSummary) -> None:
    company = ctx.companies.get(company_id)
    for target in surface_targets(company.canonical_domain, github_org=None):
        outcome = ctx.fetcher.get(target.url)
        if outcome.outcome != "ok" or not outcome.body:
            ctx.fetch_attempts.insert(run_id, FetchAttempt(
                company_id, target.source_class, target.url,
                outcome.outcome, outcome.status, 0))
            continue

        content_hash = ctx.cache.store(outcome.body.encode("utf-8"))
        doc = SourceDocument(None, company_id, target.url, target.source_class,
                             company.canonical_domain, ctx.today, datetime.now(),
                             outcome.status or 200, content_hash)
        doc_id = ctx.documents.insert(doc)
        ctx.fetch_attempts.insert(run_id, FetchAttempt(
            company_id, target.source_class, target.url, "ok", outcome.status, 1))

        result = extract_and_persist(run_id, doc, outcome.body, ctx.llm,
                                     ctx.evidence, document_id=doc_id)
        summary.quotes_accepted += result.accepted
        summary.quotes_rejected += result.rejected


def _resolve_contacts(ctx: RunContext, run_id: int, company_id: int,
                      role_title: str) -> None:
    company = ctx.companies.get(company_id)
    keywords = role_title.lower().split()
    people = ctx.contact_provider.find(company.canonical_domain, keywords)
    for person, _score in rank_contacts(people, company.headcount, keywords,
                                        ctx.config.ranking):
        status = person.email_status
        if person.email and status != "verified":
            status = ctx.contact_provider.verify(person.email)
        resolved = PersonRef(
            full_name=person.full_name, title=person.title,
            profile_url=person.profile_url,
            email=person.email if status == "verified" else None,
            email_status=status,
        )
        ctx.contacts.upsert(company_id, resolved, "provider", datetime.now())
```

```python
# src/outreach/pipeline/context.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from outreach.config import Config
from outreach.contacts.base import ContactProvider
from outreach.llm.base import LLMClient
from outreach.net.fetcher import Fetcher
from outreach.sources.base import JobBoardSource
from outreach.store.cache import DocumentCache
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import BottleneckRepo, EvidenceRepo, FetchAttemptRepo, RunRepo


@dataclass
class RunContext:
    config: Config
    today: date
    companies: CompanyRepo
    contacts: ContactRepo
    documents: DocumentRepo
    runs: RunRepo
    evidence: EvidenceRepo
    bottlenecks: BottleneckRepo
    fetch_attempts: FetchAttemptRepo
    cache: DocumentCache
    fetcher: Fetcher
    llm: LLMClient
    job_board: JobBoardSource
    contact_provider: ContactProvider
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/pipeline/ -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/pipeline/ tests/pipeline/
git commit -m "feat: pipeline runner with per-company isolation and quota awareness"
```

---

### Task 14: Report renderer

**Files:**
- Create: `src/outreach/render/__init__.py`, `src/outreach/render/report.py`, `src/outreach/render/templates/report.html.j2`
- Test: `tests/render/test_report.py`

**Interfaces:**
- Consumes: repos (Task 8), `RunSummary` (Task 13)
- Produces: `ReportCompany` view model; `build_view_model(ctx, run_id, summary) -> ReportView`; `render_report(view) -> str`; `write_report(view, directory: Path) -> Path`

Copy the committed sample at `docs/superpowers/specs/2026-09-21-sample-run-report.html` into the Jinja template and replace its literal content with loops. The CSS, the quote-forward hierarchy, the badge styles and the diagnostics footer are already designed there — do not redesign them.

- [ ] **Step 1: Write the failing test**

```python
# tests/render/test_report.py
from datetime import date
from pathlib import Path

from outreach.render.report import ReportCompany, ReportEvidence, ReportView, render_report


def view(**overrides) -> ReportView:
    base = dict(
        role_title="Backend Engineer", sector="fintech", run_date=date(2026, 9, 21),
        headcount_min=20, headcount_max=1000,
        evidenced=[ReportCompany(
            name="Ledgerline", domain="ledgerline.example", headcount=48,
            headcount_source="careers page", claim="recon overruns its window",
            summary="two first-party sources agree", reason="passed",
            evidence=[ReportEvidence(
                quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
                source_class="eng_blog", url="https://ledgerline.example/blog/1",
                published_at=date(2026, 9, 2))],
            contacts=[], fetch_log=[])],
        no_bottleneck=[], diagnostics={"Companies discovered": "2"},
    )
    base.update(overrides)
    return ReportView(**base)


def test_verbatim_quote_appears_in_the_output():
    html = render_report(view())
    assert "Our nightly reconciliation job now regularly exceeds its 6-hour window." in html


def test_evidenced_section_precedes_no_bottleneck_section():
    html = render_report(view())
    assert html.index("Evidenced bottlenecks") < html.index("No bottleneck found")


def test_gate_failure_reason_is_shown_for_no_bottleneck_companies():
    v = view(evidenced=[], no_bottleneck=[ReportCompany(
        name="Northsight", domain="northsight.example", headcount=310,
        headcount_source="band", claim="", summary="",
        reason="insufficient_independent_sources", evidence=[], contacts=[],
        fetch_log=[("eng_blog", "http_error", 404)])])
    html = render_report(v)
    assert "insufficient_independent_sources" in html or "independent source" in html
    assert "404" in html


def test_unverified_contact_never_renders_an_email_address(tmp_path):
    from outreach.render.report import ReportContact
    v = view()
    v.evidenced[0].contacts = [ReportContact(
        full_name="Deepak Raman", title="Eng Lead", email=None,
        email_status="unverified", profile_url="https://p/1", why="tier 3")]
    html = render_report(v)
    assert "Deepak Raman" in html
    assert "@" not in html.split("Deepak Raman")[1].split("</div>")[0]


def test_diagnostics_footer_is_rendered():
    html = render_report(view())
    assert "Companies discovered" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/render/ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.render'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/render/report.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape


@dataclass
class ReportEvidence:
    quote: str
    source_class: str
    url: str
    published_at: date | None


@dataclass
class ReportContact:
    full_name: str
    title: str
    email: str | None
    email_status: str
    profile_url: str | None
    why: str


@dataclass
class ReportCompany:
    name: str
    domain: str
    headcount: int | None
    headcount_source: str | None
    claim: str
    summary: str
    reason: str
    evidence: list[ReportEvidence] = field(default_factory=list)
    contacts: list[ReportContact] = field(default_factory=list)
    fetch_log: list[tuple[str, str, int | None]] = field(default_factory=list)


@dataclass
class ReportView:
    role_title: str
    sector: str
    run_date: date
    headcount_min: int
    headcount_max: int
    evidenced: list[ReportCompany]
    no_bottleneck: list[ReportCompany]
    diagnostics: dict[str, str]


_env = Environment(
    loader=PackageLoader("outreach.render", "templates"),
    autoescape=select_autoescape(["html"]),
)

REASON_TEXT = {
    "no_evidence": "no claims survived extraction",
    "insufficient_independent_sources": "fewer than two independent sources",
    "no_first_party_source": "no first-party source",
    "all_evidence_stale": "nothing inside the recency window",
}


def render_report(view: ReportView) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(view=view, reason_text=REASON_TEXT)


def write_report(view: ReportView, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    slug = f"{view.run_date.isoformat()}-{view.role_title}-{view.sector}"
    slug = slug.lower().replace(" ", "-").replace(",", "")
    path = directory / f"{slug}.html"
    path.write_text(render_report(view), encoding="utf-8")
    return path
```

Build `report.html.j2` by copying the committed sample at `docs/superpowers/specs/2026-09-21-sample-run-report.html`, keeping its `<head>` and `<style>` block **verbatim** — the CSS, quote-forward hierarchy, badges and footer grid are already designed, do not redesign them — dropping the `SAMPLE LAYOUT` banner, and replacing the body's hardcoded cards with these loops:

```jinja
{# src/outreach/render/templates/report.html.j2 — body only; head and style copied verbatim from the sample #}
<div class="wrap">
<header class="run">
  <h1>{{ view.role_title }} · {{ view.sector }}</h1>
  <div class="run-meta">
    <span>{{ view.run_date.strftime('%d %b %Y') }}</span>
    <span>headcount {{ view.headcount_min }}–{{ view.headcount_max }}</span>
    <span>{{ view.evidenced|length + view.no_bottleneck|length }} companies</span>
    <span>{{ view.evidenced|length }} evidenced</span>
    <span>{{ view.no_bottleneck|length }} no bottleneck</span>
  </div>
</header>

{% macro contacts_block(company) %}
  {% if company.contacts %}
  <div class="contacts">
    <h3>Contacts</h3>
    {% for c in company.contacts %}
    <div class="person">
      <div class="p-main">
        <div class="p-name">{{ c.full_name }}</div>
        <div class="p-title">{{ c.title }}</div>
        <div class="p-why">{{ c.why }}</div>
      </div>
      {% if c.email_status == 'verified' and c.email %}
        <div class="p-mail ok">{{ c.email }}</div>
      {% else %}
        <div class="p-mail none">no verified address{% if c.profile_url %} —
          <a href="{{ c.profile_url }}">profile</a>{% endif %}</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  {% endif %}
{% endmacro %}

<h2 class="section">Evidenced bottlenecks</h2>
{% for company in view.evidenced %}
<article class="card">
  <div class="card-head">
    <span class="co-name">{{ company.name }}</span>
    <span class="co-domain">{{ company.domain }}</span>
  </div>
  <div class="co-facts">
    {% if company.headcount %}~{{ company.headcount }} employees
      <span class="src">({{ company.headcount_source }})</span>
    {% else %}headcount unknown{% endif %}
  </div>
  <p class="claim">{{ company.claim }}</p>
  <p class="summary">{{ company.summary }}</p>
  {% for e in company.evidence %}
  <div class="ev">
    <blockquote>{{ e.quote }}</blockquote>
    <div class="ev-meta">
      <span class="badge{% if e.source_class == 'news' %} third{% endif %}">
        {{ e.source_class.replace('_', ' ') }}</span>
      <span>{% if e.published_at %}{{ e.published_at.strftime('%d %b %Y') }}
            {% else %}undated{% endif %}</span>
      <a href="{{ e.url }}">{{ e.url }}</a>
    </div>
  </div>
  {% endfor %}
  {{ contacts_block(company) }}
</article>
{% endfor %}

<h2 class="section">No bottleneck found</h2>
{% for company in view.no_bottleneck %}
<article class="card">
  <div class="card-head">
    <span class="co-name">{{ company.name }}</span>
    <span class="co-domain">{{ company.domain }}</span>
  </div>
  <div class="co-facts">
    {% if company.headcount %}~{{ company.headcount }} employees
      <span class="src">({{ company.headcount_source }})</span>
    {% else %}headcount unknown{% endif %}
  </div>
  <div class="fetchlog">
    {% for source_class, outcome, status in company.fetch_log %}
    <span class="chip {% if outcome == 'ok' %}ok{% else %}err{% endif %}">
      {{ source_class.replace('_', ' ') }} · {{ outcome }}{% if status %} {{ status }}{% endif %}
    </span>
    {% endfor %}
  </div>
  <p class="why-none"><strong>Gate failed:</strong>
    {{ reason_text.get(company.reason, company.reason) }}
    ({{ company.reason }}).</p>
  {{ contacts_block(company) }}
</article>
{% endfor %}

<footer class="diag">
  <h2>Run diagnostics</h2>
  <div class="diag-grid">
    {% for key, value in view.diagnostics.items() %}
    <div class="diag-item"><div class="k">{{ key }}</div><div class="v">{{ value }}</div></div>
    {% endfor %}
  </div>
</footer>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/render/ -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/outreach/render/ tests/render/
git commit -m "feat: quote-forward HTML report renderer"
```

---

### Task 15: CLI, real adapters, and end-to-end test

**Files:**
- Create: `src/outreach/cli.py`, `src/outreach/llm/anthropic_client.py`, `src/outreach/contacts/hunter.py`, `README.md`
- Test: `tests/test_cli.py`, `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: everything
- Produces: CLI commands `outreach run --role --sector [--dry-run] [--resume RUN_ID]` and `outreach report RUN_ID`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
import pytest
from typer.testing import CliRunner

from outreach.cli import app

runner = CliRunner()


def test_missing_api_key_aborts_before_any_network_call(monkeypatch, tmp_path):
    """Fail fast: a broken run must cost zero credits."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
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
```

```python
# tests/test_end_to_end.py
"""Whole pipeline, every adapter faked, asserting a real report is produced."""
from tests.pipeline.factories import build_context
from outreach.pipeline.runner import run_pipeline
from outreach.render.report import write_report
from outreach.cli import build_view


def test_run_produces_a_report_file_with_evidence_and_failures(tmp_path):
    ctx = build_context()
    summary = run_pipeline(ctx, "Backend Engineer", "fintech")
    view = build_view(ctx, summary)
    path = write_report(view, tmp_path)

    html = path.read_text()
    assert path.exists()
    assert "Evidenced bottlenecks" in html
    assert "No bottleneck found" in html
    assert "Run diagnostics" in html
    assert str(summary.quotes_rejected) in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py tests/test_end_to_end.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'outreach.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/outreach/cli.py
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import ConfigError, load_config, require_env
from outreach.core.ranking import score_title
from outreach.net.fetcher import Fetcher
from outreach.pipeline.context import RunContext
from outreach.pipeline.runner import RunSummary, run_pipeline
from outreach.render.report import (ReportCompany, ReportContact, ReportEvidence,
                                    ReportView, write_report)
from outreach.store.cache import DocumentCache
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import BottleneckRepo, EvidenceRepo, FetchAttemptRepo, RunRepo

app = typer.Typer(help="Outreach research pipeline")


def build_context(config_path: Path = Path("config.toml")) -> RunContext:
    load_dotenv()
    config = load_config(config_path)

    use_fakes = os.environ.get("OUTREACH_FAKE_ADAPTERS") == "1"
    if use_fakes:
        from outreach.contacts.fake import FakeContactProvider
        from outreach.llm.fake import FakeLLM
        from outreach.sources.jobboards.fake import FakeJobBoardSource
        llm, job_board = FakeLLM(), FakeJobBoardSource([])
        provider = FakeContactProvider({}, credits=0)
    else:
        from outreach.contacts.hunter import HunterProvider
        from outreach.llm.anthropic_client import AnthropicLLM
        llm = AnthropicLLM(require_env("ANTHROPIC_API_KEY"))
        provider = HunterProvider(require_env("HUNTER_API_KEY"))
        job_board = None  # set below, needs the fetcher

    conn = connect(config.paths.db)
    fetcher = Fetcher(httpx.Client())
    if not use_fakes:
        from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
        job_board = GreenhouseBoardSource(fetcher, config.discovery.greenhouse_tokens)

    return RunContext(
        config=config, today=date.today(),
        companies=CompanyRepo(conn), contacts=ContactRepo(conn),
        documents=DocumentRepo(conn), runs=RunRepo(conn), evidence=EvidenceRepo(conn),
        bottlenecks=BottleneckRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        cache=DocumentCache(config.paths.cache), fetcher=fetcher,
        llm=llm, job_board=job_board, contact_provider=provider,
    )


def build_view(ctx: RunContext, summary: RunSummary) -> ReportView:
    """Assemble the report view model from persisted rows."""
    run_id = summary.run_id
    evidenced: list[ReportCompany] = []
    no_bottleneck: list[ReportCompany] = []

    for bottleneck in ctx.bottlenecks.for_run(run_id):
        company = ctx.companies.get(bottleneck.company_id)
        keep = set(bottleneck.evidence_ids)
        urls = {d.id: d.url for d in ctx.documents.for_company(bottleneck.company_id)}
        evidence = [
            ReportEvidence(item.quote, item.source_class.value,
                           urls.get(item.source_document_id, ""), item.published_at)
            for item in ctx.evidence.for_company(run_id, bottleneck.company_id)
            if item.id in keep
        ]
        keywords = summary.role_title.lower().split()
        contacts = []
        for c in ctx.contacts.for_company(bottleneck.company_id):
            # The ranking explanation is a pure function of title and headcount,
            # so recompute it here rather than storing a derived string.
            score = score_title(c.title, company.headcount, keywords,
                                ctx.config.ranking)
            why = score.explanation if score else "ranking unavailable"
            if c.contacted_at:
                why += f" · emailed {c.contacted_at:%d %b}"
            contacts.append(ReportContact(c.full_name, c.title, c.email,
                                          c.email_status, c.profile_url, why))
        fetch_log = [
            (a.source_class.value, a.outcome, a.http_status)
            for a in ctx.fetch_attempts.for_company(run_id, bottleneck.company_id)
        ]
        card = ReportCompany(
            name=company.name, domain=company.canonical_domain,
            headcount=company.headcount, headcount_source=company.headcount_source,
            claim=bottleneck.claim, summary=bottleneck.summary,
            reason=bottleneck.reason, evidence=evidence, contacts=contacts,
            fetch_log=fetch_log,
        )
        (evidenced if bottleneck.passed else no_bottleneck).append(card)

    # Smallest and most bypassable first; unknown headcount sorts last.
    evidenced.sort(key=lambda c: (c.headcount is None, c.headcount or 0))
    no_bottleneck.sort(key=lambda c: (c.headcount is None, c.headcount or 0))

    return ReportView(
        role_title=summary.role_title, sector=summary.sector, run_date=ctx.today,
        headcount_min=ctx.config.discovery.headcount_min,
        headcount_max=ctx.config.discovery.headcount_max,
        evidenced=evidenced, no_bottleneck=no_bottleneck,
        diagnostics={
            "Companies discovered": str(summary.companies),
            "Bottlenecks passed": f"{summary.evidenced} / {summary.companies}",
            "Quotes accepted": str(summary.quotes_accepted),
            "Quotes rejected": str(summary.quotes_rejected),
            "Skipped on quota": str(summary.skipped_quota),
            "Company failures": str(summary.failures),
        },
    )


@app.command()
def run(
    role: str = typer.Option(..., "--role"),
    sector: str = typer.Option(..., "--sector"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    resume: int | None = typer.Option(None, "--resume"),
) -> None:
    try:
        ctx = build_context()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)

    if dry_run:
        terms = ctx.llm.expand_titles(role)
        postings = ctx.job_board.search(terms, ctx.config.discovery.region)
        companies = len({p.company_domain for p in postings})
        typer.echo(
            f"Dry run: {companies} companies matched. A full run would use up to "
            f"{companies} enrichment credits; {ctx.contact_provider.remaining_credits()} "
            "remain."
        )
        raise typer.Exit(code=0)

    summary = run_pipeline(ctx, role, sector, resume_run_id=resume)
    path = write_report(build_view(ctx, summary), ctx.config.paths.reports)
    typer.echo(f"Run {summary.run_id}: {summary.evidenced} evidenced, "
               f"{summary.no_bottleneck} without. Report: {path}")
```

Write `AnthropicLLM` implementing the three protocol methods against the Messages API, each requesting strict JSON and retrying once on a parse failure. Write `HunterProvider` implementing `find`, `verify` and `remaining_credits` against Hunter's domain-search, email-verifier and account endpoints. Fill in `build_view`'s body per its docstring — every row it needs already has a repo method. Write `README.md` covering install, `.env` setup, `config.toml` keys, and the two commands.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -v`
Expected: PASS, full suite green

- [ ] **Step 5: Commit**

```bash
git add src/outreach/cli.py src/outreach/llm/anthropic_client.py src/outreach/contacts/hunter.py README.md tests/
git commit -m "feat: CLI, live adapters, and end-to-end test"
```
