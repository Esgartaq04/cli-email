# src/outreach/store/runs.py
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime

from outreach.types import (
    Bottleneck,
    EvidenceItem,
    FetchAttempt,
    Finding,
    HNPostRecord,
    PostingRef,
    SourceClass,
    Stage,
    StageResult,
)


class RunRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, role_title: str, sector: str, region: str,
               band: tuple[int, int], started_at: datetime) -> int:
        cur = self.conn.execute(
            """INSERT INTO runs (role_title, sector, region, headcount_min,
                                 headcount_max, started_at, status, schema_version)
               VALUES (?, ?, ?, ?, ?, ?, 'running', 2)""",
            (role_title, sector, region, band[0], band[1], started_at.isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def schema_version(self, run_id: int) -> int:
        """1 for a run recorded before findings existed (its report reads
        `bottlenecks`), 2 for every run created since."""
        row = self.conn.execute(
            "SELECT schema_version FROM runs WHERE id = ?", (run_id,)).fetchone()
        return int(row["schema_version"])

    def window(self, run_id: int) -> tuple[datetime, datetime | None]:
        """When the run started, and when it finished (None while unfinished)."""
        row = self.conn.execute(
            "SELECT started_at, finished_at FROM runs WHERE id = ?", (run_id,)).fetchone()
        finished = row["finished_at"]
        return (datetime.fromisoformat(row["started_at"]),
                datetime.fromisoformat(finished) if finished else None)

    def set_company_stage(self, run_id: int, company_id: int, result: StageResult) -> None:
        # Not `set_stage`: that one records the pipeline step in run_companies.
        self.conn.execute(
            """INSERT INTO run_company_stages (run_id, company_id, stage, reasons)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(run_id, company_id) DO UPDATE SET
                 stage = excluded.stage, reasons = excluded.reasons""",
            (run_id, company_id, result.stage.value, json.dumps(list(result.reasons))),
        )
        self.conn.commit()

    def company_stage(self, run_id: int, company_id: int) -> StageResult | None:
        row = self.conn.execute(
            "SELECT stage, reasons FROM run_company_stages WHERE run_id = ? AND company_id = ?",
            (run_id, company_id)).fetchone()
        if row is None:
            return None
        return StageResult(Stage(row["stage"]), tuple(json.loads(row["reasons"])))

    def finish(self, run_id: int, when: datetime) -> None:
        self.conn.execute("UPDATE runs SET finished_at = ?, status = 'done' WHERE id = ?",
                          (when.isoformat(), run_id))
        self.conn.commit()

    def researched_since(self, company_id: int, since: datetime,
                         exclude_run_id: int) -> bool:
        """Whether another run started at or after `since` finished this
        company's research. Used to avoid paying twice for a company HN
        lists every month."""
        row = self.conn.execute(
            """SELECT 1 FROM run_companies rc JOIN runs r ON r.id = rc.run_id
               WHERE rc.company_id = ? AND rc.stage = 'evidence' AND rc.status = 'ok'
                 AND rc.run_id != ? AND r.started_at >= ?
               LIMIT 1""",
            (company_id, exclude_run_id, since.isoformat())).fetchone()
        return row is not None

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


class PostingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, company_id: int, posting: PostingRef) -> None:
        # company_name/company_domain are not stored: they are the company's,
        # and `for_company` joins them back so they cannot drift.
        self.conn.execute(
            """INSERT OR IGNORE INTO run_postings (run_id, company_id, title, url,
                                                   location, work_mode, employment_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, company_id, posting.title, posting.url, posting.location,
             posting.work_mode, posting.employment_type),
        )
        self.conn.commit()

    def for_company(self, run_id: int, company_id: int) -> list[PostingRef]:
        rows = self.conn.execute(
            """SELECT p.*, c.name AS company_name, c.canonical_domain AS company_domain
               FROM run_postings p JOIN companies c ON c.id = p.company_id
               WHERE p.run_id = ? AND p.company_id = ? ORDER BY p.id""",
            (run_id, company_id)).fetchall()
        return [
            PostingRef(r["company_name"], r["company_domain"], r["title"], r["url"],
                       r["location"], r["work_mode"], r["employment_type"])
            for r in rows
        ]


class FindingRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, f: Finding) -> int:
        cur = self.conn.execute(
            """INSERT INTO findings (run_id, company_id, theme, claim, summary,
                                     corroborated, passed, reason, evidence_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, f.company_id, f.theme, f.claim, f.summary, int(f.corroborated),
             int(f.passed), f.reason, ",".join(str(i) for i in f.evidence_ids)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @staticmethod
    def _row_to_finding(r: sqlite3.Row) -> Finding:
        return Finding(
            r["id"], r["company_id"], r["theme"], r["claim"], r["summary"],
            bool(r["corroborated"]), bool(r["passed"]), r["reason"],
            tuple(int(x) for x in r["evidence_ids"].split(",") if x),
        )

    def for_company(self, run_id: int, company_id: int) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE run_id = ? AND company_id = ? ORDER BY id",
            (run_id, company_id)).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def for_run(self, run_id: int) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [self._row_to_finding(r) for r in rows]

    def clear_for_company(self, run_id: int, company_id: int) -> int:
        """Drop this run's findings for one company; return the rows removed.

        Same reason as `EvidenceRepo.clear_for_company`: `findings` has no
        unique key, so re-running a company's synthesis after a partial
        failure would otherwise stack a second copy of every finding.
        """
        cur = self.conn.execute(
            "DELETE FROM findings WHERE run_id = ? AND company_id = ?",
            (run_id, company_id))
        self.conn.commit()
        return int(cur.rowcount)


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

    def clear_for_company(self, run_id: int, company_id: int,
                          only: frozenset[SourceClass] | None = None,
                          keep: frozenset[SourceClass] | None = None) -> int:
        """Drop this run's fetch attempts for one company; return rows removed.

        The companion to `EvidenceRepo.clear_for_company`: a re-run refetches
        every surface, so the old attempt rows would otherwise accumulate and
        the diagnostics would show one surface tried twice as often as it was.

        Two stages write this log -- enrich (the homepage) and evidence (every
        surface) -- and each re-derives only its own share on a resume, so
        `only` limits the delete to some classes and `keep` spares some. It is
        one DELETE: reading the survivors out and writing them back would lose
        them to a crash in between, and hand them new ids.
        """
        sql = "DELETE FROM fetch_attempts WHERE run_id = ? AND company_id = ?"
        params: list[object] = [run_id, company_id]
        for classes, op in ((only, "IN"), (keep, "NOT IN")):
            if classes is not None:
                marks = ", ".join("?" * len(classes))
                sql += f" AND source_class {op} ({marks})"
                params.extend(sorted(c.value for c in classes))
        cur = self.conn.execute(sql, params)
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


class HNPostRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert(self, run_id: int, company_id: int, record: HNPostRecord) -> None:
        # A resume re-runs discovery; the post is the same post.
        self.conn.execute(
            """INSERT OR IGNORE INTO run_hn_posts (run_id, company_id, item_id,
                   thread_title, posted_at, parse_method, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, company_id, record.item_id, record.thread_title,
             record.posted_at.isoformat(), record.parse_method, record.status))
        self.conn.commit()

    def for_company(self, run_id: int, company_id: int) -> HNPostRecord | None:
        row = self.conn.execute(
            "SELECT * FROM run_hn_posts WHERE run_id = ? AND company_id = ? "
            "ORDER BY id LIMIT 1", (run_id, company_id)).fetchone()
        if row is None:
            return None
        return HNPostRecord(int(row["item_id"]), row["thread_title"],
                            date.fromisoformat(row["posted_at"]), row["parse_method"],
                            row["status"])
