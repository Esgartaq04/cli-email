from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from outreach.config import Config
from outreach.llm.base import LLMClient
from outreach.net.fetcher import Fetcher
from outreach.sources.base import JobBoardSource
from outreach.sources.hn import HNSource
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.runs import FetchAttemptRepo, HNPostRepo, PostingRepo, RunRepo


@dataclass
class RunContext:
    """Everything a run touches, assembled once at the edge.

    The adapters are held behind their protocols so a test substitutes a
    fake without the pipeline knowing, and `today` is passed in rather than
    read from the clock so a run is reproducible. An adapter is None when
    the command doesn't need it, so that command isn't made to configure it.
    """

    config: Config
    today: date
    companies: CompanyRepo
    contacts: ContactRepo
    runs: RunRepo
    postings: PostingRepo
    fetch_attempts: FetchAttemptRepo
    hn_posts: HNPostRepo
    fetcher: Fetcher | None = None
    llm: LLMClient | None = None
    job_board: JobBoardSource | None = None
    hn: HNSource | None = None
