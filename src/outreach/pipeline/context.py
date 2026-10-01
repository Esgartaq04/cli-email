from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from outreach.config import Config
from outreach.contacts.base import ContactProvider
from outreach.contacts.facts import CompanyFactsProvider
from outreach.llm.base import LLMClient
from outreach.net.fetcher import Fetcher
from outreach.sources.base import JobBoardSource
from outreach.sources.hn import HNSource
from outreach.store.cache import DocumentCache
from outreach.store.dimensions import CompanyRepo, ContactRepo, DocumentRepo
from outreach.store.runs import (EvidenceRepo, FetchAttemptRepo, FindingRepo, HNPostRepo,
                                 PostingRepo, RunRepo)


@dataclass
class RunContext:
    """Everything a run touches, assembled once at the edge.

    The adapters are held behind their protocols so a test substitutes a
    fake without the runner knowing, and `today` is passed in rather than
    read from the clock so a run is reproducible.
    """

    config: Config
    today: date
    companies: CompanyRepo
    contacts: ContactRepo
    documents: DocumentRepo
    runs: RunRepo
    evidence: EvidenceRepo
    findings: FindingRepo
    postings: PostingRepo
    fetch_attempts: FetchAttemptRepo
    cache: DocumentCache
    fetcher: Fetcher
    llm: LLMClient
    job_board: JobBoardSource
    contact_provider: ContactProvider
    facts_provider: CompanyFactsProvider
    # HN discovery. None means no HN source at all (discovery is skipped).
    hn: HNSource | None = None
    hn_posts: HNPostRepo | None = None
