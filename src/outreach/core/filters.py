"""Run filters: sector, size and location. Pure functions, no I/O.

Sectors and size bands are Hunter's own vocabulary, so what the user types is
exactly what Hunter is later asked for. (The sector list itself is read by
`outreach.sectors`; callers pass it in as `known`.) A location is read into a
`Location`, which maps straight onto Hunter's headquarters filter and is also
what a job posting's location is checked against.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import replace
from typing import Sequence

# Pure regex over location strings -- no I/O -- shared so a posting is checked
# for a place the way the US-only scope reads it: same word boundaries.
from outreach.sources.jobboards.region import _contains_marker
from outreach.types import Location, WorkMode

SIZE_BANDS: tuple[str, ...] = (
    "1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10001+",
)


class FilterError(ValueError):
    """A filter value that cannot be used; the message is shown to the user as written."""


# --- sectors and sizes ---------------------------------------------------------

def _by_lowercase(known: Sequence[str]) -> dict[str, str]:
    return {name.lower(): name for name in known}


def suggest_sectors(value: str, known: Sequence[str], limit: int = 3) -> list[str]:
    """Up to `limit` sector names near `value`: close spellings first, then names containing it."""
    needle = value.strip().lower()
    if not needle or limit < 1:
        return []  # a blank is a substring of every name, which would suggest anything
    names = _by_lowercase(known)
    close = difflib.get_close_matches(needle, list(names), n=limit)
    containing = [name for name in names if needle in name]
    # A name that is both comes once, at its first position.
    return [names[name] for name in list(dict.fromkeys(close + containing))[:limit]]


def search_sectors(word: str | None, known: Sequence[str]) -> list[str]:
    """The names that contain `word` (any case), in the list's order; all of them without a word."""
    if word is None:
        return list(known)
    needle = word.lower()
    return [name for name in known if needle in name.lower()]


def _unknown_sector(value: str, known: Sequence[str]) -> str:
    near = suggest_sectors(value, known)
    if not near:
        return f'Unknown sector "{value}". Run "outreach sectors <word>" to search the list.'
    # Joined with ";" because Hunter's names hold commas ("Health, Wellness and Fitness").
    options = "; ".join(near)
    return (f'Unknown sector "{value}". Did you mean: {options}? '
            'Run "outreach sectors" to see them all.')


def parse_sectors(values: Sequence[str], known: Sequence[str]) -> tuple[str, ...]:
    """Each sector the user named, in Hunter's spelling, in order and without repeats."""
    names = _by_lowercase(known)
    found: dict[str, None] = {}
    for value in values:
        name = names.get(value.strip().lower())
        if name is None:
            raise FilterError(_unknown_sector(value, known))
        found[name] = None
    return tuple(found)


def parse_sizes(values: Sequence[str]) -> tuple[str, ...]:
    """Hunter's size bands exactly as it spells them, in order and without repeats."""
    for value in values:
        if value not in SIZE_BANDS:
            raise FilterError(f'Unknown size band "{value}". Use one of: {", ".join(SIZE_BANDS)}')
    return tuple(dict.fromkeys(values))


# --- lookup tables -------------------------------------------------------------

US_STATES: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
_STATE_BY_NAME = {name.lower(): code for name, code in US_STATES.items()}
_NAME_BY_STATE = {code: name for name, code in US_STATES.items()}

# What each country is called when printed, by ISO 3166 alpha-2 code.
_COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "IE": "Ireland",
    "DE": "Germany", "FR": "France", "NL": "Netherlands", "ES": "Spain", "PT": "Portugal",
    "IT": "Italy", "CH": "Switzerland", "BE": "Belgium", "AT": "Austria", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland", "IN": "India",
    "SG": "Singapore", "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong",
    "AU": "Australia", "NZ": "New Zealand", "IL": "Israel", "AE": "United Arab Emirates",
    "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CO": "Colombia", "CL": "Chile",
}

# Every lowercased country name and alias people write, to its ISO code. Bare ISO
# codes are left out on purpose: "CA", "IN", "DE" and "CO" are US states first, and
# "ca" as an alias would let "San Francisco, CA" pass a search for Canada. (A test
# keeps these names and the states' apart.)
COUNTRIES: dict[str, str] = {
    **{name.lower(): iso for iso, name in _COUNTRY_NAMES.items()},
    "usa": "US", "us": "US", "uk": "GB", "great britain": "GB", "england": "GB",
    "uae": "AE", "korea": "KR",
}
_US_NAMES = frozenset(name for name, iso in COUNTRIES.items() if iso == "US")
_COUNTRY_WORDS = {
    iso: tuple(name for name, code in COUNTRIES.items() if code == iso) for iso in _COUNTRY_NAMES
}


# --- location parsing ----------------------------------------------------------

_LOCATION_HELP = (
    'Use a US state ("TX"), a city and state ("Austin, TX"), '
    'or a country ("Canada", "Toronto, Canada").'
)


def _read_place(name: str) -> Location | None:
    """A US state (by code or name) or a country, with no city; None when it is neither."""
    # A state before a country: "CA" is California, "IN" is Indiana.
    if name.upper() in _NAME_BY_STATE:
        return Location("US", name.upper())
    key = name.lower()
    if key in _STATE_BY_NAME:
        return Location("US", _STATE_BY_NAME[key])
    iso = COUNTRIES.get(key)
    if iso == "US":
        return Location()
    return Location(iso) if iso else None


def parse_location(text: str | None) -> Location:
    """Read "TX", "Austin, TX", "Canada" or "Toronto, Canada" (nothing means the whole US)."""
    if text is None or not text.strip():
        return Location()
    parts = [" ".join(part.split()) for part in text.split(",")]
    if len(parts) > 1 and parts[-1].lower() in _US_NAMES:
        parts.pop()  # "Austin, TX, US" is "Austin, TX"
    place = _read_place(parts[-1])
    if place is not None and len(parts) == 1:
        return place
    if place is not None and len(parts) == 2 and parts[0]:
        return replace(place, city=parts[0])
    raise FilterError(f"Can't read location \"{text.strip()}\". {_LOCATION_HELP}")


def location_label(location: Location) -> str:
    """How a location is printed: "Texas", "Austin, TX", "Canada", "Toronto, Canada"."""
    if location.country == "US" and location.state:
        if location.city:
            return f"{location.city}, {location.state}"
        return _NAME_BY_STATE.get(location.state, location.state)
    place = _COUNTRY_NAMES.get(location.country, location.country)
    return f"{location.city}, {place}" if location.city else place


def hunter_location(location: Location) -> dict[str, str]:
    """The place as Hunter's `headquarters_location` filter takes it, leaving out unset fields."""
    fields = {"country": location.country, "state": location.state, "city": location.city}
    return {key: value for key, value in fields.items() if value}


# --- is a posting in the place asked for ---------------------------------------

# Hub cities people write without a state code ("NYC", "SF Bay Area"): the city
# markers of region._US_MARKERS, each with the state a "--location NY" search
# should find it in. (A test checks every one of those markers is here.)
_HUB_STATES = {
    "nyc": "NY", "new york": "NY", "brooklyn": "NY", "manhattan": "NY",
    "san francisco": "CA", "sf": "CA", "bay area": "CA", "silicon valley": "CA",
    "palo alto": "CA", "mountain view": "CA", "los angeles": "CA",
    "seattle": "WA", "boston": "MA", "austin": "TX", "chicago": "IL", "denver": "CO",
    "atlanta": "GA", "miami": "FL",
    "washington, dc": "DC", "washington dc": "DC",
}

# Washington, DC is not the state of Washington, and West Virginia is not
# Virginia. A text is scanned once, left to right, taking the longest name at
# each spot: "West Virginia" starts before "Virginia" does, and where two names
# start together ("Washington" and "Washington, DC") the longer is tried first.
_DC_NAMES = ("washington, dc", "washington dc", "washington, d.c.", "washington d.c.")
_NAMED_STATE = {**_STATE_BY_NAME, **dict.fromkeys(_DC_NAMES, "DC")}
_STATE_NAME = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(name) for name in sorted(_NAMED_STATE, key=len, reverse=True))
    + r")(?![a-z0-9])"
)
# A ", XX" code anywhere in the text: "Austin, TX / New York", "Austin, TX; Chicago, IL",
# "Plano, TX, USA". The two letters must end a word, so ", Texas" and ", USA" are not
# read as codes; whether they are a state's is checked against _NAME_BY_STATE.
_STATE_CODE = re.compile(r",\s*([a-z]{2})(?![a-z0-9])")


def _has_word(text: str, word: str) -> bool:
    return _contains_marker(text, (word.lower(),))


def _states_named(text: str) -> set[str]:
    """Every US state a lowercased location names, by full name or by a ", XX" code."""
    named = {_NAMED_STATE[match.group()] for match in _STATE_NAME.finditer(text)}
    for match in _STATE_CODE.finditer(text):
        if match.group(1).upper() in _NAME_BY_STATE:
            named.add(match.group(1).upper())
    return named


def _in_us_place(text: str, where: Location) -> bool:
    named = _states_named(text)
    if where.city:
        # The right name in the wrong state is another place: "Austin, MN" is not Austin, TX.
        # A posting that lists several places is in each state it names.
        return _has_word(text, where.city) and (
            not named or where.state is None or where.state in named
        )
    if named:
        return where.state in named
    # Hub cities are for text that gives no state: "Austin / Chicago, IL" is in Illinois.
    return any(_HUB_STATES[city] == where.state and _has_word(text, city) for city in _HUB_STATES)


def _in_foreign_place(text: str, where: Location) -> bool:
    if where.city:
        return _has_word(text, where.city)
    # New Mexico is not Mexico: take the US state names out before looking for the country.
    return _contains_marker(_STATE_NAME.sub(" ", text), _COUNTRY_WORDS.get(where.country, ()))


def posting_in_location(location_text: str, work_mode: WorkMode, where: Location) -> bool:
    """True if a posting at `location_text` is in the place `where`.

    The default (the whole US) admits everything: the board adapters and the HN
    reader have already applied the US-only scope. Inside the US a remote
    posting is open to everyone; elsewhere even a remote one has to name the
    country, since "Remote" alone says nothing about where.

    A US posting is read for every state it names (full names, and ", XX" codes
    anywhere in the text) and passes a state search when the state asked for is
    one of them. Hub cities ("NYC", "Austin") stand in only when no state is
    named. A city search needs the city as a whole word and, if the text names
    any state, the state asked for among them.
    """
    if where == Location():
        return True
    text = location_text.lower()
    if where.country == "US":
        return work_mode == "remote" or _in_us_place(text, where)
    return _in_foreign_place(text, where)
