"""Which of the people Hunter found are the ones a people list wants.

Pure functions over a `PeopleList` (tiers of wanted job titles, most important
first, plus patterns to drop). Hunter filters by title on its side; these rules
repeat that matching locally so a run can order what it bought, drop anyone it
should not have, and so the fake finder behaves like Hunter.
"""
from __future__ import annotations

import re
from typing import Sequence

from outreach.config import PeopleList
from outreach.types import FoundPerson

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def title_matches(title: str, wanted: str) -> bool:
    """Whether every word of `wanted` is a whole word of `title`, in any order.

    "Co-founder & CTO" matches "cto" and "founder"; "Factory Manager" does not
    match "cto". A `wanted` with no words in it matches nothing.
    """
    wanted_words = _words(wanted)
    return bool(wanted_words) and wanted_words <= _words(title)


def tier_of(title: str, people: PeopleList) -> tuple[int, int] | None:
    """(tier index, title index) of the first wanted title that matches, else None."""
    for tier_index, tier in enumerate(people.tiers):
        for title_index, wanted in enumerate(tier):
            if title_matches(title, wanted):
                return tier_index, title_index
    return None


def is_excluded(title: str, people: PeopleList) -> bool:
    """Whether any exclude pattern appears in `title`, in any capitalisation.

    Patterns are kept as typed in the config, so both sides are lowercased here.
    """
    lowered = title.lower()
    return any(pattern.lower() in lowered for pattern in people.exclude_titles)


def order_people(found: Sequence[FoundPerson], people: PeopleList) -> list[FoundPerson]:
    """Drop the excluded, then put the most wanted first.

    People who match none of the list's titles come last, and equals keep the
    order Hunter returned them in.
    """
    kept = [person for person in found if not is_excluded(person.title, people)]
    unranked = (len(people.tiers), 0)
    return sorted(kept, key=lambda person: tier_of(person.title, people) or unranked)
