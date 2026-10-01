from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from jinja2 import Environment, PackageLoader


@dataclass
class ReportEvidence:
    quote: str
    source_class: str
    url: str
    published_at: date | None


@dataclass
class ReportSource:
    """One page that was read, and how many claims survived from it."""
    url: str
    source_class: str
    published_at: date | None
    claims: int


@dataclass
class ReportTheme:
    """Everything found under one theme, and how far it got through the gate."""
    theme: str
    evidence: list[ReportEvidence]
    source_labels: list[str]
    independent_sources: int
    reason: str


# Display names for `Stage` values. Growth reads "Growth & Establishment"
# because that is the phase this tool exists for: past product-market fit,
# hiring to build, and still small enough that one email reaches a builder.
STAGE_LABELS = {
    "growth": "Growth & Establishment",
    "expansion": "Expansion",
    "seed_startup": "Seed / Startup",
    "maturity": "Maturity",
    "unknown": "Stage unknown",
}


def linkedin_search_url(full_name: str, company_name: str) -> str:
    """A LinkedIn people search for this person at this company.

    Offered when no profile was found, labelled as a search so a reader never
    mistakes it for a confirmed profile of the right person.
    """
    return ("https://www.linkedin.com/search/results/people/?keywords="
            + quote_plus(f"{full_name} {company_name}"))


@dataclass
class ReportContact:
    full_name: str
    title: str
    email: str | None
    email_status: str
    profile_url: str | None
    why: str
    # Set only when `profile_url` is None.
    linkedin_search: str | None = None


@dataclass
class ReportPosting:
    title: str
    url: str
    work_mode: str


@dataclass
class ReportFinding:
    """One theme that cleared the gate: what the company is building."""
    theme: str
    claim: str
    summary: str
    corroborated: bool
    evidence: list[ReportEvidence]


@dataclass
class ReportHook:
    """Something concrete to build against: a recently pushed repo or public docs."""
    kind: Literal["repo", "docs"]
    label: str
    url: str
    when: date | None


@dataclass
class ReportExcluded:
    """A company the run dropped before researching it, and why."""
    name: str
    domain: str
    reason: str


@dataclass
class ReportCompany:
    name: str
    domain: str
    headcount: int | None
    headcount_source: str | None
    # "passed", a gate reason, or "research_failed".
    reason: str
    stage: str = "unknown"
    stage_label: str = STAGE_LABELS["unknown"]
    stage_reasons: list[str] = field(default_factory=list)
    founded_year: int | None = None
    headcount_band: str | None = None
    latest_round: str | None = None
    postings: list[ReportPosting] = field(default_factory=list)
    findings: list[ReportFinding] = field(default_factory=list)
    hooks: list[ReportHook] = field(default_factory=list)
    contacts: list[ReportContact] = field(default_factory=list)
    fetch_log: list[tuple[str, str, int | None]] = field(default_factory=list)
    domain_confirmed: bool = True
    error: str | None = None
    # Only for a company without findings: what was found, and why it fell short.
    themes: list[ReportTheme] = field(default_factory=list)
    sources_read: list[ReportSource] = field(default_factory=list)


@dataclass
class ReportView:
    role_title: str
    sector: str
    run_date: date
    headcount_min: int
    headcount_max: int
    # Companies with findings in a growth or expansion stage: who this is for.
    target_stage: list[ReportCompany]
    # Companies with findings in any other stage, or none known.
    other_stages: list[ReportCompany]
    # Researched, but nothing they are building cleared the gate.
    no_findings: list[ReportCompany]
    excluded: list[ReportExcluded]
    diagnostics: dict[str, str]
    errors: list[str] = field(default_factory=list)
    min_independent_sources: int = 1


_env = Environment(
    loader=PackageLoader("outreach.render", "templates"),
    # Templates live at templates/*.j2 (required by the package-data glob in
    # pyproject.toml), so select_autoescape's extension sniffing would look at
    # the trailing ".j2" and never match "html" — turn autoescaping on
    # unconditionally instead, since every template this env loads is HTML.
    autoescape=True,
)


def is_http_url(url: str | None) -> bool:
    """Whether a third-party URL is safe to put in an href.

    Posting URLs come from job-board APIs and repo URLs from GitHub: data,
    not ours. Autoescaping keeps them from breaking out of the attribute but
    not from being `javascript:...`, so anything that does not start with a
    plain web scheme is rendered as text rather than as a link. Checked on
    the raw string, so leading junk a browser might strip also fails.
    """
    return bool(url) and url.lower().startswith(("http://", "https://"))


_env.tests["http_url"] = is_http_url

# Surfaces whose `published_at` is stamped with the fetch date -- an honest
# "true as of today" for a careers/job/about page, not a parsed publication
# date. Rendered identically to a real date, "21 Sep 2026" under a careers-
# page quote reads as "I saw this posted that day," when the page could be
# eighteen months stale. Mirrors runner.py's CURRENT_STATE_CLASSES by value
# rather than importing it, since render/ has no reason to depend on
# pipeline/ for three string constants.
CURRENT_STATE_CLASSES = frozenset({"careers_page", "job_posting", "about"})

# Why a company has no findings, or was excluded. Values are format strings
# over the view's own thresholds, so the text states the limit this run
# actually applied rather than a number baked in when it was written.
REASON_TEXT = {
    "no_evidence": "no claims survived extraction",
    "insufficient_independent_sources":
        "fewer than {min_independent_sources} independent sources",
    "no_first_party_source": "no first-party source",
    "all_evidence_stale": "nothing inside the recency window",
    "evidence_split_across_themes":
        "evidence spread across unrelated themes, none clearing the gate on its own",
    "research_failed": "research could not be completed",
    "excluded_size": "over {headcount_max:,} employees",
    "excluded_no_matching_posting": "no full-time posting in the requested work mode",
}


def describe_reason(reason: str, view: ReportView) -> str:
    """A reason code in words, with this view's thresholds filled in."""
    text = REASON_TEXT.get(reason)
    if text is None:
        return reason
    return text.format(headcount_max=view.headcount_max,
                       min_independent_sources=view.min_independent_sources)


def render_report(view: ReportView) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(view=view,
                           reason_text=lambda reason: describe_reason(reason, view),
                           current_state_classes=CURRENT_STATE_CLASSES)


def _slugify(text: str) -> str:
    """Collapse anything that isn't filesystem-safe (slashes, ampersands,
    commas, whitespace, ...) into single hyphens, so the result is always a
    single path segment rather than an accidental subdirectory."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return slug.strip("-")


def write_report(view: ReportView, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    slug = "-".join(
        part
        for part in (view.run_date.isoformat(), _slugify(view.role_title), _slugify(view.sector))
        if part
    )
    path = directory / f"{slug}.html"
    path.write_text(render_report(view), encoding="utf-8")
    return path
