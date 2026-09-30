# tests/store/test_runs.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo
from outreach.store.runs import FindingRepo, PostingRepo, RunRepo
from outreach.types import Finding, PostingRef, Stage, StageResult


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


def test_a_class_filtered_clear_removes_only_the_intended_classes(tmp_path):
    """Each stage clears its own share of the log in one statement, so a crash
    can never lose the other stage's rows, and the survivors keep their order."""
    from outreach.store.runs import FetchAttemptRepo
    from outreach.types import FetchAttempt, SourceClass

    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    attempts = FetchAttemptRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime.now())

    classes = (SourceClass.CAREERS_PAGE, SourceClass.HOMEPAGE, SourceClass.ENG_BLOG,
               SourceClass.HOMEPAGE)

    def fill():
        for i, cls in enumerate(classes):
            attempts.insert(rid, FetchAttempt(a, cls, f"https://a.example/{i}", "ok", 200, 0))
        attempts.insert(rid, FetchAttempt(b, SourceClass.HOMEPAGE, "https://b.example/",
                                          "ok", 200, 0))

    fill()
    homepage = frozenset({SourceClass.HOMEPAGE})
    assert attempts.clear_for_company(rid, a, keep=homepage) == 2
    assert [x.url for x in attempts.for_company(rid, a)] == [
        "https://a.example/1", "https://a.example/3"]

    attempts.clear_for_company(rid, a)
    fill()
    assert attempts.clear_for_company(rid, a, only=homepage) == 2
    assert [x.url for x in attempts.for_company(rid, a)] == [
        "https://a.example/0", "https://a.example/2"]

    # The sibling company's homepage row was never in scope.
    assert [x.url for x in attempts.for_company(rid, b)] == [
        "https://b.example/", "https://b.example/"]


def _run_and_company(tmp_path, domain="a.example"):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    cid = companies.upsert(domain, "A", 40, "careers")
    rid = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime.now())
    return conn, runs, rid, cid


def test_new_runs_are_schema_version_2(tmp_path):
    _, runs, rid, _ = _run_and_company(tmp_path)
    assert runs.schema_version(rid) == 2


def test_stage_round_trips_and_upserts(tmp_path):
    _, runs, rid, cid = _run_and_company(tmp_path)
    assert runs.company_stage(rid, cid) is None
    runs.set_company_stage(rid, cid, StageResult(Stage.GROWTH, ("~120 employees",)))
    assert runs.company_stage(rid, cid) == StageResult(Stage.GROWTH, ("~120 employees",))
    runs.set_company_stage(rid, cid, StageResult(Stage.EXPANSION, ("x", "y")))
    assert runs.company_stage(rid, cid) == StageResult(Stage.EXPANSION, ("x", "y"))
    # The startup stage is its own table: the pipeline-step rows are untouched.
    assert runs.companies_for_run(rid) == []


def test_stage_reasons_survive_commas_and_empty(tmp_path):
    _, runs, rid, cid = _run_and_company(tmp_path)
    runs.set_company_stage(rid, cid, StageResult(Stage.UNKNOWN, ("a, b", "c")))
    assert runs.company_stage(rid, cid).reasons == ("a, b", "c")
    runs.set_company_stage(rid, cid, StageResult(Stage.UNKNOWN, ()))
    assert runs.company_stage(rid, cid) == StageResult(Stage.UNKNOWN, ())


def test_stage_is_scoped_per_run(tmp_path):
    conn, runs, r1, cid = _run_and_company(tmp_path)
    r2 = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime.now())
    runs.set_company_stage(r1, cid, StageResult(Stage.SEED_STARTUP, ("s",)))
    assert runs.company_stage(r2, cid) is None


def _posting(url="https://a.example/jobs/1"):
    return PostingRef("A", "a.example", "Backend Engineer", url, "Remote, US",
                      "remote", "full_time")


def test_postings_ignore_duplicates_within_a_run(tmp_path):
    conn, runs, rid, cid = _run_and_company(tmp_path)
    postings = PostingRepo(conn)
    p = _posting()
    postings.insert(rid, cid, p)
    postings.insert(rid, cid, p)
    assert postings.for_company(rid, cid) == [p]


def test_postings_are_scoped_by_run_and_company(tmp_path):
    conn, runs, r1, a = _run_and_company(tmp_path)
    b = CompanyRepo(conn).upsert("b.example", "B", 50, "careers")
    r2 = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime.now())
    postings = PostingRepo(conn)
    postings.insert(r1, a, _posting("https://a.example/1"))
    postings.insert(r2, a, _posting("https://a.example/1"))  # same url, other run: kept
    postings.insert(r1, b, PostingRef("B", "b.example", "SRE", "https://b.example/1", "NYC"))
    assert [p.url for p in postings.for_company(r1, a)] == ["https://a.example/1"]
    assert len(postings.for_company(r2, a)) == 1
    (only,) = postings.for_company(r1, b)
    assert (only.company_name, only.company_domain) == ("B", "b.example")
    assert (only.work_mode, only.employment_type) == ("unknown", "unknown")


def _finding(company_id, claim="slow deploys", passed=True, ids=(3, 7)):
    return Finding(None, company_id, "infra", claim, "summary", True, passed,
                   "reason", ids)


def test_findings_round_trip_and_clear(tmp_path):
    conn, runs, r1, a = _run_and_company(tmp_path)
    b = CompanyRepo(conn).upsert("b.example", "B", 50, "careers")
    r2 = runs.create("Backend Engineer", "fintech", "US", (1, 2000), datetime.now())
    findings = FindingRepo(conn)

    fid = findings.insert(r1, _finding(a))
    findings.insert(r1, _finding(a, "flaky tests", passed=False, ids=()))
    findings.insert(r1, _finding(b))
    findings.insert(r2, _finding(a))

    first, second = findings.for_company(r1, a)
    assert first == Finding(fid, a, "infra", "slow deploys", "summary", True, True,
                            "reason", (3, 7))
    assert (second.passed, second.evidence_ids) == (False, ())
    assert [f.company_id for f in findings.for_run(r1)] == [a, a, b]

    assert findings.clear_for_company(r1, a) == 2
    assert findings.for_company(r1, a) == []
    assert len(findings.for_company(r1, b)) == 1   # sibling company untouched
    assert len(findings.for_company(r2, a)) == 1   # other run untouched
    assert findings.clear_for_company(r1, a) == 0  # idempotent
