from __future__ import annotations

import re
from collections import defaultdict
from typing import Sequence

from outreach.types import EvidenceItem

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_theme(raw: str) -> str:
    """Lowercase, collapse any run of non-alphanumerics to a single hyphen.

    Deliberately dumb: exact match after normalization, no embeddings and no
    similarity threshold. A split cluster fails the gate and surfaces as a
    no-bottleneck company, which is the safe direction to fail in.
    """
    return _NON_ALNUM.sub("-", raw.strip().lower()).strip("-")


def cluster_by_theme(items: Sequence[EvidenceItem]) -> dict[str, list[EvidenceItem]]:
    clusters: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in items:
        clusters[normalize_theme(item.theme)].append(item)
    return dict(clusters)
