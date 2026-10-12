from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

from outreach.sectors import load_sectors


def test_bundled_sectors_are_hunters_list():
    sectors = load_sectors()
    assert 400 <= len(sectors) <= 600 and len(set(sectors)) == len(sectors)
    assert "Software Development" in sectors and "Health, Wellness and Fitness" in sectors


def test_the_list_is_read_once_and_is_immutable():
    assert isinstance(load_sectors(), tuple)
    assert load_sectors() is load_sectors()


def test_every_name_can_be_matched_without_ambiguity():
    # Sectors are matched case-insensitively and sent back to Hunter as spelled
    # here, so a refreshed list must not have padded names or names that differ
    # only by case.
    sectors = load_sectors()
    assert all(name == name.strip() for name in sectors)
    assert len({name.lower() for name in sectors}) == len(sectors)


def test_the_list_ships_with_the_package():
    # Run from a checkout the file is always found; without this entry it would be
    # missing from an installed copy, and "outreach sectors" would fail there.
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["outreach"]
    assert any(fnmatch("data/industries.json", pattern) for pattern in patterns)
