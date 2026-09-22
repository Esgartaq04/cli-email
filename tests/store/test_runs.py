# tests/store/test_runs.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo
from outreach.store.runs import RunRepo


def test_stage_checkpoint_round_trips(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    cid = companies.upsert("foo.com", "Foo", 48, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())

    runs.set_stage(rid, cid, "profile", "ok")
    assert runs.stage_status(rid, cid, "profile") == "ok"

    runs.set_stage(rid, cid, "profile", "failed", error="boom")
    assert runs.stage_status(rid, cid, "profile") == "failed"


def test_companies_for_run_lists_every_company_the_run_touched(tmp_path):
    """The complete list of companies a run researched, whether or not a
    company ever earned a bottlenecks row -- the only way a report can
    surface a company whose research failed entirely."""
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    runs.set_stage(rid, a, "discover", "ok")
    runs.set_stage(rid, b, "discover", "ok")
    runs.set_stage(rid, a, "evidence", "failed", error="boom")
    assert runs.companies_for_run(rid) == [a, b]


def test_companies_for_run_is_scoped_to_its_own_run(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    r1 = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    r2 = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    runs.set_stage(r1, a, "discover", "ok")
    assert runs.companies_for_run(r1) == [a]
    assert runs.companies_for_run(r2) == []


def test_stage_error_returns_the_recorded_error_text(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    runs.set_stage(rid, a, "evidence", "failed", error="boom")
    assert runs.stage_error(rid, a, "evidence") == "boom"


def test_stage_error_is_none_for_a_stage_never_written_or_without_an_error(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    assert runs.stage_error(rid, a, "evidence") is None
    runs.set_stage(rid, a, "evidence", "ok")
    assert runs.stage_error(rid, a, "evidence") is None


def test_pending_companies_excludes_completed_ones(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    runs.set_stage(rid, a, "discover", "ok")
    runs.set_stage(rid, b, "discover", "ok")
    runs.set_stage(rid, a, "contacts", "ok")
    assert runs.pending_companies(rid, "contacts") == [b]


def test_clear_for_company_is_scoped_to_one_run_and_one_company(tmp_path):
    """Re-running a stage must drop only that company's rows for that run."""
    from datetime import date

    from outreach.store.dimensions import DocumentRepo
    from outreach.store.runs import EvidenceRepo, FetchAttemptRepo
    from outreach.types import EvidenceItem, FetchAttempt, SourceClass, SourceDocument

    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    evidence, attempts = EvidenceRepo(conn), FetchAttemptRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    doc_id = DocumentRepo(conn).insert(SourceDocument(
        None, a, "https://a.example/blog", SourceClass.ENG_BLOG, "a.example",
        date(2026, 9, 1), datetime.now(), 200, "hash"))
    r1 = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())
    r2 = runs.create("Backend Engineer", "fintech", "US", (20, 1000), datetime.now())

    def item(company_id):
        return EvidenceItem(None, company_id, doc_id, "claim", "quote",
                            SourceClass.ENG_BLOG, "a.example", date(2026, 9, 1), "t")

    def attempt(company_id):
        return FetchAttempt(company_id, SourceClass.ENG_BLOG,
                            "https://a.example/blog", "ok", 200, 1)

    for run_id, company_id in ((r1, a), (r1, b), (r2, a)):
        evidence.insert(run_id, item(company_id))
        attempts.insert(run_id, attempt(company_id))

    assert evidence.clear_for_company(r1, a) == 1
    assert attempts.clear_for_company(r1, a) == 1

    assert evidence.for_company(r1, a) == []
    assert attempts.for_company(r1, a) == []
    assert len(evidence.for_company(r1, b)) == 1   # sibling company untouched
    assert len(attempts.for_company(r1, b)) == 1
    assert len(evidence.for_company(r2, a)) == 1   # earlier run untouched
    assert len(attempts.for_company(r2, a)) == 1

    assert evidence.clear_for_company(r1, a) == 0  # idempotent
