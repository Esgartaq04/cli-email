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
