from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal

EmailStatus = Literal["verified", "unverified", "not_found"]
StageStatus = Literal["pending", "ok", "failed", "skipped_quota",
                      "skipped_domain_unconfirmed", "excluded_size",
                      "excluded_no_matching_posting", "no_findings",
                      "skipped_cap", "skipped_recent"]
# "unknown" is kept by the work-mode filter: a posting that does not say is not
# evidence against the user's preference.
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
ALL_WORK_MODES: frozenset[WorkMode] = frozenset({"remote", "hybrid", "onsite"})
# Only "other" (contract / part-time / intern / temporary) is ever dropped.
EmploymentType = Literal["full_time", "other", "unknown"]


class SourceClass(str, Enum):
    JOB_POSTING = "job_posting"
    CAREERS_PAGE = "careers_page"
    ENG_BLOG = "eng_blog"
    CHANGELOG = "changelog"
    GITHUB = "github"
    STATUS_PAGE = "status_page"  # legacy: no longer produced, kept so old rows still load
    # The company's own post in HN's "Who is hiring?" thread: first-party in
    # content, published on news.ycombinator.com, dated by the post itself.
    HN_POST = "hn_post"
    NEWS = "news"
    PRESS = "press"
    ABOUT = "about"
    HOMEPAGE = "homepage"
    DEV_DOCS = "dev_docs"


class Stage(str, Enum):
    SEED_STARTUP = "seed_startup"
    GROWTH = "growth"
    EXPANSION = "expansion"
    MATURITY = "maturity"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FundingRound:
    # "pre_seed" | "seed" | "series_a".."series_z" | "ipo" | "acquired"
    kind: str
    announced: date | None


@dataclass(frozen=True)
class CompanyFacts:
    headcount: int | None
    headcount_band: str | None
    founded_year: int | None
    funding_rounds: tuple[FundingRound, ...]
    tags: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Company:
    id: int | None
    canonical_domain: str
    name: str
    headcount: int | None
    headcount_source: str | None
    # Everything below defaults so positional Company(id, domain, name, hc, src) keeps working.
    headcount_band: str | None = None
    founded_year: int | None = None
    funding_rounds: tuple[FundingRound, ...] = ()
    tags: tuple[str, ...] = ()
    github_org: str | None = None
    facts_fetched_at: datetime | None = None


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
class Finding:
    id: int | None
    company_id: int
    theme: str
    claim: str
    summary: str
    corroborated: bool
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
    work_mode: WorkMode = "unknown"
    employment_type: EmploymentType = "unknown"


@dataclass(frozen=True)
class PersonRef:
    full_name: str
    title: str
    profile_url: str | None
    email: str | None
    email_status: EmailStatus


@dataclass(frozen=True)
class HNPost:
    """One top-level post in an HN "Who is hiring?" thread, as HTML."""
    item_id: int
    posted_at: datetime
    html: str


@dataclass(frozen=True)
class HNThread:
    item_id: int
    title: str
    posts: tuple[HNPost, ...]


@dataclass(frozen=True)
class ParsedPost:
    """The hiring company and one job, read out of an HN post.

    `ats` is (board kind, token) -- kind is "greenhouse", "ashby" or "lever" --
    when the post links a public job board, else None.
    """
    company: str
    domain: str
    ats: tuple[str, str] | None
    role: str
    location: str
    work_mode: WorkMode
    employment_type: EmploymentType


@dataclass(frozen=True)
class HNPostRecord:
    """The HN post a company came from in one run, and what became of it."""
    item_id: int
    thread_title: str
    posted_at: date
    parse_method: str  # "parsed" | "llm"
    status: str  # "kept" | "skipped_cap" | "skipped_recent"
