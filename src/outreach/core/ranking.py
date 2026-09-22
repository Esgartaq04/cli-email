from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from outreach.config import RankingConfig
from outreach.types import PersonRef

# Ordered most senior first; first match wins.
_TIERS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, ("founder", "cofounder", "cto", "chief technology", "svp", "evp", "vp",
         "vp engineering", "vp of engineering", "head of engineering", "head of platform",
         "head of product engineering")),
    (2, ("director", "codirector", "senior manager", "head of")),
    (3, ("engineering manager", "team lead", "tech lead", "eng manager")),
    (4, ("staff engineer", "principal engineer", "senior engineer", "senior software")),
)

_TIER_WEIGHT = {1: 100.0, 2: 60.0, 3: 40.0, 4: 20.0}
_NEUTRAL_HEADCOUNT = 500
_MAX_SIZE_FACTOR = 5.0
_RELEVANCE_BONUS = 25.0


@dataclass(frozen=True)
class ContactScore:
    score: float
    tier: int
    explanation: str


def _tier_for(title_lower: str) -> int | None:
    for tier, patterns in _TIERS:
        if any(re.search(r"\b" + re.escape(p) + r"\b", title_lower) for p in patterns):
            return tier
    return None


def _size_factor(tier: int, headcount: int | None) -> float:
    """Small companies make senior people genuinely reachable; large ones don't.

    Unknown headcount uses the neutral value rather than dividing by None.
    """
    if tier > 2:
        return 1.0
    effective = headcount if headcount and headcount > 0 else _NEUTRAL_HEADCOUNT
    return min(_MAX_SIZE_FACTOR, max(1.0, _NEUTRAL_HEADCOUNT / effective))


def score_title(
    title: str,
    headcount: int | None,
    role_keywords: Sequence[str],
    config: RankingConfig,
) -> ContactScore | None:
    lowered = title.lower()
    if any(pattern in lowered for pattern in config.exclude_title_patterns):
        return None

    tier = _tier_for(lowered)
    if tier is None:
        return None

    factor = _size_factor(tier, headcount)
    relevant = any(k.lower() in lowered for k in role_keywords)
    score = _TIER_WEIGHT[tier] * factor + (_RELEVANCE_BONUS if relevant else 0.0)

    headcount_text = str(headcount) if headcount else "headcount unknown"
    parts = [f"tier {tier}", f"{headcount_text} employees" if headcount else headcount_text]
    if relevant:
        parts.append("org matches role")
    return ContactScore(score=score, tier=tier, explanation=" · ".join(parts))


def rank_contacts(
    people: Sequence[PersonRef],
    headcount: int | None,
    role_keywords: Sequence[str],
    config: RankingConfig,
) -> list[tuple[PersonRef, ContactScore]]:
    scored: list[tuple[PersonRef, ContactScore]] = []
    for person in people:
        result = score_title(person.title, headcount, role_keywords, config)
        if result is not None:
            scored.append((person, result))
    scored.sort(key=lambda pair: (-pair[1].score, pair[0].full_name))
    return scored[: config.max_contacts_per_company]
