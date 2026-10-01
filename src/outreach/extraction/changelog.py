from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

from outreach.extraction.htmltext import html_to_text

_HEADINGS = frozenset({"h1", "h2", "h3", "h4"})

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
# Exact names and three-letter abbreviations (plus "sept"), never a bare
# prefix: "12 Marketing 2026" must not read as 12 March.
_MONTHS = {name: i for i, full in enumerate(_MONTH_NAMES, 1) for name in (full, full[:3])}
_MONTHS["sept"] = 9
_MONTH = r"(?P<mon>[A-Za-z]{3,9})\.?"

# No trailing \b: a <time datetime="2026-08-01T10:00:00Z"> timestamp has a word
# character (T) right after the day, so a boundary check would reject it.
_ISO = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)")
_MONTH_FIRST = re.compile(rf"\b{_MONTH}\s+(?P<d>\d{{1,2}}),?\s+(?P<y>\d{{4}})\b")
_DAY_FIRST = re.compile(rf"\b(?P<d>\d{{1,2}})\s+{_MONTH},?\s+(?P<y>\d{{4}})\b")


@dataclass(frozen=True)
class ChangelogEntry:
    published_at: date
    heading: str
    text: str


def _parse_date(text: str) -> date | None:
    """The first calendar date in `text`, in any of the formats changelogs use."""
    found: list[tuple[int, date]] = []
    for pattern in (_ISO, _MONTH_FIRST, _DAY_FIRST):
        for m in pattern.finditer(text):
            groups = m.groupdict()
            month = int(groups["m"]) if "m" in groups else _MONTHS.get(groups["mon"].lower())
            if month is None:
                continue
            try:
                found.append((m.start(), date(int(groups["y"]), month, int(groups["d"]))))
            except ValueError:
                continue  # "2026-02-31" is not a date; keep looking
    return min(found, key=lambda pair: pair[0])[1] if found else None


class _HeadingScanner(HTMLParser):
    """Records where each heading sits in the source and any <time datetime> inside it."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self._html = html
        # HTMLParser reports (line, column); turn that into a string offset.
        self._line_starts = [0]
        for m in re.finditer("\n", html):
            self._line_starts.append(m.end())
        self._open: int | None = None
        self._time: list[str] = []
        # (start, end, datetime attributes found inside) for every heading.
        self.headings: list[tuple[int, int, list[str]]] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _HEADINGS:
            self._open = self._offset()
            self._time = []
        elif tag == "time" and self._open is not None:
            value = dict(attrs).get("datetime")
            if value:
                self._time.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADINGS and self._open is not None:
            start = self._offset()
            end = self._html.find(">", start) + 1 or len(self._html)
            self.headings.append((self._open, end, self._time))
            self._open = None


def split_changelog(html: str) -> list[ChangelogEntry]:
    """Dated entries of a changelog page, newest first.

    An entry begins at an h1-h4 whose text (or `<time datetime>`) states a date
    and runs to the next dated heading. Only headings start entries: a date in
    a footer ("(c) 2019") or a paragraph is not a release, so it is ignored. An
    undated heading ("Fixes") inside an entry stays part of that entry's text.
    A page with no dated headings yields no entries -- the gate then has no
    evidence to date, which beats inventing one.
    """
    scanner = _HeadingScanner(html)
    scanner.feed(html)
    scanner.close()

    dated: list[tuple[int, int, str, date]] = []
    for start, end, time_values in scanner.headings:
        heading = " ".join(html_to_text(html[start:end]).split())
        published = next((d for d in (_parse_date(v) for v in time_values) if d), None)
        published = published or _parse_date(heading)
        if published:
            dated.append((start, end, heading, published))

    entries: list[ChangelogEntry] = []
    for i, (_, end, heading, published) in enumerate(dated):
        body_end = dated[i + 1][0] if i + 1 < len(dated) else len(html)
        body = " ".join(html_to_text(html[end:body_end]).split())
        entries.append(ChangelogEntry(published, heading, body))
    # sorted() is stable, so two entries on one day keep their page order.
    return sorted(entries, key=lambda e: e.published_at, reverse=True)
