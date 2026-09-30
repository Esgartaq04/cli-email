# src/outreach/store/migrations.py
from __future__ import annotations

import sqlite3

# schema.sql is never edited: it keeps creating the v1 tables with
# IF NOT EXISTS, and everything after v1 layers on here. That is what lets a
# fresh database and the user's existing data/pipeline.db (contact history
# included) take the same path -- both report user_version 0 after schema.sql
# runs, and both come out the other side at 2.
#
# Each script carries its own BEGIN/COMMIT and sets user_version as its last
# statement, so a crash mid-migration rolls back the added columns together
# with the version bump. Without that, a half-applied ALTER TABLE would make
# the next connect() fail on "duplicate column name" forever.
#
# Index i brings a database to version i + 2 (index 0 is the only way out of
# versions 0 and 1, which are the same schema).
MIGRATIONS: tuple[str, ...] = (
    """
    BEGIN;

    ALTER TABLE companies ADD COLUMN headcount_band TEXT;
    ALTER TABLE companies ADD COLUMN founded_year INTEGER;
    ALTER TABLE companies ADD COLUMN funding_rounds TEXT;
    ALTER TABLE companies ADD COLUMN tags TEXT;
    ALTER TABLE companies ADD COLUMN facts_source TEXT;
    ALTER TABLE companies ADD COLUMN facts_fetched_at TEXT;
    ALTER TABLE companies ADD COLUMN github_org TEXT;

    -- Old runs keep schema_version 1 so the report can tell a bottlenecks-era
    -- run from a findings-era one.
    ALTER TABLE runs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;

    -- Separate from run_companies, whose `stage` column already means the
    -- pipeline step (discover, profile, ...), not the startup stage.
    CREATE TABLE run_company_stages (
      id INTEGER PRIMARY KEY,
      run_id INTEGER NOT NULL REFERENCES runs(id),
      company_id INTEGER NOT NULL REFERENCES companies(id),
      stage TEXT NOT NULL,
      reasons TEXT NOT NULL,
      UNIQUE (run_id, company_id)
    );

    CREATE TABLE run_postings (
      id INTEGER PRIMARY KEY,
      run_id INTEGER NOT NULL REFERENCES runs(id),
      company_id INTEGER NOT NULL REFERENCES companies(id),
      title TEXT NOT NULL,
      url TEXT NOT NULL,
      location TEXT NOT NULL,
      work_mode TEXT NOT NULL,
      employment_type TEXT NOT NULL,
      UNIQUE (run_id, url)
    );

    CREATE TABLE findings (
      id INTEGER PRIMARY KEY,
      run_id INTEGER NOT NULL REFERENCES runs(id),
      company_id INTEGER NOT NULL REFERENCES companies(id),
      theme TEXT NOT NULL,
      claim TEXT NOT NULL,
      summary TEXT NOT NULL,
      corroborated INTEGER NOT NULL,
      passed INTEGER NOT NULL,
      reason TEXT NOT NULL,
      evidence_ids TEXT NOT NULL
    );

    PRAGMA user_version = 2;
    COMMIT;
    """,
)


def migrate(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    # Versions 0 (fresh or pre-versioning) and 1 are the same schema.
    for script in MIGRATIONS[max(version - 1, 0):]:
        try:
            conn.executescript(script)
        except Exception:
            # executescript leaves the transaction open when a statement
            # fails partway; roll it back so the connection is left clean.
            if conn.in_transaction:
                conn.rollback()
            raise
