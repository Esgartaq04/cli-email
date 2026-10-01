import sqlite3
from importlib import resources

import pytest

from outreach.store import migrations
from outreach.store.db import connect
from outreach.store.dimensions import CompanyRepo, ContactRepo
from outreach.store.migrations import migrate


def test_connect_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "t.db")
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "contacts", "source_documents", "runs", "run_companies",
            "evidence_items", "bottlenecks", "fetch_attempts", "run_company_stages",
            "run_postings", "findings"} <= names


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


def _v1_database(path):
    """A database exactly as the pre-migration code left it: schema.sql only."""
    conn = sqlite3.connect(path)
    conn.executescript(resources.files("outreach.store").joinpath("schema.sql").read_text())
    return conn


def test_migration_upgrades_a_v1_database_and_keeps_contacted_history(tmp_path):
    conn = _v1_database(tmp_path / "old.db")
    conn.execute("INSERT INTO companies (canonical_domain, name) VALUES ('a.example','A')")
    conn.execute("INSERT INTO contacts (company_id, full_name, title, email_status, contacted_at) "
                 "VALUES (1,'Ann','CTO','verified','2026-09-01T00:00:00')")
    conn.execute("INSERT INTO runs (role_title, sector, region, headcount_min, headcount_max, "
                 "started_at, status) VALUES ('r','s','US',1,2,'2026-09-01T00:00:00','done')")
    conn.commit()
    conn.close()
    new = connect(tmp_path / "old.db")
    assert new.execute("PRAGMA user_version").fetchone()[0] == 2
    assert ContactRepo(new).for_company(1)[0].contacted_at is not None
    # The pre-existing company reads back with the new fields empty, and the
    # pre-existing run is tagged as a v1 run.
    company = CompanyRepo(new).get(1)
    assert (company.funding_rounds, company.tags, company.founded_year) == ((), (), None)
    assert new.execute("SELECT schema_version FROM runs").fetchone()[0] == 1


def test_connect_is_idempotent_across_versions(tmp_path):
    connect(tmp_path / "t.db").close()
    conn = connect(tmp_path / "t.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migrate_applies_nothing_once_current(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    monkeypatch.setattr(migrations, "MIGRATIONS", ("THIS IS NOT SQL;",))
    migrate(conn)  # would raise if the script were (re)applied


def test_failed_migration_rolls_back_columns_and_version(tmp_path, monkeypatch):
    """A crash partway must not leave columns added but user_version unset,
    or the next connect would die on 'duplicate column name' forever."""
    conn = _v1_database(tmp_path / "old.db")
    broken = migrations.MIGRATIONS[0].replace(
        "PRAGMA user_version = 2;", "CREATE TABLE findings (x);")  # already exists
    monkeypatch.setattr(migrations, "MIGRATIONS", (broken,))
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn)
    assert not conn.in_transaction
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(companies)")}
    assert "headcount_band" not in cols
    # ...and the real migration still applies cleanly afterwards.
    monkeypatch.undo()
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
