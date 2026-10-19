from __future__ import annotations

import re

# One "@", something before it, and a dotted domain whose labels are not empty.
# This is a typo guard for what a person types, not an RFC 5322 parser.
_ADDRESS = re.compile(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+")


def normalize_address(text: str) -> str:
    """The form an address is stored and compared in: trimmed and lowercased."""
    address = text.strip().lower()
    if not _ADDRESS.fullmatch(address):
        raise ValueError(f'"{text}" is not an email address')
    return address
