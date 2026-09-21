from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    schema = resources.files("outreach.store").joinpath("schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    return conn
