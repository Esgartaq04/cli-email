"""The report a jobs or companies run ends with.

A card for every company whose people were bought, then every other company the
run considered and why it has none: nobody to find, already covered by an
earlier list or an email, or left out. Only addresses Hunter confirmed are ever
shown. The view is built from what the run stored, so `outreach report` can
render it again later; only the run's in-memory summary (the HN funnel and its
errors) is lost, and the page says so.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader

from outreach.core.filters import describe_filters, location_label
from outreach.credits import format_credits
from outreach.render.links import is_http_url, linkedin_search_url, slugify
from outreach.types import Company, CompanyInfo, Contact, RunInfo, Spend

if TYPE_CHECKING:
    from outreach.pipeline.context import RunContext
    from outreach.pipeline.summary import RunSummary

_NOT_RECORDED = "not recorded"
_JOB_RUN_SUFFIX = " · job run"
_COMPANY_RUN_HEADING = "Company run"

# Companies Hunter found by headquarters, so the report can say where they are.
_HQ_SOURCES = ("hunter", "hunter_hiring")
_SOURCE_LABELS = (("board", "job boards"), ("hn", "HN"), ("hunter", "Hunter"),
                  ("hunter_hiring", "Hunter hiring search"))
_NO_ONE_FOUND = ("empty", "no_matching_people")
_EARLIER = ("skipped_earlier_list", "skipped_emailed")
# Left out for a reason that needs nothing from the run to state.
_LEFT_OUT_REASONS = {
    "left_out_filters": "doesn't match your sector or size",
    "excluded_no_matching_posting": "no full-time posting in the requested work mode or location",
    "out_of_credits": "not bought — out of credits",
    "domain_unconfirmed": "its website didn't answer, so its domain couldn't be confirmed",
}


@dataclass
class PersonLine:
    full_name: str
    title: str
    # Only ever an address Hunter confirmed.
    email: str | None
    profile_url: str | None
    # Set only when there is no profile URL.
    linkedin_search: str | None
    emailed_on: date | None


@dataclass
class PostingLine:
    title: str
    url: str
    work_mode: str
    via: str


@dataclass
class CompanyCard:
    name: str
    domain: str
    sector: str | None
    size: str | None
    hq: str | None
    fit_unknown: bool
    postings: list[PostingLine]
    # The role, for a company Hunter's hiring search found: it has no posting to link.
    hiring_per_hunter: str | None
    people: list[PersonLine]


@dataclass
class ListedCompany:
    name: str
    domain: str
    reason: str


@dataclass
class ContactReportView:
    run_id: int
    kind: str
    heading: str
    filters_line: str
    people: str
    run_date: date
    credits_line: str
    with_contacts: list[CompanyCard]
    no_one_found: list[ListedCompany]
    earlier: list[ListedCompany]
    left_out: list[ListedCompany]
    details: dict[str, str]
    errors: list[str]


_env = Environment(
    loader=PackageLoader("outreach.render", "templates"),
    # Templates live at templates/*.j2 (required by the package-data glob in
    # pyproject.toml), so select_autoescape's extension sniffing would look at
    # the trailing ".j2" and never match "html" -- turn autoescaping on
    # unconditionally instead, since every template this env loads is HTML.
    autoescape=True,
)
_env.tests["http_url"] = is_http_url


def build_contact_view(ctx: RunContext, run_id: int,
                       summary: RunSummary | None = None) -> ContactReportView:
    """Read a stored run into the report's view.

    `summary` is what the run counted as it went (its HN funnel and errors); a
    report rendered later has none, and says "not recorded" where it would show them.
    """
    run = ctx.runs.info(run_id)
    if run is None or run.kind is None or run.filters is None or run.people is None:
        raise ValueError(f"run {run_id} does not exist or was recorded by an older version "
                         "of this tool")
    return _ViewBuilder(ctx, run).build(summary)


class _ViewBuilder:
    def __init__(self, ctx: RunContext, run: RunInfo) -> None:
        self.ctx, self.run = ctx, run
        self.run_id, self.kind = run.run_id, run.kind
        self.filters, self.people = run.filters, run.people

    def build(self, summary: RunSummary | None) -> ContactReportView:
        with_contacts: list[CompanyCard] = []
        no_one_found: list[ListedCompany] = []
        earlier: list[ListedCompany] = []
        left_out: list[ListedCompany] = []
        rows = self.ctx.company_info.for_run(self.run_id)
        for company_id, info in rows:
            company = self.ctx.companies.get(company_id)
            status = (self.ctx.runs.stage_status(self.run_id, company_id, "contacts")
                      or self.ctx.runs.stage_status(self.run_id, company_id, "discover"))
            if status == "bought":
                with_contacts.append(self._card(company_id, info, company))
            elif status in _NO_ONE_FOUND:
                no_one_found.append(ListedCompany(
                    company.name, company.canonical_domain, self._no_one_reason(status)))
            elif status in _EARLIER:
                earlier.append(ListedCompany(company.name, company.canonical_domain,
                                             self._earlier_reason(company_id, company, status)))
            else:
                left_out.append(ListedCompany(company.name, company.canonical_domain,
                                              self._left_out_reason(company_id, status)))
        credits_line = _credits_line(self.run.spend)
        return ContactReportView(
            self.run_id, self.kind, self._heading(), describe_filters(self.filters), self.people,
            self.run.started_at.date(), credits_line, with_contacts, no_one_found, earlier,
            left_out, self._details(rows, credits_line, summary),
            list(summary.errors) if summary else [_NOT_RECORDED])

    def _heading(self) -> str:
        if self.kind == "jobs":
            return f"{self.run.role}{_JOB_RUN_SUFFIX}"
        return _COMPANY_RUN_HEADING

    # --- why a company has no card ---

    def _no_one_reason(self, status: str) -> str:
        reason = f'Hunter has no one matching "{self.people}"'
        # Only a count of zero is free. A search that returned people who were all dropped
        # (excluded titles) ends `empty` after the addresses were paid for.
        return f"{reason} — cost nothing" if status == "no_matching_people" else reason

    def _earlier_reason(self, company_id: int, company: Company, status: str) -> str:
        if status == "skipped_earlier_list":
            bought = self.ctx.runs.bought_before(company_id, self.people, self.run_id)
            if bought is None:
                return "already on an earlier list — not bought again"
            earlier_run, when = bought
            return f"on run {earlier_run}, {when:%d %b %Y} — not bought again"
        mark = self.ctx.emailed.mark_for_company(company_id, company.canonical_domain)
        if mark is None:  # the mark was removed after the run
            return "you've emailed someone here"
        return f"you emailed {mark.address} on {mark.emailed_on:%d %b %Y}"

    def _left_out_reason(self, company_id: int, status: str | None) -> str:
        if status in _LEFT_OUT_REASONS:
            return _LEFT_OUT_REASONS[status]
        if status == "skipped_cap":
            return f"over this run's HN cap of {self.ctx.config.hn.max_new_companies}"
        if status == "over_cap":
            return f"over this run's cap of {self.ctx.config.run.max_companies} companies"
        if status == "failed":
            error = self.ctx.runs.stage_error(self.run_id, company_id, "contacts")
            return f"failed — {error}" if error else "failed"
        return "not reached"  # discovered, and the run stopped before it got to it

    # --- a card ---

    def _card(self, company_id: int, info: CompanyInfo, company: Company) -> CompanyCard:
        hn_post = (self.ctx.hn_posts.for_company(self.run_id, company_id)
                   if info.source == "hn" else None)
        if info.source != "hn":
            via = "via job board"
        elif hn_post is None:
            via = "via HN Who is hiring?"
        else:
            via = f"via HN Who is hiring? ({hn_post.posted_at:%B %Y})"
        postings = [PostingLine(p.title, p.url, p.work_mode, via)
                    for p in self.ctx.postings.for_company(self.run_id, company_id)]
        contacts = self.ctx.run_contacts.for_company(self.run_id, company_id)
        return CompanyCard(
            company.name, company.canonical_domain, info.sector, info.size,
            location_label(self.filters.location) if info.source in _HQ_SOURCES else None,
            info.fit == "unknown", postings,
            self.run.role if info.source == "hunter_hiring" else None,
            [self._person(contact, company.name) for contact in contacts])

    def _person(self, contact: Contact, company_name: str) -> PersonLine:
        email = contact.email if contact.email_status == "verified" else None
        profile_url = contact.profile_url if is_http_url(contact.profile_url) else None
        return PersonLine(
            contact.full_name, contact.title, email, profile_url,
            None if profile_url else linkedin_search_url(contact.full_name, company_name),
            # The emailed list holds addresses lowercased; a contact's is as Hunter gave it.
            self.ctx.emailed.get(email.lower()) if email else None)

    # --- run details ---

    def _details(self, rows: list[tuple[int, CompanyInfo]], credits_line: str,
                 summary: RunSummary | None) -> dict[str, str]:
        spend = self.run.spend
        details = {
            "Companies found": self._found(rows),
            "Emails revealed": str(spend.emails) if spend else _NOT_RECORDED,
            "Addresses checked": str(spend.verifications) if spend else _NOT_RECORDED,
            "Credits": credits_line,
        }
        if self.kind == "jobs":
            details.update(_hn_funnel(summary))
        return details

    def _found(self, rows: list[tuple[int, CompanyInfo]]) -> str:
        """The companies discovery kept, by where they came from."""
        counts = Counter(info.source for company_id, info in rows
                         if self.ctx.runs.stage_status(self.run_id, company_id, "discover") == "ok")
        found = [f"{counts[source]} {label}" for source, label in _SOURCE_LABELS if counts[source]]
        return " · ".join(found) or "none"


def _credits_line(spend: Spend | None) -> str:
    if spend is None:
        return _NOT_RECORDED
    used = f"{format_credits(spend.spent)} credits used"
    if spend.balance_before is None:
        return used
    return f"{used} · {format_credits(spend.balance_before - spend.spent)} left"


def _hn_funnel(summary: RunSummary | None) -> dict[str, str]:
    if summary is None:
        values = [_NOT_RECORDED] * 5
    else:
        values = [summary.hn_thread_title or "none",
                  f"{summary.hn_posts_read} / {summary.hn_role_matched}",
                  f"{summary.hn_parsed} / {summary.hn_llm_parsed} / {summary.hn_llm_rejected}",
                  str(summary.hn_filtered),
                  f"{summary.hn_kept} / {summary.hn_skipped_cap}"]
    labels = ("HN thread", "HN posts read / matching role", "HN parsed / LLM-parsed / LLM-rejected",
              "HN filtered out", "HN kept / over the cap")
    return dict(zip(labels, values))


def render_contact_report(view: ContactReportView) -> str:
    return _env.get_template("contacts.html.j2").render(view=view)


def _file_slug(view: ContactReportView) -> str:
    """The part of the file name that says what the run was for: the role of a
    jobs run, the first sector of a companies run. Both are read back from the
    heading and the filters line, which are built from them in this module."""
    if view.kind == "jobs":
        return slugify(view.heading.removesuffix(_JOB_RUN_SUFFIX))
    # "any sector" when the run named none, as describe_filters prints it.
    return slugify(view.filters_line.split(" · ")[0].split("; ")[0])


def write_contact_report(view: ContactReportView, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    name = "-".join(part for part in (view.run_date.isoformat(), f"run{view.run_id}", view.kind,
                                      _file_slug(view)) if part)
    path = directory / f"{name}.html"
    path.write_text(render_contact_report(view), encoding="utf-8")
    return path
