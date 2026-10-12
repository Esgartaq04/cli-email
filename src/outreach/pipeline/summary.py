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
