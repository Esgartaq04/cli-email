"""Reading HN "Who is hiring?" posts: pure functions, no I/O.

A post is written by the hiring company, mostly in the conventional
`Company | Role | Location | REMOTE | Full-time` first line followed by
free text and links. Everything here is deterministic so every rule has a
truth-table test; posts these rules cannot read go to the LLM fallback,
whose answer is checked by `validate_llm_post` against the post itself.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from html.parser import HTMLParser
from typing import Sequence
from urllib.parse import urlsplit

from outreach.core.dedupe import canonical_domain
from outreach.core.stage import parse_headcount_statement, parse_round
from outreach.core.workmode import classify_employment, classify_work_mode
# Pure regex over location strings -- no I/O -- shared so an HN location is
# scoped exactly the way a job board's is.
from outreach.sources.jobboards.region import _NON_US_MARKERS, _contains_marker, matches_region
from outreach.types import EvidenceItem, ParsedPost, SourceClass

HN_PUBLISHER = "news.ycombinator.com"

ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.ashbyhq.com": "ashby",
    "jobs.lever.co": "lever",
}

# Hosts a post links that are never the hiring company's own site: HN itself,
# job boards, social and document hosts, and link shorteners (a shortened
# link says nothing about where it lands, so guessing would research the
# wrong company).
NON_COMPANY_HOSTS = frozenset({
    "news.ycombinator.com", "ycombinator.com", "hn.algolia.com",
    *ATS_HOSTS,
    "github.com", "linkedin.com", "twitter.com", "x.com", "docs.google.com",
    "forms.gle", "calendly.com", "notion.so", "notion.site", "wellfound.com",
    "angel.co", "bit.ly", "lnkd.in", "t.co", "tinyurl.com",
})

# Suffixes that make "Node.js" look like a domain in free text.
_CODE_SUFFIXES = frozenset({"js", "ts", "py", "rb", "rs", "md", "sh", "json", "yaml", "yml"})
_BARE_DOMAIN = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.([a-z]{2,})\b")
_URL = re.compile(r"https?://\S+", re.I)

_MODE_WORDS = r"(?:remote|onsite|on-site|on site|hybrid|in[- ]office)"
_MODE_ONLY = re.compile(rf"^\s*{_MODE_WORDS}(?:\s*(?:[/,&+]|\bor\b|\band\b)\s*{_MODE_WORDS})*\s*$", re.I)
_EMPLOYMENT_WORDS = r"(?:full[- ]?time|part[- ]?time|contract(?:or)?|intern(?:ship)?|permanent|fte)"
_EMPLOYMENT_ONLY = re.compile(
    rf"^\s*{_EMPLOYMENT_WORDS}(?:\s*(?:[/,&+]|\bor\b|\band\b)\s*{_EMPLOYMENT_WORDS})*\s*$", re.I)
_SALARY = re.compile(r"[$€£]|\d\s*k\b", re.I)
_PARENTHETICAL = re.compile(r"\s*\([^)]*(?:\.|http)[^)]*\)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")

_MAX_FIELD = 120


class _PostReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "p":
            self.parts.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _read(html: str) -> _PostReader:
    reader = _PostReader()
    reader.feed(html or "")
    reader.close()
    return reader


def post_text(html: str) -> str:
    """Plain text, one line per paragraph. HN puts the first line before the first <p>."""
    lines = "".join(_read(html).parts).split("\n")
    return "\n".join(line.strip() for line in lines)


def post_links(html: str) -> list[str]:
    """Every href in document order, entities decoded."""
    return _read(html).links


def _host(url: str) -> str | None:
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def _is_non_company(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in NON_COMPANY_HOSTS)


def ats_from_links(links: Sequence[str]) -> tuple[str, str] | None:
    for url in links:
        host = _host(url)
        kind = ATS_HOSTS.get(host or "")
        if kind is None:
            continue
        try:
            segments = [s for s in urlsplit(url).path.split("/") if s]
        except ValueError:
            continue
        if segments:
            return kind, segments[0].lower()
    return None


def company_domain(links: Sequence[str], first_line: str) -> str | None:
    for url in links:
        if not url.lower().startswith(("http://", "https://")):
            continue  # mailto:, relative links, item links
        host = _host(url)
        if host and not _is_non_company(host):
            return canonical_domain(host)
    for match in _BARE_DOMAIN.finditer(_URL.sub(" ", first_line.lower())):
        if match.group(1) in _CODE_SUFFIXES:
            continue
        host = match.group(0)
        if not _is_non_company(host):
            return canonical_domain(host)
    return None


def _location_like(segment: str) -> bool:
    lowered = segment.lower()
    return (matches_region(segment, "US") or _contains_marker(lowered, _NON_US_MARKERS)
            or "remote" in lowered)


def parse_post(html: str) -> ParsedPost | None:
    """The deterministic read of a post, or None when it doesn't follow the convention."""
    first_line = post_text(html).split("\n", 1)[0].strip()
    segments = [s.strip() for s in first_line.split("|")]
    if len(segments) < 2:
        return None
    company = _URL.sub("", _PARENTHETICAL.sub("", segments[0])).strip()

    modes, declared, rest = [], None, []
    for seg in segments[1:]:
        if not seg:
            continue
        if _MODE_ONLY.match(seg):
            modes.append(seg)
        elif _EMPLOYMENT_ONLY.match(seg):
            declared = declared or seg
        elif _SALARY.search(seg):
            continue
        else:
            rest.append(seg)
    location = next((s for s in rest if _location_like(s)), "")
    role = next((s for s in rest if not _location_like(s)), "")

    links = post_links(html)
    domain = company_domain(links, first_line)
    # Mode tags in their own segment ("| REMOTE |") are read like a location
    # field: loosely. Body text stays under workmode's stricter rules.
    work_mode = classify_work_mode(" ".join([location, *modes]).strip(), role, first_line)
    employment = classify_employment(role, first_line, declared)
    if not (company and domain and role and (location or work_mode != "unknown")):
        return None
    return ParsedPost(company, domain, ats_from_links(links), role, location,
                      work_mode, employment)


_MODE_EVIDENCE = {
    "remote": re.compile(r"\bremote\b", re.I),
    "hybrid": re.compile(r"\bhybrid\b", re.I),
    "onsite": re.compile(r"\b(?:on-?site|on site|in[- ]office)\b", re.I),
}
_OTHER_EVIDENCE = re.compile(r"\b(?:contract(?:or)?|part[- ]?time|intern(?:ship)?|temporary)\b", re.I)


def validate_llm_post(candidate: ParsedPost, html: str) -> ParsedPost | None:
    """Accept the model's reading only where the post itself backs every field.

    The model may only copy: a company, role or location that is not in the
    text, a domain that is not one of the post's own links (or written in
    it), or a work mode with no supporting tag rejects the whole post. The
    board is taken from the links, never from the model.
    """
    text = post_text(html).lower()
    links = post_links(html)
    if not candidate.company.strip() or not candidate.role.strip():
        return None
    if len(candidate.company) > _MAX_FIELD or len(candidate.role) > _MAX_FIELD:
        return None
    for value in (candidate.company, candidate.role, candidate.location):
        if value and value.strip().lower() not in text:
            return None
    try:
        domain = canonical_domain(candidate.domain)
    except ValueError:
        return None
    linked = {canonical_domain(h) for h in (_host(u) for u in links) if h}
    if _is_non_company(domain) or (domain not in linked and domain not in text):
        return None
    mode = candidate.work_mode
    if mode != "unknown" and not (mode in _MODE_EVIDENCE and _MODE_EVIDENCE[mode].search(text)):
        return None
    employment = candidate.employment_type
    if employment == "other" and not _OTHER_EVIDENCE.search(text):
        employment = "unknown"
    if not (candidate.location or mode != "unknown"):
        return None
    return replace(candidate, domain=domain, ats=ats_from_links(links),
                   employment_type=employment)


def hint_signals(text: str, posted_at: date) -> list[EvidenceItem]:
    """Free stage hints from a post, shaped as the evidence the classifier reads."""
    hints: list[EvidenceItem] = []
    for sentence in (s.strip() for s in _SENTENCE.split(text)):
        if not sentence:
            continue
        if parse_round(sentence) is not None:
            theme = "funding-round"
        elif parse_headcount_statement(sentence) is not None:
            theme = "headcount-statement"
        else:
            continue
        hints.append(EvidenceItem(None, 0, 0, "", sentence, SourceClass.HN_POST,
                                  HN_PUBLISHER, posted_at, theme))
    return hints
