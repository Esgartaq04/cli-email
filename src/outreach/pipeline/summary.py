from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunSummary:
    """What a run did, counted as it goes."""

    run_id: int
    errors: list[str] = field(default_factory=list)
    # HN "Who is hiring?" funnel. Posts dropped before a company existed are
    # only ever counted here.
    hn_thread_title: str | None = None
    hn_posts_read: int = 0
    hn_role_matched: int = 0
    hn_parsed: int = 0
    hn_llm_parsed: int = 0
    hn_llm_rejected: int = 0
    hn_filtered: int = 0
    hn_skipped_cap: int = 0
    hn_kept: int = 0
    # Hunter credits, in tenths (see outreach.credits). `bought` counts the
    # companies a person was bought at, including those a resumed run bought
    # before it stopped; `spent`, `emails` and `verifications` only this call's.
    balance_before: int | None = None
    emails: int = 0
    verifications: int = 0
    spent: int = 0
    bought: int = 0
    out_of_credits: int = 0
    # Companies a company run found, by where they came from ({"hunter": n}).
    found: dict[str, int] = field(default_factory=dict)
