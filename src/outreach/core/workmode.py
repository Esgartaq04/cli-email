"""Work-mode and employment classification for job postings.

Pure string matching: no I/O. Job-board adapters call the classifiers to tag a
PostingRef; the runner calls `parse_work_modes` / `posting_matches` to filter.

The filters are deliberately lenient. A posting whose mode cannot be determined
stays ("unknown"), and only a posting positively identified as contract /
part-time / intern / temporary ("other") is dropped. Dropping on a guess would
silently hide real startups; keeping an unknown only costs a glance at the report.
"""
from __future__ import annotations

import html
import re

from outreach.types import (
    ALL_WORK_MODES,
    EmploymentType,
    PostingRef,
    WorkMode,
)

_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")


def _plain(content: str) -> str:
    """Strip HTML tags (to a space, so <li>a</li><li>b</li> doesn't glue) and unescape.

    A regex strip is enough here: we only pattern-match the result, never render
    it. core/ must not import from extraction/, so this is not shared with the
    real HTML extractor.
    """
    # Collapse whitespace so multi-word patterns survive newlines and tag gaps.
    return _WHITESPACE.sub(" ", html.unescape(_TAG.sub(" ", content)))


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Location / title: short, structured strings, so bare words are a reliable signal.
# "distributed" is excluded when it names a technical field ("Distributed Systems
# Engineer"), which is far more common in titles than a distributed-team posting.
_LT_HYBRID = _rx(r"\bhybrid\b")
_LT_REMOTE = _rx(
    r"\b(?:remote|work from home|wfh)\b"
    r"|\bdistributed\b(?!\s+(?:systems?|computing|databases?|storage|training|tracing|ledger))"
)
_LT_ONSITE = _rx(r"\bon-?site\b|\bin[- ]office\b")

# Content: a long free-text body where bare "remote" is noise ("remote monitoring
# product"), so only explicit statements count. Hybrid is some days in the
# office, not all of them: one to four days a week is hybrid, five is onsite.
_C_HYBRID = _rx(r"\bhybrid\b|\b[1-4] days? (?:a|per) week in (?:the|our) office\b")
_C_REMOTE = _rx(r"\bfully remote\b|\b100% remote\b|\bremote-first\b")
_C_ONSITE = _rx(
    r"\bon-?site\b|\bin-office\b|\bin office 5 days\b"
    r"|\b5 days (?:a|per) week in (?:the|our) office\b"
)


def _first_match(text: str, patterns: tuple[tuple[re.Pattern[str], WorkMode], ...]) -> WorkMode:
    # Order encodes precedence: "Remote or Hybrid" is a hybrid role.
    for pattern, mode in patterns:
        if pattern.search(text):
            return mode
    return "unknown"


def classify_work_mode(location: str, title: str, content: str = "") -> WorkMode:
    """Classify remote / hybrid / onsite, or "unknown" when nothing says so.

    Location and title are checked first. Content is consulted only when they
    are inconclusive. A bare city ("New York, NY") is never read as onsite.
    """
    mode = _first_match(
        f"{location} {title}",
        ((_LT_HYBRID, "hybrid"), (_LT_REMOTE, "remote"), (_LT_ONSITE, "onsite")),
    )
    if mode != "unknown" or not content:
        return mode
    return _first_match(
        _plain(content),
        ((_C_HYBRID, "hybrid"), (_C_REMOTE, "remote"), (_C_ONSITE, "onsite")),
    )


# ATS "declared" values (Ashby employmentType, Lever commitment), compared after
# lowercasing and stripping '-', '_' and spaces.
_DECLARED_SEPARATORS = re.compile(r"[-_\s]+")
_DECLARED_FULL_TIME = frozenset({"fulltime"})
_DECLARED_OTHER = frozenset(
    {"parttime", "intern", "internship", "contract", "contractor", "temporary", "temp"}
)

# Only the title is searched for non-full-time hints: "contract" in a body is
# usually about customers, not the role. Word boundaries keep "Internal Tools"
# and "Contracts Manager" out.
#
# "Contract" and "Temp" count only as a qualifier on the role -- in
# parentheses, after a dash, or as the title's last word -- never as part of
# what the role works on: "Smart Contract Engineer" and "Contract Lifecycle
# Engineer" are full-time jobs, and dropping one hides a real opening.
# "Smart Contract" is the common title that ends on the word, so the
# trailing-word rule leaves it out by name.
_QUALIFIER = r"(?:contract(?:or)?|temp)"
_TITLE_OTHER = _rx(
    r"\bintern(?:ship)?\b|\bpart[- ]time\b|\btemporary\b"
    r"|\bcontract[- ]to[- ]hire\b"
    rf"|\(\s*{_QUALIFIER}\s*\)"  # "(Contract)", "(Temp)"
    rf"|[-–—]\s*{_QUALIFIER}\s*(?:$|[-–—|,(/])"  # "- Contract - Remote"
    rf"|(?<!smart )\b{_QUALIFIER}\s*$"  # "Engineer, Contractor"
)
_FULL_TIME = _rx(r"\bfull[- ]time\b")


def classify_employment(
    title: str, content: str = "", declared: str | None = None
) -> EmploymentType:
    """Classify full_time / other / unknown.

    Precedence: a recognised `declared` value, then non-full-time hints in the
    title, then "full-time" in title or content. An unrecognised `declared`
    value falls through rather than counting as unknown.
    """
    if declared:
        key = _DECLARED_SEPARATORS.sub("", declared).lower()
        if key in _DECLARED_FULL_TIME:
            return "full_time"
        if key in _DECLARED_OTHER:
            return "other"
    if _TITLE_OTHER.search(title):
        return "other"
    if _FULL_TIME.search(title) or (content and _FULL_TIME.search(_plain(content))):
        return "full_time"
    return "unknown"


def parse_work_modes(flag: str | None) -> frozenset[WorkMode]:
    """Parse a comma-separated --work-mode value; None or blank means all modes.

    Accepts "on-site"/"on_site" for "onsite". Raises ValueError naming the
    offending token ("unknown" is a classification, not a filter value).
    """
    if flag is None:
        return ALL_WORK_MODES
    modes: set[WorkMode] = set()
    for raw in flag.split(","):
        token = raw.strip()
        if not token:
            continue
        key = _DECLARED_SEPARATORS.sub("", token).lower()
        if key not in ALL_WORK_MODES:
            allowed = ", ".join(sorted(ALL_WORK_MODES))
            raise ValueError(f"unknown work mode {token!r}; expected one or more of: {allowed}")
        modes.add(key)  # type: ignore[arg-type]  # narrowed by the membership check
    return frozenset(modes) or ALL_WORK_MODES


def posting_matches(posting: PostingRef, wanted: frozenset[WorkMode]) -> bool:
    """Keep a posting unless it is known non-full-time or known to be an unwanted mode."""
    if posting.employment_type == "other":
        return False
    return posting.work_mode == "unknown" or posting.work_mode in wanted
