from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from outreach.config import GateConfig
from outreach.types import EvidenceItem


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    reason: str
    evidence_ids: tuple[int, ...]


def _independence_key(item: EvidenceItem) -> tuple[str, str]:
    return (item.source_class.value, item.publisher_domain)


def evaluate(
    items: Sequence[EvidenceItem], config: GateConfig, today: date
) -> GateVerdict:
    """Decide whether a cluster of claims is a shippable bottleneck.

    Pure: no I/O, no clock. `today` is supplied by the caller.
    """
    ids = tuple(i.id for i in items if i.id is not None)

    if not items:
        return GateVerdict(False, "no_evidence", ())

    if len({_independence_key(i) for i in items}) < config.min_independent_sources:
        return GateVerdict(False, "insufficient_independent_sources", ids)

    if config.require_first_party and not any(
        i.source_class.value in config.first_party_classes for i in items
    ):
        return GateVerdict(False, "no_first_party_source", ids)

    cutoff = today - timedelta(days=config.recency_days)
    if not any(i.published_at is not None and i.published_at >= cutoff for i in items):
        return GateVerdict(False, "all_evidence_stale", ids)

    return GateVerdict(True, "passed", ids)
