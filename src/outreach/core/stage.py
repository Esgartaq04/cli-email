"""Deterministic startup-stage classification.

The LLM never decides stage: it only extracts quote-backed signals (funding
rounds, headcount statements, expansion moves). This module turns those plus
structured company facts into a Stage, so the decision is reproducible and its
truth table is the test. Pure: no I/O, no clock (`today` is passed in).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Sequence

from outreach.config import StageConfig
from outreach.core.themes import EXPANSION_SIGNALS
from outreach.types import (
    CompanyFacts,
    EvidenceItem,
    FundingRound,
    Stage,
    StageResult,
)

# --- funding-round parsing -------------------------------------------------

_PRE_SEED = re.compile(r"\bpre-?seed\b", re.I)
# A bare "seed" is too ambiguous ("the seed of the idea"), so it must sit in
# round context. The lookbehind keeps "pre-seed round" from also reading as seed.
_SEED = re.compile(
    r"(?<!pre-)\bseed\s+(?:round|funding|financing|investment)\b"
    r"|\braised\b[^.]{0,60}(?<!pre-)\bseed\b",
    re.I,
)
_SERIES = re.compile(r"\bseries\s+([a-z])\b", re.I)
_IPO = re.compile(
    r"\bIPO\b|initial public offering|went public|began trading on (?:the )?(?:NYSE|NASDAQ)",
    re.I,
)
# Requires "by": "we acquired Tinyco" is an acquirer, not an acquired company.
_ACQUIRED = re.compile(r"(?:was |been |is being )?acquired\s+by\b", re.I)

_HEADCOUNT = re.compile(
    r"(?:team of|over|more than|nearly|about|almost)?\s*"
    r"(?<![\d,])(\d{1,3}(?:,\d{3})*|\d+)\+?\s+"
    r"(?:people|employees|team members|teammates|staff)",
    re.I,
)

_SERIES_KIND = re.compile(r"series_([a-z])$")
_TERMINAL_KINDS = ("ipo", "acquired")


def _kind_rank(kind: str) -> int:
    """Order of rounds: pre_seed < seed < series_a < ... < series_z < ipo/acquired.

    Unrecognised kinds rank below everything, so they never beat a real round.
    """
    if kind == "pre_seed":
        return 0
    if kind == "seed":
        return 1
    m = _SERIES_KIND.match(kind)
    if m:
        return 2 + ord(m.group(1)) - ord("a")
    if kind in _TERMINAL_KINDS:
        return 100
    return -1


def parse_round(text: str, announced: date | None = None) -> FundingRound | None:
    """Read the most advanced funding event a sentence states, or None.

    "Series A in 2021, then a Series B this year" is series_b: the company has
    reached the highest round mentioned.
    """
    if _IPO.search(text):
        return FundingRound("ipo", announced)
    if _ACQUIRED.search(text):
        return FundingRound("acquired", announced)
    letters = [m.group(1).lower() for m in _SERIES.finditer(text)]
    if letters:
        return FundingRound(f"series_{max(letters)}", announced)
    if _SEED.search(text):
        return FundingRound("seed", announced)
    if _PRE_SEED.search(text):
        return FundingRound("pre_seed", announced)
    return None


def parse_headcount_statement(text: str) -> int | None:
    m = _HEADCOUNT.search(text)
    return int(m.group(1).replace(",", "")) if m else None


# --- company size bands ----------------------------------------------------

_BAND_RANGE = re.compile(r"^\s*(\d[\d,]*)\s*([kK])?\s*[-–]\s*(\d[\d,]*)\s*([kK])?\s*$")
_BAND_OPEN = re.compile(r"^\s*(\d[\d,]*)\s*([kK])?\s*\+\s*$")


def _band_number(digits: str, k: str | None) -> int:
    n = int(digits.replace(",", ""))
    return n * 1000 if k else n


def parse_band(band: str | None) -> tuple[int, int | None] | None:
    """"11-50" -> (11, 50); "10K+" / "10000+" -> (10000, None); else None."""
    if not band:
        return None
    m = _BAND_RANGE.match(band)
    if m:
        return _band_number(m.group(1), m.group(2)), _band_number(m.group(3), m.group(4))
    m = _BAND_OPEN.match(band)
    if m:
        return _band_number(m.group(1), m.group(2)), None
    return None


def exceeds_cap(headcount: int | None, band: str | None, cap: int) -> bool:
    """True only when the company is *known* to be over the cap.

    A band's lower bound is enough: "1001-5000" with a cap of 2000 is not known
    to exceed it, while "5001-10000" certainly does. Unknown size never drops a
    company.
    """
    parsed = parse_band(band)
    if parsed is not None:
        return parsed[0] > cap
    return headcount is not None and headcount > cap


# --- classification --------------------------------------------------------

_PRIORITY = {
    Stage.GROWTH: 0,
    Stage.EXPANSION: 0,
    Stage.UNKNOWN: 1,
    Stage.SEED_STARTUP: 2,
    Stage.MATURITY: 3,
}


def stage_sort_key(stage: Stage, headcount: int | None) -> tuple[int, bool, int]:
    """Sort key: target stages first, then known-size before unknown, smaller first."""
    return (_PRIORITY[stage], headcount is None, headcount or 0)


def _latest_round(rounds: Sequence[FundingRound]) -> FundingRound | None:
    # Dated rounds rank after undated ones; ties fall to the more advanced kind.
    return max(
        rounds,
        key=lambda r: (r.announced is not None, r.announced or date.min, _kind_rank(r.kind)),
        default=None,
    )


def _round_label(r: FundingRound) -> str:
    base = "IPO" if r.kind == "ipo" else r.kind.replace("_", " ").title()
    return f"{base} ({r.announced:%Y-%m})" if r.announced else base


def _stage_from_round(kind: str) -> Stage | None:
    m = _SERIES_KIND.match(kind)
    if m:
        return Stage.EXPANSION if m.group(1) >= "c" else Stage.GROWTH
    if kind in ("seed", "pre_seed"):
        return Stage.SEED_STARTUP
    return None


def classify_stage(
    facts: CompanyFacts | None,
    signals: Sequence[EvidenceItem],
    today: date,
    config: StageConfig,
) -> StageResult:
    """First match wins; funding beats headcount, which beats nothing.

    Structured facts (Hunter) take precedence over quote-derived signals; the
    quotes only fill gaps.
    """
    rounds: Sequence[FundingRound] = facts.funding_rounds if facts else ()
    if not rounds:
        rounds = [
            r
            for s in signals
            if s.theme == "funding-round"
            for r in [parse_round(s.quote, s.published_at)]
            if r is not None
        ]
    latest = _latest_round(rounds)

    headcount = facts.headcount if facts else None
    if headcount is None:
        stated = [
            n
            for s in signals
            if s.theme == "headcount-statement"
            for n in [parse_headcount_statement(s.quote)]
            if n is not None
        ]
        headcount = max(stated, default=None)

    founded = facts.founded_year if facts else None

    stage = Stage.UNKNOWN
    if latest is not None and latest.kind in _TERMINAL_KINDS:
        stage = Stage.MATURITY
    elif (
        founded is not None
        and today.year - founded >= config.maturity_min_age_years
        and latest is not None
        and latest.announced is not None
        and (today - latest.announced).days >= 365 * config.maturity_min_years_since_funding
    ):
        stage = Stage.MATURITY
    else:
        from_round = _stage_from_round(latest.kind) if latest is not None else None
        if from_round is not None:
            stage = from_round
        elif headcount is not None:
            if headcount > config.growth_max_headcount:
                stage = Stage.EXPANSION
            elif headcount > config.seed_max_headcount:
                stage = Stage.GROWTH
            else:
                stage = Stage.SEED_STARTUP

    reasons: list[str] = []
    if latest is not None:
        reasons.append(_round_label(latest))
    if headcount is not None:
        reasons.append(f"~{headcount} employees")
    if founded is not None:
        reasons.append(f"founded {founded}")

    if stage is Stage.GROWTH:
        recent = next(
            (
                s
                for s in signals
                if s.theme in EXPANSION_SIGNALS
                and s.published_at is not None
                and (today - s.published_at).days <= config.signal_recency_days
            ),
            None,
        )
        if recent is not None:
            stage = Stage.EXPANSION
            reasons.append(f"expansion signal: {recent.theme.replace('-', ' ')}")

    return StageResult(stage, tuple(reasons))
