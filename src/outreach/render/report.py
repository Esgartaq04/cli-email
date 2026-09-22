from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, PackageLoader


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
    domain_confirmed: bool = True
    error: str | None = None


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
    errors: list[str] = field(default_factory=list)


_env = Environment(
    loader=PackageLoader("outreach.render", "templates"),
    # Templates live at templates/*.j2 (required by the package-data glob in
    # pyproject.toml), so select_autoescape's extension sniffing would look at
    # the trailing ".j2" and never match "html" — turn autoescaping on
    # unconditionally instead, since every template this env loads is HTML.
    autoescape=True,
)

# Surfaces whose `published_at` is stamped with the fetch date -- an honest
# "true as of today" for a careers/job/status page, not a parsed publication
# date. Rendered identically to a real date, "21 Sep 2026" under a careers-
# page quote reads as "I saw this posted that day," when the page could be
# eighteen months stale. Mirrors runner.py's CURRENT_STATE_CLASSES by value
# rather than importing it, since render/ has no reason to depend on
# pipeline/ for three string constants.
CURRENT_STATE_CLASSES = frozenset({"careers_page", "job_posting", "status_page"})

REASON_TEXT = {
    "no_evidence": "no claims survived extraction",
    "insufficient_independent_sources": "fewer than two independent sources",
    "no_first_party_source": "no first-party source",
    "all_evidence_stale": "nothing inside the recency window",
    "evidence_split_across_themes": "evidence spread across unrelated themes, none corroborated",
    "research_failed": "research could not be completed",
}


def render_report(view: ReportView) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(view=view, reason_text=REASON_TEXT,
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
