"""Hunter credits, counted as whole numbers of tenths.

Hunter bills in fractions of a credit (checking an address is 0.5), so the code
counts tenths: 1 credit is 10 and a check is 5. Integer sums are exact where
0.1 + 0.2 is not, and a budget that runs to zero must reach exactly zero. A
figure comes in as a float once, through `tenths`, and goes out as text once,
through `format_credits`; nothing else in the code handles credits as a float.
"""
from __future__ import annotations

from decimal import Decimal


def tenths(value: object) -> int:
    """A number of credits as a whole number of tenths: 0.5 -> 5, 2 -> 20.

    Raises ValueError for anything that is not a number of credits Hunter could
    bill: a bool or a string, a negative, or a figure finer than a tenth.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"credits must be a number, got {value!r}")
    # Through str() so the Decimal is the figure that was written. Decimal(0.1)
    # would expose the binary fraction behind the float and fail every check.
    amount = Decimal(str(value)) * 10
    # TOML can write nan and inf, and Decimal raises its own errors on them.
    if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
        raise ValueError(f"credits must be 0 or more, in steps of 0.1, got {value!r}")
    return int(amount)


def format_credits(amount: int) -> str:
    """Tenths back to credits as a person reads them: 45 -> "4.5", 30 -> "3"."""
    whole, tenth = divmod(abs(amount), 10)
    text = f"{whole}.{tenth}" if tenth else str(whole)
    return f"-{text}" if amount < 0 else text
