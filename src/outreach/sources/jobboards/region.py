"""Region matching shared by every job-board adapter."""
from __future__ import annotations

import re
from typing import Sequence

# Explicit, unambiguous markers that a location is in the US. Bare country-
# code-shaped hints (", CA", ", IL", ...) are deliberately NOT used as
# blanket US signals here -- they alias ISO country codes (Ontario "CA",
# Tel Aviv "IL") and a false accept would defeat V1's US-only scoping.
_US_MARKERS = (
    "united states", "usa", "u.s.", "remote - us", "remote (us", "us remote",
    "us", "us-remote", "remote us", "us only", "us-only",
    # Major US hubs as people write them without a state code ("NYC",
    # "SF Bay Area") -- common in free-text HN posts. Non-US markers are
    # still checked first, so "London" can never be read as US.
    "nyc", "new york", "brooklyn", "manhattan", "san francisco", "sf",
    "bay area", "silicon valley", "palo alto", "mountain view", "seattle",
    "boston", "austin", "los angeles", "chicago", "denver", "atlanta", "miami",
    "washington, dc", "washington dc",
)

# Recognized non-US markers. Checked before the trailing state-code check
# and before the US markers, so a non-US city/country name always wins over
# an incidental two-letter suffix that happens to match a US state code.
#
# This deliberately includes every country whose ISO alpha-2 code collides
# with a US state abbreviation (CA/IL/CO/MA/PA/TN/AL/AZ/GA/LA/MD/MT/NE/SC/
# TO), by country name and by principal city, so e.g. "Bogotá, CO" doesn't
# pass the trailing-state-code check just because Colombia's code is also
# Colorado's. "Dublin, OH" / "Berlin, NH" (a US city sharing a name with a
# foreign capital) are an accepted residual -- see greenhouse.py review notes.
_NON_US_MARKERS = (
    "uk", "united kingdom", "london", "canada", "ontario", "toronto", "vancouver",
    "israel", "tel aviv", "india", "bengaluru", "bangalore", "germany", "berlin",
    "munich", "ireland", "dublin", "netherlands", "amsterdam", "poland", "warsaw",
    "krakow", "singapore", "australia", "sydney", "melbourne", "brazil",
    "são paulo", "sao paulo", "mexico", "spain", "madrid", "barcelona", "france",
    "paris", "japan", "tokyo", "emea", "apac",
    # ISO alpha-2 / US-state-code collisions (CO, MA, PA, TN, AL, AZ, GA,
    # LA, MD, MT, NE, SC, TO): country name plus principal city.
    "colombia", "bogotá", "bogota", "medellín", "medellin",
    "morocco", "casablanca", "rabat",
    "panama", "panama city",
    "tunisia", "tunis",
    "albania", "tirana",
    "azerbaijan", "baku",
    "gabon", "libreville",
    "laos", "vientiane",
    "moldova", "chișinău", "chisinau",
    "malta", "valletta",
    "niger", "niamey",
    "seychelles", "victoria",
    "tonga", "nukuʻalofa", "nukualofa",
)

_US_STATE_CODES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})

# A state code only counts when it is the trailing ", XX" of the string
# (e.g. "Chicago, IL"), not a bare substring anywhere in it.
_TRAILING_STATE_CODE = re.compile(r",\s*([a-zA-Z]{2})\s*$")


def _contains_marker(lowered: str, markers: Sequence[str]) -> bool:
    """True if any marker appears in `lowered` at a word boundary.

    Boundary-checked against ASCII letters/digits so short markers (e.g.
    "uk") don't false-match inside an unrelated word (e.g. "Milwaukee").
    """
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", lowered)
        for marker in markers
    )


# Structured country values (Ashby addressCountry, Lever country) that mean the US.
_US_COUNTRIES = frozenset({"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"})


def matches_region(location: str, region: str, country: str | None = None) -> bool:
    """True if a posting at `location` falls inside `region`.

    Only "US" is scoped; any other region admits everything. A structured
    `country`, when the ATS supplies one, is authoritative over the free-text
    location ("Remote" alone says nothing, but addressCountry does).
    """
    if region.upper() != "US":
        return True
    if country and country.strip():
        return country.strip().upper() in _US_COUNTRIES
    if not location:
        # Fail closed: no declared location is not evidence of a US
        # location, and admitting it risks emailing someone outside
        # the region V1 is scoped to.
        return False
    lowered = location.lower()
    if _contains_marker(lowered, _NON_US_MARKERS):
        return False
    if _contains_marker(lowered, _US_MARKERS):
        return True
    match = _TRAILING_STATE_CODE.search(lowered)
    return bool(match and match.group(1).upper() in _US_STATE_CODES)
