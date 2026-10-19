# tests/store/test_runs.py
from datetime import datetime
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.runs import CompanyInfoRepo, PostingRepo, RunContactRepo, RunRepo
from outreach.types import CompanyInfo, Location, PersonRef, PostingRef, RunFilters, Spend


def test_stage_checkpoint_round_trips(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    cid = companies.upsert("foo.com", "Foo", 48, "careers")
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())

    runs.set_stage(rid, cid, "profile", "ok")
    assert runs.stage_status(rid, cid, "profile") == "ok"

    runs.set_stage(rid, cid, "profile", "failed", error="boom")
    assert runs.stage_status(rid, cid, "profile") == "failed"


def test_companies_for_run_lists_every_company_the_run_touched(tmp_path):
    """The complete list of companies a run considered, whatever became of
    each -- the only way a report can surface a company that failed."""
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    runs.set_stage(rid, a, "discover", "ok")
    runs.set_stage(rid, b, "discover", "ok")
    runs.set_stage(rid, a, "evidence", "failed", error="boom")
    assert runs.companies_for_run(rid) == [a, b]


def test_companies_for_run_is_scoped_to_its_own_run(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    r1 = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    r2 = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    runs.set_stage(r1, a, "discover", "ok")
    assert runs.companies_for_run(r1) == [a]
    assert runs.companies_for_run(r2) == []


def test_stage_error_returns_the_recorded_error_text(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    runs.set_stage(rid, a, "evidence", "failed", error="boom")
    assert runs.stage_error(rid, a, "evidence") == "boom"


def test_stage_error_is_none_for_a_stage_never_written_or_without_an_error(tmp_path):
    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    assert runs.stage_error(rid, a, "evidence") is None
    runs.set_stage(rid, a, "evidence", "ok")
    assert runs.stage_error(rid, a, "evidence") is None


def test_clear_for_company_is_scoped_to_one_run_and_one_company(tmp_path):
    """Re-running a stage must drop only that company's rows for that run."""
    from outreach.store.runs import FetchAttemptRepo
    from outreach.types import FetchAttempt, SourceClass

    conn = connect(tmp_path / "t.db")
    companies, runs = CompanyRepo(conn), RunRepo(conn)
    attempts = FetchAttemptRepo(conn)
    a = companies.upsert("a.example", "A", 40, "careers")
    b = companies.upsert("b.example", "B", 60, "careers")
    r1 = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    r2 = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())

    def attempt(company_id):
        return FetchAttempt(company_id, SourceClass.ENG_BLOG,
                            "https://a.example/blog", "ok", 200, 1)

    for run_id, company_id in ((r1, a), (r1, b), (r2, a)):
        attempts.insert(run_id, attempt(company_id))

    assert attempts.clear_for_company(r1, a) == 1

    assert attempts.for_company(r1, a) == []
    assert len(attempts.for_company(r1, b)) == 1   # sibling company untouched
    assert len(attempts.for_company(r2, a)) == 1   # earlier run untouched

    assert attempts.clear_for_company(r1, a) == 0  # idempotent


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
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())

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
    rid = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    return conn, runs, rid, cid


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
    r2 = runs.start("companies", "Backend Engineer", RunFilters(), "hiring", datetime.now())
    postings = PostingRepo(conn)
    postings.insert(r1, a, _posting("https://a.example/1"))
    postings.insert(r2, a, _posting("https://a.example/1"))  # same url, other run: kept
    postings.insert(r1, b, PostingRef("B", "b.example", "SRE", "https://b.example/1", "NYC"))
    assert [p.url for p in postings.for_company(r1, a)] == ["https://a.example/1"]
    assert len(postings.for_company(r2, a)) == 1
    (only,) = postings.for_company(r1, b)
    assert (only.company_name, only.company_domain) == ("B", "b.example")
    assert (only.work_mode, only.employment_type) == ("unknown", "unknown")


def _hn_db(tmp_path):
    conn = connect(tmp_path / "t.db")
    return conn, CompanyRepo(conn), RunRepo(conn)


def test_hn_post_record_round_trips_and_dedupes(tmp_path):
    from datetime import date
    from outreach.store.runs import HNPostRepo
    from outreach.types import HNPostRecord
    conn, companies, runs = _hn_db(tmp_path)
    cid = companies.upsert("acme.io", "Acme", None, None)
    run = runs.start("jobs", "r", RunFilters(), "hiring", datetime(2026, 9, 30))
    repo = HNPostRepo(conn)
    record = HNPostRecord(101, "Ask HN: Who is hiring? (September 2026)", date(2026, 9, 1),
                          "parsed", "kept")
    repo.insert(run, cid, record)
    repo.insert(run, cid, record)
    assert repo.for_company(run, cid) == record
    assert conn.execute("SELECT COUNT(*) FROM run_hn_posts").fetchone()[0] == 1
    assert repo.for_company(run, 999) is None


def test_company_find_canonicalizes(tmp_path):
    conn, companies, runs = _hn_db(tmp_path)
    cid = companies.upsert("acme.io", "Acme", None, None)
    assert companies.find("https://www.Acme.io/careers").id == cid
    assert companies.find("other.io") is None


def test_hn_post_record_keeps_the_parsed_post_and_html_for_resume(tmp_path):
    from datetime import date
    from outreach.store.runs import HNPostRepo
    from outreach.types import HNPostRecord, ParsedPost
    conn, companies, runs = _hn_db(tmp_path)
    cid = companies.upsert("acme.io", "Acme", None, None)
    run = runs.start("jobs", "r", RunFilters(), "hiring", datetime(2026, 9, 30))
    parsed = ParsedPost("Acme", "acme.io", ("lever", "acme"), "Backend Engineer",
                        "Austin, TX", "onsite", "full_time")
    record = HNPostRecord(7, "Ask HN: Who is hiring? (September 2026)", date(2026, 9, 1),
                          "llm", "kept", parsed, "<p>Acme | Backend Engineer")
    HNPostRepo(conn).insert(run, cid, record)
    assert HNPostRepo(conn).for_run(run) == [(cid, record)]
    assert HNPostRepo(conn).for_run(run + 1) == []


def _runs_and_company(tmp_path):
    conn = connect(tmp_path / "t.db")
    return conn, RunRepo(conn), CompanyRepo(conn).upsert("a.example", "A", None, None)


def test_start_records_kind_people_and_filters(tmp_path):
    conn, runs, _ = _runs_and_company(tmp_path)
    filters = RunFilters(("Software Development",), ("51-200",), Location("US", "TX"), ("remote",))
    run_id = runs.start("jobs", "Backend Engineer", filters, "hiring", datetime(2026, 10, 11, 9))
    info = runs.info(run_id)
    assert (info.kind, info.role, info.people, info.filters, info.schema_version, info.spend) == (
        "jobs", "Backend Engineer", "hiring", filters, 4, None)
    assert (info.run_id, info.started_at) == (run_id, datetime(2026, 10, 11, 9))
    assert runs.schema_version(run_id) == 4


def test_start_fills_the_columns_older_readers_still_expect(tmp_path):
    conn, runs, _ = _runs_and_company(tmp_path)
    filters = RunFilters(("Software Development", "Financial Services"),
                         location=Location("CA"))
    run_id = runs.start("companies", "", filters, "sponsors", datetime(2026, 10, 11, 9))
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert (row["role_title"], row["sector"], row["region"], row["headcount_min"],
            row["headcount_max"], row["status"]) == (
        "", "Software Development; Financial Services", "CA", 0, 0, "running")


def test_info_is_none_for_an_unknown_run(tmp_path):
    _, runs, _ = _runs_and_company(tmp_path)
    assert runs.info(999) is None


def test_info_for_a_pre_v4_run_has_no_kind_filters_or_spend(tmp_path):
    conn, runs, _ = _runs_and_company(tmp_path)
    cur = conn.execute("INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max,"
                       " started_at, status, schema_version) "
                       "VALUES ('r','s','US',1,2000,'2026-09-25T10:00:00','done',3)")
    info = runs.info(cur.lastrowid)
    assert (info.kind, info.people, info.filters, info.spend, info.schema_version) == (
        None, None, None, None, 3)
    assert (info.role, info.started_at) == ("r", datetime(2026, 9, 25, 10))


def test_default_filters_round_trip(tmp_path):
    _, runs, _ = _runs_and_company(tmp_path)
    run_id = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    assert runs.info(run_id).filters == RunFilters()


def test_record_spend_adds_up_and_keeps_the_first_balance(tmp_path):
    _, runs, _ = _runs_and_company(tmp_path)
    r = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    runs.record_spend(r, Spend(500, 3, 1, 35))
    runs.record_spend(r, Spend(465, 2, 0, 20))
    assert runs.info(r).spend == Spend(500, 5, 1, 55)


def test_a_balance_read_after_the_first_spend_fills_a_missing_one(tmp_path):
    _, runs, _ = _runs_and_company(tmp_path)
    r = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    runs.record_spend(r, Spend(None, 1, 0, 10))
    runs.record_spend(r, Spend(480, 1, 0, 10))
    assert runs.info(r).spend == Spend(480, 2, 0, 20)


def test_spend_is_scoped_to_its_run(tmp_path):
    _, runs, _ = _runs_and_company(tmp_path)
    a = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    b = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 12))
    runs.record_spend(a, Spend(500, 3, 1, 35))
    assert runs.info(b).spend is None


def test_bought_before_is_per_people_list_and_only_looks_back(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "bought")
    second = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 2))
    assert runs.bought_before(cid, "hiring", second) == (first, datetime(2026, 9, 1))
    assert runs.bought_before(cid, "sponsors", second) is None
    assert runs.bought_before(cid, "hiring", first) is None
    assert runs.bought_before(cid, "hiring", None) == (first, datetime(2026, 9, 1))


def test_bought_before_returns_the_latest_purchase(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    second = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 2))
    third = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 3))
    runs.set_stage(first, cid, "contacts", "bought")
    runs.set_stage(second, cid, "contacts", "bought")
    assert runs.bought_before(cid, "hiring", third) == (second, datetime(2026, 9, 2))
    assert runs.bought_before(cid, "hiring", second) == (first, datetime(2026, 9, 1))


def test_bought_before_is_per_company(tmp_path):
    conn, runs, cid = _runs_and_company(tmp_path)
    other = CompanyRepo(conn).upsert("b.example", "B", None, None)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, other, "contacts", "bought")
    assert runs.bought_before(cid, "hiring", None) is None


def test_only_the_contacts_stage_counts_as_a_purchase(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "discover", "bought")
    assert runs.bought_before(cid, "hiring", None) is None


def test_an_empty_search_is_not_a_purchase(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "empty")
    second = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 2))
    assert runs.bought_before(cid, "hiring", second) is None
    assert runs.came_back_empty_before(cid, "hiring", second) is True


def test_came_back_empty_before_is_per_people_list_and_only_looks_back(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "no_matching_people")
    second = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 2))
    assert runs.came_back_empty_before(cid, "hiring", second) is True
    assert runs.came_back_empty_before(cid, "sponsors", second) is False
    assert runs.came_back_empty_before(cid, "hiring", first) is False
    assert runs.came_back_empty_before(cid, "hiring", None) is True


def test_a_purchase_is_not_an_empty_search(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "bought")
    assert runs.came_back_empty_before(cid, "hiring", None) is False


def test_pre_v4_contacts_count_as_an_earlier_hiring_list(tmp_path):
    conn, runs, cid = _runs_and_company(tmp_path)
    cur = conn.execute("INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max, started_at,"
                       " status, schema_version) VALUES ('r','s','US',1,2000,'2026-09-25T10:00:00','done',3)")
    runs.set_stage(cur.lastrowid, cid, "contacts", "ok")
    assert runs.bought_before(cid, "hiring", None) == (cur.lastrowid, datetime(2026, 9, 25, 10))
    assert runs.bought_before(cid, "sponsors", None) is None


def test_an_ok_contacts_stage_in_a_v4_run_is_not_a_purchase(tmp_path):
    _, runs, cid = _runs_and_company(tmp_path)
    first = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 9, 1))
    runs.set_stage(first, cid, "contacts", "ok")
    assert runs.bought_before(cid, "hiring", None) is None


def test_company_info_round_trips_in_position_order(tmp_path):
    conn, runs, a = _runs_and_company(tmp_path)
    b = CompanyRepo(conn).upsert("b.example", "B", None, None)
    run = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    repo = CompanyInfoRepo(conn)
    full = CompanyInfo(1, "board", "Software Development", "51-200", "unknown", True)
    repo.insert(run, a, full)
    repo.insert(run, b, CompanyInfo(0, "hunter_hiring"))
    assert repo.for_run(run) == [(b, CompanyInfo(0, "hunter_hiring")), (a, full)]
    assert repo.get(run, a) == full
    assert repo.get(run, b) == CompanyInfo(0, "hunter_hiring", None, None, "fits", False)


def test_company_info_ignores_a_second_insert_for_the_same_company(tmp_path):
    conn, runs, a = _runs_and_company(tmp_path)
    run = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    repo = CompanyInfoRepo(conn)
    repo.insert(run, a, CompanyInfo(0, "hn", "Fintech"))
    repo.insert(run, a, CompanyInfo(5, "hunter", "Other", fit="no"))
    assert repo.for_run(run) == [(a, CompanyInfo(0, "hn", "Fintech"))]


def test_company_info_is_scoped_to_its_run(tmp_path):
    conn, runs, a = _runs_and_company(tmp_path)
    r1 = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    r2 = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 12))
    repo = CompanyInfoRepo(conn)
    repo.insert(r1, a, CompanyInfo(0, "board"))
    assert repo.for_run(r2) == []
    assert repo.get(r2, a) is None


def test_run_contacts_keep_their_order(tmp_path):
    conn, runs, cid = _runs_and_company(tmp_path)
    contacts = ContactRepo(conn)
    ids = [contacts.upsert(cid, PersonRef(name, "CTO", None, f"{name.lower()}@a.example",
                                          "verified"), "hunter", datetime(2026, 10, 11))
           for name in ("Ann", "Bo", "Cy")]
    run = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    repo = RunContactRepo(conn)
    # Inserted out of order: the stored position decides, not the insert order.
    repo.add(run, cid, ids[2], 0)
    repo.add(run, cid, ids[0], 1)
    repo.add(run, cid, ids[1], 2)
    repo.add(run, cid, ids[1], 2)  # a resume adds the same person again
    assert [c.full_name for c in repo.for_company(run, cid)] == ["Cy", "Ann", "Bo"]


def test_run_contacts_are_scoped_to_their_run_and_company(tmp_path):
    conn, runs, a = _runs_and_company(tmp_path)
    b = CompanyRepo(conn).upsert("b.example", "B", None, None)
    contacts = ContactRepo(conn)
    ann = contacts.upsert(a, PersonRef("Ann", "CTO", None, "ann@a.example", "verified"),
                          "hunter", datetime(2026, 10, 11))
    bo = contacts.upsert(b, PersonRef("Bo", "CTO", None, "bo@b.example", "verified"),
                         "hunter", datetime(2026, 10, 11))
    r1 = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 11))
    r2 = runs.start("companies", "", RunFilters(), "hiring", datetime(2026, 10, 12))
    repo = RunContactRepo(conn)
    repo.add(r1, a, ann, 0)
    repo.add(r1, b, bo, 0)
    assert [c.full_name for c in repo.for_company(r1, a)] == ["Ann"]
    assert [c.full_name for c in repo.for_company(r1, b)] == ["Bo"]
    assert repo.for_company(r2, a) == []
