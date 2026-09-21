from __future__ import annotations

import re
from dataclasses import dataclass

from outreach.llm.base import LLMClient
from outreach.store.runs import EvidenceRepo
from outreach.types import EvidenceItem, SourceDocument

_WS = re.compile(r"\s+")


def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends.

    Applied to BOTH sides of the substring guard. Without this, extracted
    HTML text (collapsed spaces, stray newlines) never matches a model's
    quote and the pipeline silently produces nothing.
    """
    return _WS.sub(" ", text).strip()


@dataclass(frozen=True)
class ExtractionResult:
    accepted: int
    rejected: int


def extract_and_persist(
    run_id: int,
    doc: SourceDocument,
    text: str,
    llm: LLMClient,
    evidence_repo: EvidenceRepo,
    document_id: int,
) -> ExtractionResult:
    """Pull claims from one document, keeping only those whose quote is real.

    A rejected quote is expected output, not an error. The count is reported
    so a drifting extraction prompt is visible in diagnostics.
    """
    haystack = normalize_ws(text)
    accepted = rejected = 0

    for raw in llm.extract_claims(text):
        if normalize_ws(raw.quote) not in haystack:
            rejected += 1
            continue
        evidence_repo.insert(run_id, EvidenceItem(
            id=None, company_id=doc.company_id, source_document_id=document_id,
            claim=raw.claim, quote=raw.quote, source_class=doc.source_class,
            publisher_domain=doc.publisher_domain, published_at=doc.published_at,
            theme=raw.theme,
        ))
        accepted += 1

    return ExtractionResult(accepted=accepted, rejected=rejected)
