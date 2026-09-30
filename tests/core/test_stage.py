from __future__ import annotations

from datetime import date

import pytest

from outreach.config import StageConfig
from outreach.core.stage import (
    classify_stage,
    exceeds_cap,
    parse_band,
    parse_headcount_statement,
    parse_round,
    stage_sort_key,
)
from outreach.core.themes import ALL_THEMES, BUILDING_THEMES, EXPANSION_SIGNALS, SIGNAL_THEMES
from outreach.types import CompanyFacts, EvidenceItem, FundingRound, SourceClass, Stage

T = date(2026, 9, 30)
C = StageConfig()


def facts(headcount=None, rounds=(), founded=None):
    return CompanyFacts(headcount, None, founded, tuple(rounds), (), "hunter")


def sig(theme, quote, when=T):
    return EvidenceItem(1, 1, 1, "c", quote, SourceClass.PRESS, "x.example", when, theme)


@pytest.mark.parametrize("f,signals,expected", [
    (facts(rounds=[FundingRound("ipo", date(2021, 5, 1))]), [], Stage.MATURITY),
    (facts(founded=2010, rounds=[FundingRound("series_b", date(2018, 1, 1))], headcount=300), [], Stage.MATURITY),
    (facts(rounds=[FundingRound("series_c", date(2025, 1, 1))], headcount=120), [], Stage.EXPANSION),  # funding beats headcount
    (facts(rounds=[FundingRound("series_b", date(2026, 3, 1))], headcount=800), [], Stage.GROWTH),     # funding beats headcount
    (facts(headcount=650), [], Stage.EXPANSION),
    (facts(headcount=120), [], Stage.GROWTH),
    (facts(headcount=20), [], Stage.SEED_STARTUP),
    (facts(rounds=[FundingRound("seed", None)]), [], Stage.SEED_STARTUP),
    (None, [], Stage.UNKNOWN),
    (None, [sig("funding-round", "We raised a $30M Series B led by Acme.")], Stage.GROWTH),
    (facts(headcount=200), [sig("new-office", "We just opened our London office.")], Stage.EXPANSION),
    (facts(headcount=200), [sig("new-office", "Opened London.", date(2024, 1, 1))], Stage.GROWTH),  # stale signal
    (None, [sig("headcount-statement", "We're a team of 150 people.")], Stage.GROWTH),
])
def test_classify_stage_truth_table(f, signals, expected):
    assert classify_stage(f, signals, T, C).stage is expected


def test_reasons_are_rendered_in_a_fixed_format():
    r = classify_stage(CompanyFacts(350, "201-500", 2019,
                       (FundingRound("series_b", date(2026, 3, 1)),), (), "hunter"), [], T, C)
    assert r.reasons == ("Series B (2026-03)", "~350 employees", "founded 2019")


def test_expansion_signal_reason_names_the_theme():
    r = classify_stage(facts(headcount=200), [sig("new-office", "Opened London.")], T, C)
    assert r.reasons == ("~200 employees", "expansion signal: new office")


def test_undated_round_and_unknown_parts_are_omitted():
    assert classify_stage(facts(rounds=[FundingRound("seed", None)]), [], T, C).reasons == ("Seed",)
    assert classify_stage(None, [], T, C).reasons == ()


def test_quote_backed_round_and_headcount_render_like_facts():
    r = classify_stage(None, [
        sig("funding-round", "We raised a $30M Series B.", date(2026, 3, 12)),
        sig("headcount-statement", "We're a team of 150 people."),
    ], T, C)
    assert r.stage is Stage.GROWTH
    assert r.reasons == ("Series B (2026-03)", "~150 employees")


def test_facts_rounds_win_over_quote_rounds():
    r = classify_stage(facts(rounds=[FundingRound("seed", None)]),
                       [sig("funding-round", "We raised a Series D.")], T, C)
    assert r.stage is Stage.SEED_STARTUP


def test_latest_round_is_by_date_then_kind_rank():
    r = classify_stage(facts(rounds=[FundingRound("series_b", date(2024, 1, 1)),
                                     FundingRound("series_a", date(2025, 1, 1))]), [], T, C)
    assert r.reasons[0] == "Series A (2025-01)"
    # An undated later-stage round does not outrank a dated one.
    r = classify_stage(facts(rounds=[FundingRound("series_b", None),
                                     FundingRound("seed", date(2025, 1, 1))]), [], T, C)
    assert r.reasons[0] == "Seed (2025-01)"
    # Same date: higher stage wins.
    r = classify_stage(facts(rounds=[FundingRound("seed", date(2025, 1, 1)),
                                     FundingRound("series_a", date(2025, 1, 1))]), [], T, C)
    assert r.reasons[0] == "Series A (2025-01)"


def test_maturity_by_age_needs_founded_year_and_dated_round():
    old = date(2018, 1, 1)
    # No founded_year: rule does not fire.
    assert classify_stage(facts(rounds=[FundingRound("series_b", old)]), [], T, C).stage is Stage.GROWTH
    # Undated round: rule does not fire.
    assert classify_stage(facts(founded=2010, rounds=[FundingRound("series_b", None)]), [], T, C).stage is Stage.GROWTH
    # Recent funding keeps an old company out of maturity.
    assert classify_stage(facts(founded=2010, rounds=[FundingRound("series_b", date(2025, 1, 1))]),
                          [], T, C).stage is Stage.GROWTH


def test_acquired_is_maturity():
    assert classify_stage(facts(rounds=[FundingRound("acquired", None)]), [], T, C).stage is Stage.MATURITY


def test_only_expansion_themes_with_recent_dates_promote_growth():
    f = facts(headcount=200)
    assert classify_stage(f, [sig("active-build", "Building.")], T, C).stage is Stage.GROWTH
    assert classify_stage(f, [sig("new-office", "Opened.", None)], T, C).stage is Stage.GROWTH
    assert classify_stage(f, [sig("acquisition", "Bought Tinyco.")], T, C).stage is Stage.EXPANSION


def test_expansion_signal_does_not_change_seed_stage():
    assert classify_stage(facts(headcount=20), [sig("new-office", "Opened.")], T, C).stage is Stage.SEED_STARTUP


def test_bare_seed_without_round_context_does_not_count():
    assert parse_round("the seed of the idea") is None
    assert parse_round("raised $2M in seed funding").kind == "seed"
    assert parse_round("announced a seed investment").kind == "seed"
    assert parse_round("we raised $4M, including a seed extension").kind == "seed"


@pytest.mark.parametrize("text,kind", [
    ("announced our $40M Series C", "series_c"), ("closed a pre-seed round", "pre_seed"),
    ("raised a $3M seed round", "seed"), ("was acquired by BigCo", "acquired"),
    ("completed its initial public offering", "ipo"), ("we acquired Tinyco", None),
    ("Series A in 2021, then a Series B this year", "series_b"),
    ("began trading on the NASDAQ", "ipo"), ("Acme went public", "ipo"),
    ("a pre-seed round, then a seed round", "seed"),
    ("a series of announcements", None),
])
def test_parse_round(text, kind):
    got = parse_round(text)
    assert (got.kind if got else None) == kind


def test_parse_round_carries_announced_date():
    assert parse_round("raised a Series A", date(2026, 3, 1)).announced == date(2026, 3, 1)


@pytest.mark.parametrize("text,n", [
    ("We're a team of 150 people.", 150), ("more than 1,200 employees worldwide", 1200),
    ("about 40+ team members", 40), ("our 12 teammates", 12), ("no numbers here", None),
])
def test_parse_headcount_statement(text, n):
    assert parse_headcount_statement(text) == n


def test_stage_sort_key_orders_targets_first_then_smaller():
    keys = [stage_sort_key(Stage.MATURITY, 10), stage_sort_key(Stage.EXPANSION, 900),
            stage_sort_key(Stage.GROWTH, 80), stage_sort_key(Stage.UNKNOWN, None),
            stage_sort_key(Stage.SEED_STARTUP, 5), stage_sort_key(Stage.GROWTH, None)]
    assert sorted(keys) == [keys[2], keys[1], keys[5], keys[3], keys[4], keys[0]]


@pytest.mark.parametrize("headcount,band,expected", [
    (3000, "1001-5000", False), (None, "5001-10000", True), (2500, None, True),
    (None, None, False), (1999, None, False), (None, "10000+", True)])
def test_exceeds_cap(headcount, band, expected):
    assert exceeds_cap(headcount, band, 2000) is expected


@pytest.mark.parametrize("band,expected", [("11-50", (11, 50)), ("10K+", (10000, None)),
                                           ("10000+", (10000, None)), ("lots", None), (None, None)])
def test_parse_band(band, expected):
    assert parse_band(band) == expected


def test_theme_lists():
    assert ALL_THEMES == BUILDING_THEMES + SIGNAL_THEMES
    assert EXPANSION_SIGNALS <= set(ALL_THEMES)
    assert len(set(ALL_THEMES)) == len(ALL_THEMES)
