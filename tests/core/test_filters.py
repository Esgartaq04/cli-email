from __future__ import annotations

import pytest

from outreach.core.filters import (
    _HUB_STATES,
    COUNTRIES,
    SIZE_BANDS,
    US_STATES,
    FilterError,
    hunter_location,
    location_label,
    parse_location,
    parse_sectors,
    parse_sizes,
    posting_in_location,
    search_sectors,
    suggest_sectors,
)
from outreach.sources.jobboards.region import _US_MARKERS, _US_STATE_CODES
from outreach.types import Location

KNOWN = ("Software Development", "Banking", "Health, Wellness and Fitness", "Higher Education")


# --- sectors ------------------------------------------------------------------

def test_filter_error_is_a_value_error():
    assert issubclass(FilterError, ValueError)


def test_sectors_match_case_insensitively_in_hunters_spelling():
    assert parse_sectors(["software development", "Banking", "banking"], KNOWN) == (
        "Software Development", "Banking")


def test_a_sector_with_a_comma_is_one_sector():
    assert parse_sectors(["Health, Wellness and Fitness"], KNOWN) == ("Health, Wellness and Fitness",)


def test_no_sectors_is_fine():
    assert parse_sectors([], KNOWN) == ()


def test_unknown_sector_suggests_close_names():
    with pytest.raises(FilterError, match='Unknown sector "software developement". Did you mean: Software Development'):
        parse_sectors(["software developement"], KNOWN)


def test_unknown_sector_message_is_pinned():
    with pytest.raises(FilterError) as excinfo:
        parse_sectors(["software developement"], KNOWN)
    assert str(excinfo.value) == (
        'Unknown sector "software developement". Did you mean: Software Development? '
        'Run "outreach sectors" to see them all.')


def test_suggestions_are_separated_by_semicolons_because_names_hold_commas():
    known = ("Health, Wellness and Fitness", "Health Care Services", "Software Development")
    with pytest.raises(FilterError) as excinfo:
        parse_sectors(["health"], known)
    assert 'Did you mean: Health, Wellness and Fitness; Health Care Services? ' in str(excinfo.value)


def test_unknown_sector_with_no_near_name_points_at_the_search():
    with pytest.raises(FilterError) as excinfo:
        parse_sectors(["Zoology"], KNOWN)
    assert str(excinfo.value) == 'Unknown sector "Zoology". Run "outreach sectors <word>" to search the list.'


def test_a_bad_sector_among_good_ones_is_reported():
    with pytest.raises(FilterError, match='Unknown sector "Bankin"'):
        parse_sectors(["Banking", "Bankin"], KNOWN)


def test_close_names_come_before_substring_matches_and_nothing_repeats():
    known = ("Investment Banking", "Banking", "Banks and Credit Unions")
    # "Banking" is both a close match and a substring match, and is listed once.
    assert suggest_sectors("bank", known) == ["Banking", "Investment Banking", "Banks and Credit Unions"]
    assert suggest_sectors("BANK", known) == suggest_sectors("bank", known)


def test_suggestions_are_capped():
    known = ("Banking", "Investment Banking", "Banks and Credit Unions", "Bank Software")
    assert len(suggest_sectors("bank", known)) == 3
    assert suggest_sectors("bank", known, limit=2) == ["Banking", "Investment Banking"]
    assert suggest_sectors("bank", known, limit=0) == []


def test_a_blank_value_suggests_nothing():
    assert suggest_sectors("  ", KNOWN) == []


def test_search_sectors_is_a_substring_filter():
    assert search_sectors("ed", KNOWN) == ["Higher Education"]
    assert search_sectors(None, KNOWN) == list(KNOWN)


def test_search_sectors_ignores_case_and_keeps_the_list_order():
    assert search_sectors("HEALTH", KNOWN) == ["Health, Wellness and Fitness"]
    assert search_sectors("n", KNOWN) == ["Software Development", "Banking", "Health, Wellness and Fitness",
                                          "Higher Education"]
    assert search_sectors("zzz", KNOWN) == []


# --- sizes --------------------------------------------------------------------

def test_sizes_must_be_hunters_bands():
    assert parse_sizes(["51-200", "11-50"]) == ("51-200", "11-50")
    with pytest.raises(FilterError, match="Unknown size band"):
        parse_sizes(["50-200"])


def test_the_size_bands_are_the_eight_hunter_accepts():
    assert SIZE_BANDS == ("1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10001+")
    assert parse_sizes(SIZE_BANDS) == SIZE_BANDS


def test_an_unknown_size_band_lists_the_real_ones():
    with pytest.raises(FilterError) as excinfo:
        parse_sizes(["11-50", "50-200"])
    assert str(excinfo.value) == (
        'Unknown size band "50-200". '
        'Use one of: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+')


def test_a_size_band_is_exact():
    for text in (" 11-50", "11 - 50", "11-50 ", "10001", "10001+ "):
        with pytest.raises(FilterError):
            parse_sizes([text])


def test_a_repeated_size_band_counts_once():
    assert parse_sizes(["11-50", "51-200", "11-50"]) == ("11-50", "51-200")
    assert parse_sizes([]) == ()


# --- location parsing ---------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (None, Location()), ("  ", Location()), ("US", Location()), ("United States", Location()),
    ("TX", Location("US", "TX")), ("tx", Location("US", "TX")), ("CA", Location("US", "CA")),
    ("Texas", Location("US", "TX")), ("Austin, TX", Location("US", "TX", "Austin")),
    ("Austin, TX, US", Location("US", "TX", "Austin")), ("Canada", Location("CA")),
    ("Toronto, Canada", Location("CA", None, "Toronto")),
])
def test_parse_location(text, expected):
    assert parse_location(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("", Location()), ("USA", Location()), ("us", Location()), ("united states", Location()),
    ("DC", Location("US", "DC")), ("District of Columbia", Location("US", "DC")),
    ("Washington, DC", Location("US", "DC", "Washington")),
    ("Washington", Location("US", "WA")),
    ("TEXAS", Location("US", "TX")), ("texas", Location("US", "TX")),
    ("Austin, Texas", Location("US", "TX", "Austin")),
    ("Austin, Texas, USA", Location("US", "TX", "Austin")),
    ("Texas, United States", Location("US", "TX")),
    ("  Austin ,  TX ", Location("US", "TX", "Austin")),
    ("New York, NY", Location("US", "NY", "New York")),
    ("New  York,  NY", Location("US", "NY", "New York")),
    # A state whose name holds another's: West Virginia is not Virginia, New Mexico is not Mexico.
    ("Virginia", Location("US", "VA")), ("West Virginia", Location("US", "WV")),
    ("New Mexico", Location("US", "NM")), ("Mexico", Location("MX")),
    # Two letters are a state first: "IN" is Indiana, not India.
    ("IN", Location("US", "IN")), ("DE", Location("US", "DE")),
])
def test_parse_location_more_ways_to_say_it(text, expected):
    assert parse_location(text) == expected


@pytest.mark.parametrize("name,iso", [
    ("Canada", "CA"), ("United Kingdom", "GB"), ("UK", "GB"), ("Germany", "DE"), ("France", "FR"),
    ("Ireland", "IE"), ("Netherlands", "NL"), ("Spain", "ES"), ("India", "IN"), ("Mexico", "MX"),
    ("Brazil", "BR"), ("Australia", "AU"), ("Singapore", "SG"), ("great britain", "GB"),
])
def test_a_country_by_name_or_alias_with_or_without_a_city(name, iso):
    assert parse_location(name) == Location(iso)
    assert parse_location(f"Anytown, {name}") == Location(iso, None, "Anytown")


@pytest.mark.parametrize("text", ["Austin", "Atlantis", "ZZ", "Austin, ZZ", "a, b, c"])
def test_unreadable_location(text):
    with pytest.raises(FilterError, match="Can't read location"):
        parse_location(text)


@pytest.mark.parametrize("text", [",", "Austin,", ", TX", "TX,,", "Austin, TX, Canada", "Toronto, ON", "Remote"])
def test_other_unreadable_locations(text):
    with pytest.raises(FilterError, match="Can't read location"):
        parse_location(text)


def test_unreadable_location_message_is_pinned():
    with pytest.raises(FilterError) as excinfo:
        parse_location("Atlantis")
    assert str(excinfo.value) == (
        'Can\'t read location "Atlantis". Use a US state ("TX"), a city and state ("Austin, TX"), '
        'or a country ("Canada", "Toronto, Canada").')


# --- location labels and Hunter's filter -------------------------------------

def test_location_label_and_hunter_filter():
    assert [location_label(loc) for loc in (Location(), Location("US", "TX"), Location("US", "TX", "Austin"),
            Location("CA"), Location("CA", None, "Toronto"))] == [
        "United States", "Texas", "Austin, TX", "Canada", "Toronto, Canada"]
    assert hunter_location(Location("US", "TX", "Austin")) == {"country": "US", "state": "TX", "city": "Austin"}
    assert hunter_location(Location()) == {"country": "US"}


def test_more_labels_and_filters():
    assert location_label(Location("US", "DC")) == "District of Columbia"
    assert location_label(Location("US", "DC", "Washington")) == "Washington, DC"
    assert location_label(Location("GB")) == "United Kingdom"
    assert location_label(Location("GB", None, "London")) == "London, United Kingdom"
    assert hunter_location(Location("US", "TX")) == {"country": "US", "state": "TX"}
    assert hunter_location(Location("CA", None, "Toronto")) == {"country": "CA", "city": "Toronto"}


def test_every_state_and_country_the_parser_can_return_prints_as_a_name():
    for code in US_STATES.values():
        assert location_label(Location("US", code)) != code
    for iso in set(COUNTRIES.values()):
        assert location_label(Location(iso)) != iso


# --- the lookup tables --------------------------------------------------------

def test_us_states_are_the_fifty_plus_dc():
    assert len(US_STATES) == 51
    assert sorted(US_STATES.values()) == sorted(_US_STATE_CODES | {"DC"})


def test_no_country_name_or_alias_is_also_a_us_state():
    # A shared word would make "CA" or "Georgia" mean two places.
    states = {name.lower() for name in US_STATES} | {code.lower() for code in US_STATES.values()}
    assert not set(COUNTRIES) & states
    assert all(name == name.lower() for name in COUNTRIES)


def test_every_us_hub_city_has_a_state():
    # The city markers in region._US_MARKERS, as opposed to its "united states" / "us-only" phrases.
    not_cities = {"united states", "usa", "u.s.", "remote - us", "remote (us", "us remote",
                  "us-remote", "remote us", "remote, us", "us only", "us-only"}
    assert set(_US_MARKERS) - not_cities <= set(_HUB_STATES)
    assert set(_HUB_STATES.values()) <= set(US_STATES.values())


# --- is a posting in the place asked for -------------------------------------

TX, AUSTIN, CANADA = Location("US", "TX"), Location("US", "TX", "Austin"), Location("CA")


@pytest.mark.parametrize("text,mode,where,expected", [
    ("Chicago, IL", "onsite", Location(), True), ("", "unknown", Location(), True),
    ("Austin, TX", "onsite", TX, True), ("Dallas, Texas", "hybrid", TX, True), ("Austin", "onsite", TX, True),
    ("Chicago, IL", "onsite", TX, False), ("", "onsite", TX, False),
    ("Remote - US", "remote", AUSTIN, True), ("Austin, TX", "onsite", AUSTIN, True),
    ("Austin, MN", "onsite", AUSTIN, False), ("Austin", "onsite", AUSTIN, True),
    ("Toronto, Canada", "onsite", CANADA, True), ("Remote (Canada)", "remote", CANADA, True),
    ("Remote", "remote", CANADA, False), ("Toronto, ON", "onsite", CANADA, False),
    ("Toronto, ON", "onsite", Location("CA", None, "Toronto"), True),
])
def test_posting_in_location(text, mode, where, expected):
    assert posting_in_location(text, mode, where) is expected


CA, NY, WA, DC = (Location("US", code) for code in ("CA", "NY", "WA", "DC"))
VA, WV, NM = (Location("US", code) for code in ("VA", "WV", "NM"))


@pytest.mark.parametrize("text,mode,where,expected", [
    # Any location is fine when none was asked for, even one outside the US.
    ("Toronto, Canada", "onsite", Location(), True),
    # A state: its code, its name, or a hub city that is known to be in it.
    ("Plano, TX", "onsite", TX, True), ("Houston, Texas", "onsite", TX, True),
    ("AUSTIN, tx", "onsite", TX, True), ("Remote - US", "remote", TX, True),
    ("San Francisco", "hybrid", CA, True), ("SF Bay Area", "onsite", CA, True),
    ("NYC", "onsite", NY, True), ("New York", "onsite", NY, True),
    ("NYC", "onsite", TX, False), ("Austin", "onsite", CA, False),
    ("Plano", "onsite", TX, False),
    # Washington, DC is not Washington state, and West Virginia is not Virginia.
    ("Seattle, Washington", "onsite", WA, True),
    ("Washington, DC", "onsite", WA, False), ("Washington, DC", "onsite", DC, True),
    ("Washington DC", "hybrid", DC, True), ("Washington, D.C.", "onsite", WA, False),
    ("Charleston, West Virginia", "onsite", WV, True), ("Charleston, West Virginia", "onsite", VA, False),
    ("Albuquerque, New Mexico", "onsite", NM, True),
    # A state code still counts when a country follows it.
    ("Plano, TX, USA", "onsite", TX, True), ("San Francisco, CA, United States", "onsite", CA, True),
    ("Plano, TX, US", "onsite", TX, True), ("Plano, TX, USA", "onsite", CA, False),
    ("Austin, MN, USA", "onsite", AUSTIN, False), ("Austin, TX, USA", "onsite", AUSTIN, True),
    # A city, or a hub city, must be a whole word.
    ("Austinville", "onsite", AUSTIN, False), ("Sfax, Tunisia", "onsite", CA, False),
    ("Montrealer", "onsite", Location("CA", None, "Montreal"), False),
    # A city must be in the state asked for, if the text names one.
    ("Austin, Texas", "onsite", AUSTIN, True), ("Austin, Minnesota", "onsite", AUSTIN, False),
    ("Austinville, VA", "onsite", AUSTIN, False), ("AUSTIN", "hybrid", AUSTIN, True),
    ("Dallas, TX", "onsite", AUSTIN, False), ("", "onsite", AUSTIN, False),
    ("St. Louis, MO", "onsite", Location("US", "MO", "St. Louis"), True),
    ("Remote", "remote", Location("US", "TX", "Dallas"), True),
    # Outside the US: a country by name or alias, or the city asked for.
    ("London, UK", "onsite", Location("GB"), True), ("London", "onsite", Location("GB"), False),
    ("Remote - United Kingdom", "remote", Location("GB"), True),
    ("Berlin, Germany", "hybrid", Location("DE"), True), ("Berlin, Germany", "hybrid", Location("FR"), False),
    ("Montreal, QC", "onsite", Location("CA", None, "Montreal"), True),
    ("Remote (Canada)", "remote", Location("CA", None, "Toronto"), False),
    # New Mexico is not Mexico.
    ("Albuquerque, New Mexico", "onsite", Location("MX"), False),
    ("Mexico City, Mexico", "onsite", Location("MX"), True),
    ("Remote - Mexico", "remote", Location("MX"), True),
])
def test_posting_in_location_edge_cases(text, mode, where, expected):
    assert posting_in_location(text, mode, where) is expected
