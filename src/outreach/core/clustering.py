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
    """Group by normalized theme, dropping anything that normalizes empty.

    "", "—" and "..." all normalize to "". Without this guard, two entirely
    unrelated claims that both carry a punctuation-only theme would land in
    the SAME cluster under the "" key -- which can then satisfy all three
    gate conditions and ship a summary connecting two things that have
    nothing to do with each other. `extract_and_persist` already rejects an
    empty-themed claim before it is ever persisted (see extraction/
    extract.py), so this is defense in depth for evidence that reaches here
    by any other path.
    """
    clusters: dict[str, list[EvidenceItem]] = defaultdict(list)
    for item in items:
        theme = normalize_theme(item.theme)
        if not theme:
            continue
        clusters[theme].append(item)
    return dict(clusters)
