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
    # Only HOMEPAGE is still used: FetchAttempt records the homepage check that
    # confirms a guessed domain. The other values only describe rows that
    # earlier versions wrote, which must still load.
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


@dataclass(frozen=True)
class Company:
    id: int | None
    canonical_domain: str
    name: str
    headcount: int | None
    headcount_source: str | None
    # Defaults so positional Company(id, domain, name, hc, src) keeps working.
    headcount_band: str | None = None


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
class Location:
    """The place a run looks in; the default is the whole US, as every run was before.

    Maps straight onto Hunter's `headquarters_location` filter, and is what a
    job posting's location is checked against.
    """
    country: str = "US"  # ISO 3166 alpha-2
    state: str | None = None  # US state code; only with country "US"
    city: str | None = None


@dataclass(frozen=True)
class RunFilters:
    """What a run was narrowed to; stored on the run so a report can say so."""
    sectors: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    location: Location = Location()
    work_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyInfo:
    """What one run knows about one company, beyond the company itself."""
    position: int  # the order the run considered it in
    source: str  # "board" | "hn" | "hunter" | "hunter_hiring"
    sector: str | None = None
    size: str | None = None
    fit: str = "fits"  # "fits" | "unknown" | "no", against the run's filters
    guessed_domain: bool = False


@dataclass(frozen=True)
class Spend:
    """Hunter credits a run used, in tenths of a credit."""
    balance_before: int | None = None
    emails: int = 0
    verifications: int = 0
    spent: int = 0


@dataclass(frozen=True)
class RunInfo:
    """A stored run. Runs recorded before v4 have no kind, people, filters or spend."""
    run_id: int
    kind: str | None
    role: str
    people: str | None
    filters: RunFilters | None
    started_at: datetime
    schema_version: int
    spend: Spend | None


@dataclass(frozen=True)
class EmailedEntry:
    """An address marked as emailed, with whatever a stored contact tells about it."""
    address: str
    emailed_on: date
    full_name: str | None = None
    title: str | None = None
    company: str | None = None


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
    # What the post was read as, and the post itself: a resume rebuilds this
    # run's HN selection from these instead of re-reading a thread that has
    # grown since, or paying the LLM fallback again.
    parsed: ParsedPost | None = None
    html: str = ""
