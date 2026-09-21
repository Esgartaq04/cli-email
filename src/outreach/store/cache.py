from __future__ import annotations

import hashlib
from pathlib import Path


class DocumentCache:
    """Content-addressed store for fetched documents.

    Keeps SQLite small, and the files double as test fixtures — the gate's
    tests read the same bytes a real run saw.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, content_hash: str) -> Path:
        return self.root / content_hash[:2] / content_hash

    def store(self, content: bytes) -> str:
        content_hash = hashlib.sha256(content).hexdigest()
        target = self.path_for(content_hash)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return content_hash

    def has(self, content_hash: str) -> bool:
        return self.path_for(content_hash).exists()

    def read(self, content_hash: str) -> str:
        return self.path_for(content_hash).read_text(encoding="utf-8", errors="replace")
