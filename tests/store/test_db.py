from outreach.store.db import connect


def test_connect_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "contacts", "source_documents", "runs", "run_companies",
            "evidence_items", "bottlenecks", "fetch_attempts"} <= names


def test_connect_is_idempotent(tmp_path):
    path = tmp_path / "t.db"
    connect(path).close()
    conn = connect(path)
    assert conn.execute("SELECT count(*) c FROM companies").fetchone()["c"] == 0


def test_company_domain_is_unique(tmp_path):
    import sqlite3
    import pytest
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A2')")
