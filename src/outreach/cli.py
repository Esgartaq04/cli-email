from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import typer
from dotenv import load_dotenv

from outreach.config import load_config, require_env
from outreach.net.fetcher import Fetcher, build_user_agent
from outreach.pipeline.context import RunContext
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.emailed import EmailedRepo
from outreach.store.runs import (CompanyInfoRepo, FetchAttemptRepo, HNPostRepo, PostingRepo,
                                 RunContactRepo, RunRepo)

app = typer.Typer(help="Find the right people to email at the right companies.")


def build_context(config_path: Path = Path("config.toml")) -> RunContext:
    load_dotenv()
    config = load_config(config_path)

    use_fakes = os.environ.get("OUTREACH_FAKE_ADAPTERS") == "1"
    if use_fakes:
        from outreach.contacts.fake import FakePeopleFinder
        from outreach.llm.fake import FakeLLM
        from outreach.sources.hn import FakeHNSource
        from outreach.sources.jobboards.fake import FakeJobBoardSource
        llm, job_board, hn = FakeLLM(), FakeJobBoardSource([]), FakeHNSource()
        # No credits, so a fake run can never buy anyone.
        people = FakePeopleFinder(balance=0)
    else:
        from outreach.contacts.hunter import HunterProvider
        from outreach.llm.anthropic_client import AnthropicLLM
        llm = AnthropicLLM(require_env("ANTHROPIC_API_KEY"))
        people = HunterProvider(require_env("HUNTER_API_KEY"))
        job_board = hn = None  # set below, both need the fetcher

    conn = connect(config.paths.db)
    # The contact address is deliberately not in the source: it is a
    # politeness signal for site operators, not a secret, but committing it
    # would publish a scrapeable address every time this repo is shared.
    fetcher = Fetcher(
        httpx.Client(),
        user_agent=build_user_agent(os.environ.get("OUTREACH_CONTACT_EMAIL")),
    )
    if not use_fakes:
        from outreach.sources.hn import HNThreadSource
        hn = HNThreadSource(fetcher)
        from outreach.sources.jobboards.ashby import AshbyBoardSource
        from outreach.sources.jobboards.greenhouse import GreenhouseBoardSource
        from outreach.sources.jobboards.lever import LeverBoardSource
        from outreach.sources.jobboards.multi import MultiBoardSource
        discovery = config.discovery
        # A board with no tokens has nothing to search; leaving it out keeps
        # an unconfigured board from being reported as a failing one.
        job_board = MultiBoardSource([
            cls(fetcher, tokens) for cls, tokens in (
                (GreenhouseBoardSource, discovery.greenhouse_tokens),
                (AshbyBoardSource, discovery.ashby_tokens),
                (LeverBoardSource, discovery.lever_tokens),
            ) if tokens])

    return RunContext(
        config=config, today=date.today(),
        companies=CompanyRepo(conn), contacts=ContactRepo(conn), runs=RunRepo(conn),
        postings=PostingRepo(conn), fetch_attempts=FetchAttemptRepo(conn),
        hn_posts=HNPostRepo(conn), company_info=CompanyInfoRepo(conn),
        run_contacts=RunContactRepo(conn), emailed=EmailedRepo(conn),
        fetcher=fetcher, llm=llm, job_board=job_board, hn=hn, people=people,
    )


if __name__ == "__main__":
    app()
