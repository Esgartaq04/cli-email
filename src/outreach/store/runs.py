# src/outreach/store/runs.py
from __future__ import annotations

import sqlite3
from datetime import datetime

from outreach.types import Bottleneck, EvidenceItem, FetchAttempt, SourceClass


class RunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, role_title: str, sector: str, region: str,
               band: tuple[int, int], started_at: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (role_title, sector, region, headcount_min,
                                 headcount_max, started_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'running')""",
            (role_title, sector, region, band[0], band[1], started_at.isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def finish(self, run_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE runs SET finished_at = ?, status = 'done' WHERE id = ?",
                          (when.isoformat(), run_id))
        self.conn.commit()

    def set_stage(self, run_id: int, company_id: int, stage: str,
                  status: str, error: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO run_companies (run_id, company_id, stage, status, error)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id, company_id, stage) DO UPDATE SET
                 status = excluded.status, error = excluded.error""",
            (run_id, company_id, stage, status, error),
        )
        self.conn.commit()

    def stage_status(self, run_id: int, company_id: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM run_companies WHERE run_id=? AND company_id=? AND stage=?",
            (run_id, company_id, stage)).fetchone()
        return row["status"] if row else None

    def companies_for_run(self, run_id: int) -> list[int]:
        """Every company this run ever touched, in company-id order.

        `run_companies` has a row for a company the moment `discover`
        checkpoints it, whether or not it ever earns a `bottlenecks` row --
        so this is the only complete list of companies a run researched.
        Without it, a company whose every surface failed has no trace left
        in the report at all: `bottlenecks` never gets a row for it (by
        design -- see runner.py), and nothing else reads this table.
        """
        rows = self.conn.execute(
            "SELECT DISTINCT company_id FROM run_companies WHERE run_id = ? "
            "ORDER BY company_id", (run_id,)).fetchall()
        return [int(r["company_id"]) for r in rows]

    def stage_error(self, run_id: int, company_id: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT error FROM run_companies WHERE run_id=? AND company_id=? AND stage=?",
            (run_id, company_id, stage)).fetchone()
        return row["error"] if row else None

    def pending_companies(self, run_id: int, stage: str) -> list[int]:
        """Companies that reached the run but have no successful row for `stage`."""
        rows = self.conn.execute(
            """SELECT DISTINCT company_id FROM run_companies rc WHERE run_id = ?
               AND NOT EXISTS (
                 SELECT 1 FROM run_companies done
                 WHERE done.run_id = rc.run_id AND done.company_id = rc.company_id
                   AND done.stage = ? AND done.status = 'ok')
               ORDER BY company_id""",
            (run_id, stage)).fetchall()
        return [int(r["company_id"]) for r in rows]


class EvidenceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, item: EvidenceItem) -> int:
        cur = self.conn.execute(
            """INSERT INTO evidence_items (run_id, company_id, source_document_id, claim,
                                           quote, source_class, publisher_domain,
                                           published_at, theme)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, item.company_id, item.source_document_id, item.claim, item.quote,
             item.source_class.value, item.publisher_domain,
             item.published_at.isoformat() if item.published_at else None, item.theme),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def clear_for_company(self, run_id: int, company_id: int) -> int:
        """Drop this run's evidence for one company; return the rows removed.

        Re-running a company's evidence stage after a partial failure
        re-extracts every surface, and `evidence_items` has no unique key to
        absorb that. Without a delete path a resume silently doubles the
        rows, so the report cites the same quote twice and `quotes_accepted`
        is inflated. Scoped to (run_id, company_id): no other company's
        evidence and no earlier run is touched.
        """
        cur = self.conn.execute(
            "DELETE FROM evidence_items WHERE run_id = ? AND company_id = ?",
            (run_id, company_id))
        self.conn.commit()
        return int(cur.rowcount)

    def for_company(self, run_id: int, company_id: int) -> list[EvidenceItem]:
        rows = self.conn.execute(
            "SELECT * FROM evidence_items WHERE run_id=? AND company_id=? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [
            EvidenceItem(
                r["id"], r["company_id"], r["source_document_id"], r["claim"], r["quote"],
                SourceClass(r["source_class"]), r["publisher_domain"],
                datetime.fromisoformat(r["published_at"]).date() if r["published_at"] else None,
                r["theme"],
            ) for r in rows
        ]


class BottleneckRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, b: Bottleneck) -> int:
        cur = self.conn.execute(
            """INSERT INTO bottlenecks (run_id, company_id, claim, summary, passed,
                                        reason, evidence_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, b.company_id, b.claim, b.summary, int(b.passed), b.reason,
             ",".join(str(i) for i in b.evidence_ids)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def for_run(self, run_id: int) -> list[Bottleneck]:
        rows = self.conn.execute(
            "SELECT * FROM bottlenecks WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [
            Bottleneck(
                r["id"], r["company_id"], r["claim"], r["summary"], bool(r["passed"]),
                r["reason"],
                tuple(int(x) for x in r["evidence_ids"].split(",") if x),
            ) for r in rows
        ]


class FetchAttemptRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, attempt: FetchAttempt) -> None:
        self.conn.execute(
            """INSERT INTO fetch_attempts (run_id, company_id, source_class, url,
                                           outcome, http_status, document_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, attempt.company_id, attempt.source_class.value, attempt.url,
             attempt.outcome, attempt.http_status, attempt.document_count),
        )
        self.conn.commit()

    def clear_for_company(self, run_id: int, company_id: int) -> int:
        """Drop this run's fetch attempts for one company; return rows removed.

        The companion to `EvidenceRepo.clear_for_company`: a re-run refetches
        every surface, so the old attempt rows would otherwise accumulate and
        the diagnostics would show one surface tried twice as often as it was.
        """
        cur = self.conn.execute(
            "DELETE FROM fetch_attempts WHERE run_id = ? AND company_id = ?",
            (run_id, company_id))
        self.conn.commit()
        return int(cur.rowcount)

    def for_company(self, run_id: int, company_id: int) -> list[FetchAttempt]:
        rows = self.conn.execute(
            "SELECT * FROM fetch_attempts WHERE run_id=? AND company_id=? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [
            FetchAttempt(r["company_id"], SourceClass(r["source_class"]), r["url"],
                         r["outcome"], r["http_status"], r["document_count"])
            for r in rows
        ]
