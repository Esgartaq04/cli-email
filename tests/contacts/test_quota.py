from outreach.contacts.quota import CreditBudget


def test_spends_until_the_budget_runs_out():
    b = CreditBudget(3)
    assert [b.try_spend(1) for _ in range(4)] == [True, True, True, False]
    assert (b.remaining, b.spent) == (0, 3)


def test_a_charge_larger_than_what_is_left_is_refused_whole():
    b = CreditBudget(1)
    assert b.try_spend(2) is False and b.remaining == 1


def test_negative_start_is_zero():
    assert CreditBudget(-5).remaining == 0


def test_free_calls_always_succeed():
    assert CreditBudget(0).try_spend(0) is True

