import pytest

from outreach.contacts.ledger import CreditLedger


def test_reserve_settle_and_refuse():
    ledger = CreditLedger(50)
    assert ledger.try_reserve(45) and ledger.remaining == 5
    ledger.settle(45, 30)
    assert (ledger.remaining, ledger.spent) == (20, 30)
    assert not ledger.try_reserve(45) and ledger.remaining == 20


def test_reserving_exactly_what_is_left_works():
    ledger = CreditLedger(45)
    assert ledger.try_reserve(45) and ledger.remaining == 0
    assert not ledger.try_reserve(1)


def test_settling_more_than_was_reserved_is_a_bug():
    with pytest.raises(ValueError):
        CreditLedger(50).settle(10, 15)
