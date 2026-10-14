from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CreditLedger:
    """The credits a run has left to spend, in tenths (see `outreach.credits`).

    A company's worst-case cost is reserved before anything is bought and the
    difference is returned once the real cost is known, so a run never starts a
    purchase it might not be able to pay for, and `remaining` never goes negative.
    """

    remaining: int
    spent: int = 0

    def try_reserve(self, amount: int) -> bool:
        """Set `amount` aside; False, and nothing changes, when that is more than is left."""
        if amount > self.remaining:
            return False
        self.remaining -= amount
        return True

    def settle(self, reserved: int, used: int) -> None:
        """Count `used` as spent and give back the rest of what was `reserved`."""
        if used > reserved:
            raise ValueError(f"used {used} tenths of a credit but only {reserved} were reserved")
        self.remaining += reserved - used
        self.spent += used
