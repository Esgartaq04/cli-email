from datetime import datetime, date

from outreach.extraction.extract import extract_and_persist, normalize_ws
from outreach.llm.base import RawClaim
from outreach.llm.fake import FakeLLM
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, DocumentRepo
from outreach.store.runs import EvidenceRepo, RunRepo
from outreach.types import SourceClass, SourceDocument

DOC_TEXT = """
Our nightly   reconciliation job now regularly
exceeds its 6-hour window. We are splitting it into per-ledger workers.
"""


def setup(tmp_path):
    conn = connect(tmp_path / "t.db")
    company_id = CompanyRepo(conn).upsert("acme.example", "Acme", 48, "careers")
    run_id = RunRepo(conn).create("Backend Engineer", "fintech", "US",
                                  (20, 1000), datetime.now())
    doc = SourceDocument(None, company_id, "https://acme.example/blog/1",
                         SourceClass.ENG_BLOG, "acme.example", date(2026, 9, 2),
                         datetime.now(), 200, "hash1")
    doc_id = DocumentRepo(conn).insert(doc)
    return conn, run_id, company_id, doc_id, doc


def test_normalize_ws_collapses_runs_and_newlines():
    assert normalize_ws("a   b\n\tc ") == "a b c"


def test_quote_with_different_whitespace_still_matches(tmp_path):
    """The failure this prevents: HTML text extraction collapses whitespace,
    so a naive `in` check rejects every claim and the pipeline goes silent."""
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation exceeds its window",
        quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
        theme="reconciliation-throughput",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 1
    assert result.rejected == 0
    assert len(repo.for_company(run_id, company_id)) == 1


def test_invented_quote_is_rejected_and_not_persisted(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="they are migrating to Kubernetes",
        quote="We are migrating the whole platform to Kubernetes this quarter.",
        theme="infra-migration",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1
    assert repo.for_company(run_id, company_id) == []


def test_empty_quote_is_rejected_and_not_persisted(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation is slow", quote="", theme="reconciliation-throughput",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1
    assert repo.for_company(run_id, company_id) == []


def test_whitespace_only_quote_is_rejected_and_not_persisted(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation is slow", quote="   \n\t  ", theme="reconciliation-throughput",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1
    assert repo.for_company(run_id, company_id) == []


def test_a_punctuation_only_theme_is_rejected_and_not_persisted(tmp_path):
    """"", "—" and "..." all normalize to an empty theme. A claim like this
    must never be persisted -- it would land in an unkeyed cluster with
    every other theme-less claim from any company, merging unrelated
    evidence into something that can pass the gate."""
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[RawClaim(
        claim="reconciliation exceeds its window",
        quote="Our nightly reconciliation job now regularly exceeds its 6-hour window.",
        theme="—",
    )])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert result.accepted == 0
    assert result.rejected == 1
    assert repo.for_company(run_id, company_id) == []


def test_mixed_batch_keeps_only_real_quotes(tmp_path):
    conn, run_id, company_id, doc_id, doc = setup(tmp_path)
    llm = FakeLLM(claims=[
        RawClaim("real", "splitting it into per-ledger workers", "reconciliation-throughput"),
        RawClaim("fake", "we have no engineers left", "attrition"),
    ])
    repo = EvidenceRepo(conn)
    result = extract_and_persist(run_id, doc, DOC_TEXT, llm, repo, document_id=doc_id)
    assert (result.accepted, result.rejected) == (1, 1)
    assert repo.for_company(run_id, company_id)[0].claim == "real"
