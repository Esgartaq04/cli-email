"""HTML-to-text, tested against saved real documents.

The fixtures are real page shapes (inline markup mid-sentence, HTML entities,
script/style noise, a nav/header/footer around the actual prose) -- not the
bare-`<p>`-only pages a hand-written test reaches for, which is how a reader
that only copes with those passes CI and then fails on every real page.
"""
from __future__ import annotations

from pathlib import Path

from outreach.sources.jobboards.htmltext import html_to_text

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_script_and_style_content_is_dropped():
    html = FIXTURES.joinpath("careers_page.html").read_text()
    text = html_to_text(html)
    assert "gtag" not in text
    assert "noscript-content-must-never-appear" not in text
    assert "background: #111" not in text


def test_inline_markup_mid_sentence_does_not_fuse_or_leak_tags():
    html = FIXTURES.joinpath("careers_page.html").read_text()
    text = html_to_text(html)
    assert "<strong>" not in text and "</strong>" not in text
    assert ("the reconciliation pipeline that currently" in text
            or "own the reconciliation pipeline that currently" in text)


def test_entities_decode_to_real_characters():
    html = FIXTURES.joinpath("eng_blog_post.html").read_text()
    text = html_to_text(html)
    assert "&rsquo;" not in text and "&mdash;" not in text and "&amp;" not in text
    assert "’" in text or "'" in text  # rsquo decoded
    assert "—" in text or "--" in text or "-" in text  # mdash decoded, some dash survives


def test_block_boundaries_get_a_separating_space():
    text = html_to_text("<p>Foo</p><p>Bar</p>")
    assert "FooBar" not in text
    assert "Foo Bar" in text or "Foo" in text and "Bar" in text
