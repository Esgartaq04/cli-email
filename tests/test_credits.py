from __future__ import annotations

import pytest

from outreach.credits import format_credits, tenths


@pytest.mark.parametrize("value,expected", [(1, 10), (0.5, 5), (0, 0), (2, 20), (0.2, 2)])
def test_tenths(value, expected):
    assert tenths(value) == expected


@pytest.mark.parametrize("value", [
    0.25, -1, True, "1", None,
    # TOML has float literals for these; they must be a ValueError, not an
    # InvalidOperation or OverflowError out of Decimal.
    float("nan"), float("inf"),
])
def test_tenths_rejects(value):
    with pytest.raises(ValueError):
        tenths(value)


@pytest.mark.parametrize("amount,text", [
    (45, "4.5"), (30, "3"), (0, "0"), (5, "0.5"), (135, "13.5"),
    (-5, "-0.5"),  # never expected, but a sign must not turn -5 into "-1.5"
])
def test_format_credits(amount, text):
    assert format_credits(amount) == text
