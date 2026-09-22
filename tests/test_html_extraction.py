"""HTML-to-text extraction, tested against saved real documents.

Spec §12 requires extraction to be tested against saved real documents in
`tests/fixtures/` with a recorded-response fake LLM, asserting both the
extracted claims and the substring guard. These fixtures are real page
shapes (inline markup mid-sentence, HTML entities, script/style noise, a
nav/header/footer around the actual prose) -- not the bare-`<p>`-only pages
the rest of the suite uses, which is exactly what let the un-normalized
substring guard pass CI while failing on every real page.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from outreach.extraction.extract import extract_and_persist
from outreach.extraction.htmltext import html_to_text
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, DocumentRepo
from outreach.store.runs import EvidenceRepo, RunRepo
from outreach.types import SourceClass, SourceDocument

FIXTURES = Path(__file__).parent / "fixtures"


def _setup(tmp_path):
    conn = connect(tmp_path / "t.db")
    company_id = CompanyRepo(conn).upsert("ledgerline.example", "Ledgerline", 48, "careers")
    run_id = RunRepo(conn).create("Backend Engineer", "fintech", "US",
                                  (20, 1000), datetime.now())
    return conn, run_id, company_id


def _document(company_id: int, source_class: SourceClass, published_at) -> SourceDocument:
    return SourceDocument(None, company_id, "https://ledgerline.example/x",
                          source_class, "ledgerline.example", published_at,
                          datetime.now(), 200, "hash")


# --- html_to_text itself ---------------------------------------------------

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


# --- the substring guard over a real saved page -----------------------------

def test_honest_quote_crossing_an_inline_tag_is_accepted(tmp_path):
    """The failure this fixture exists to catch: an honest quote that merely
    crosses a <strong> tag must NOT be rejected by the guard."""
    conn, run_id, company_id = _setup(tmp_path)
    html = FIXTURES.joinpath("careers_page.html").read_text()
    text = html_to_text(html)
    doc = _document(company_id, SourceClass.CAREERS_PAGE, date(2026, 9, 21))
    doc_id = DocumentRepo(conn).insert(doc)

    quote = ("You will own the reconciliation pipeline that currently runs "
             "overnight and is the team’s tightest constraint")
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation is the team's tightest constraint",
        quote=quote, theme="reconciliation-throughput")])
    result = extract_and_persist(run_id, doc, text, llm, EvidenceRepo(conn),
                                 document_id=doc_id)
    assert result.accepted == 1
    assert result.rejected == 0


def test_quote_carrying_literal_markup_is_rejected(tmp_path):
    """A quote that still contains a raw tag was never real prose -- reject it."""
    conn, run_id, company_id = _setup(tmp_path)
    html = FIXTURES.joinpath("careers_page.html").read_text()
    text = html_to_text(html)
    doc = _document(company_id, SourceClass.CAREERS_PAGE, date(2026, 9, 21))
    doc_id = DocumentRepo(conn).insert(doc)

    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation is the tightest constraint",
        quote="You will own the <strong>reconciliation pipeline</strong> that currently runs",
        theme="reconciliation-throughput")])
    result = extract_and_persist(run_id, doc, text, llm, EvidenceRepo(conn),
                                 document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1


def test_honest_quote_with_curly_apostrophe_entity_is_accepted(tmp_path):
    """`&rsquo;`/`&mdash;`/`&amp;` in the source must decode on both sides of
    the guard, or a quote spanning one of them is rejected outright."""
    conn, run_id, company_id = _setup(tmp_path)
    html = FIXTURES.joinpath("eng_blog_post.html").read_text()
    text = html_to_text(html)
    doc = _document(company_id, SourceClass.ENG_BLOG, None)
    doc_id = DocumentRepo(conn).insert(doc)

    quote = ("Our nightly reconciliation job now regularly exceeds its "
             "6-hour window — we’re splitting it into per-ledger workers")
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation job exceeds its window",
        quote=quote, theme="reconciliation-throughput")])
    result = extract_and_persist(run_id, doc, text, llm, EvidenceRepo(conn),
                                 document_id=doc_id)
    assert result.accepted == 1
    assert result.rejected == 0
