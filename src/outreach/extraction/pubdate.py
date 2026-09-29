from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser

_JSON_LD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')

_META_KEYS = frozenset({
    "article:published_time", "og:article:published_time", "datepublished",
    "publish-date", "pubdate",
})


def _to_date(raw: str) -> date | None:
    # ISO 8601 date or timestamp: the calendar day is the first ten characters.
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


class _DateSniffer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: list[str] = []
        self.time_values: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop")).lower()
            if key in _META_KEYS and a.get("content"):
                self.meta.append(a["content"])
        elif tag == "time" and a.get("datetime"):
            self.time_values.append(a["datetime"])


def parse_published_date(html: str) -> date | None:
    """The publication date a page states about itself, or None.

    Structured metadata (JSON-LD, meta tags) wins. A bare `<time>` element
    counts only when the page has exactly one distinct value: a listing page
    carries one per card, and picking among them would be a guess. None is
    the honest answer when the page does not say — the gate then treats the
    evidence as not fresh rather than inventing a date for it.
    """
    for raw in _JSON_LD_DATE.findall(html):
        parsed = _to_date(raw)
        if parsed:
            return parsed

    sniffer = _DateSniffer()
    sniffer.feed(html)
    sniffer.close()

    for raw in sniffer.meta:
        parsed = _to_date(raw)
        if parsed:
            return parsed

    distinct = {d for d in (_to_date(v) for v in sniffer.time_values) if d}
    if len(distinct) == 1:
        return next(iter(distinct))
    return None
