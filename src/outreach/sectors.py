"""The sector names Hunter's company search accepts.

`data/industries.json` is a copy of https://hunter.io/files/industries.json,
bundled with the package so listing and checking sectors works offline and
costs nothing. To refresh it, download that file again and replace the copy;
the tests check that it is still a list of unique, unpadded names.
"""
from __future__ import annotations

import json
from functools import cache
from importlib.resources import files


@cache
def load_sectors() -> tuple[str, ...]:
    """Hunter's industry names, in the order it lists them."""
    path = files("outreach").joinpath("data/industries.json")
    return tuple(json.loads(path.read_text(encoding="utf-8")))
