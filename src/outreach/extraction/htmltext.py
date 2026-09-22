from __future__ import annotations

from html.parser import HTMLParser

# Elements whose content is never prose the LLM should see or quote from.
_SKIP_TEXT_TAGS = frozenset({"script", "style", "noscript", "template"})

# A conservative set of block-level (and line-break) tags. A space is
# inserted at every boundary of one of these so that, e.g.,
# "<p>Foo</p><p>Bar</p>" or "<li>Foo</li><li>Bar</li>" never fuses into
# "FooBar" -- and so a sentence that legitimately ends at a block boundary
# never silently glues onto the next block's first word.
_BLOCK_TAGS = frozenset({
    "p", "div", "br", "hr", "li", "ul", "ol", "tr", "td", "th", "table",
    "section", "article", "header", "footer", "nav", "aside", "main",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "dl", "dt", "dd",
})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TEXT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    """Strip tags and decode entities, leaving plain prose an LLM can quote.

    `convert_charrefs=True` makes `HTMLParser` decode entities (`&rsquo;`,
    `&mdash;`, `&amp;`, numeric refs, ...) into their real characters as it
    parses, so no separate `html.unescape` pass is needed. A space is
    inserted at every block-level boundary so adjacent elements never fuse
    into a sentence nobody wrote (`<p>Foo</p><p>Bar</p>` -> "Foo Bar", not
    "FooBar"). `<script>`/`<style>` content is dropped entirely -- it is
    never prose.

    This is the ONLY place raw HTML is turned into the text that both the
    LLM sees and the substring guard checks against. Both sides of the
    guard must see the same text, or an honest quote is rejected and a
    fabricated one that happens to survive markup is accepted -- see
    `extraction/extract.py`.
    """
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()
