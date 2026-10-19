"""A company run: the companies come from Hunter's company search, and contacts are bought at them.

Discovery is a single free search, made before the run is recorded so that a
Hunter outage leaves no half-started run behind. Whatever it finds is recorded
with the sector and size it was found under, which is also what a resume reads
back instead of searching again.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from outreach.config import PeopleList
from outreach.pipeline.contacts import buy_contacts, restore_candidates
from outreach.pipeline.context import RunContext
from outreach.pipeline.summary import RunSummary
from outreach.sources.companies import CompanyQuery, FoundCompany
from outreach.types import CompanyInfo, RunFilters

SOURCE = "hunter"


def run_companies(ctx: RunContext, filters: RunFilters, people: PeopleList,
                  resume_run_id: int | None = None) -> RunSummary:
    """Find companies in Hunter, buy contacts at them for the `people` list, and finish the run.

    With `resume_run_id` the run's recorded companies are bought at again and no
    search is made. A Hunter search that fails raises before anything is stored.
    """
    if resume_run_id is None:
        # `filters.location` is passed as is: its default is the US, whereas
        # None would ask Hunter for companies anywhere.
        found = ctx.company_search.search(
            CompanyQuery(filters.sectors, filters.sizes, filters.location))
        run_id = ctx.runs.start("companies", "", filters, people.name, datetime.now())
        _record(ctx, run_id, found)
        summary = RunSummary(run_id, found={SOURCE: len(found)})
    else:
        info = ctx.runs.info(resume_run_id)
        if info is None or info.kind != "companies":
            raise ValueError(f"run {resume_run_id} is not a company run")
        run_id = resume_run_id
        summary = RunSummary(run_id)

    buy_contacts(ctx, run_id, people, restore_candidates(ctx, run_id), summary)
    ctx.runs.finish(run_id, datetime.now())
    return summary


def _record(ctx: RunContext, run_id: int, found: Sequence[FoundCompany]) -> None:
    """Store each company in the order Hunter gave them, as discovered."""
    for position, company in enumerate(found):
        company_id = ctx.companies.upsert(company.domain, company.name, None, None)
        ctx.company_info.insert(
            run_id, company_id, CompanyInfo(position, SOURCE, company.sector, company.size))
        ctx.runs.set_stage(run_id, company_id, "discover", "ok")
