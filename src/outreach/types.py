from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Literal

EmailStatus = Literal["verified", "unverified", "not_found"]
StageStatus = Literal["pending", "ok", "failed", "skipped_quota",
                      "skipped_domain_unconfirmed"]


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
