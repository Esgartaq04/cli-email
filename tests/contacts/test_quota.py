from outreach.contacts.quota import allocate_quota


def test_all_companies_processed_when_credits_are_sufficient():
    plan = allocate_quota([1, 2, 3], remaining=10)
    assert plan.process == [1, 2, 3]
    assert plan.skipped == []


def test_shortfall_processes_in_rank_order_and_skips_the_rest():
    """Companies arrive already sorted by rank. A shortfall must never
    truncate silently or raise — the remainder is reported as skipped."""
    plan = allocate_quota([1, 2, 3, 4, 5], remaining=2)
    assert plan.process == [1, 2]
    assert plan.skipped == [3, 4, 5]


def test_zero_credits_skips_everything_without_raising():
    plan = allocate_quota([1, 2], remaining=0)
    assert plan.process == []
    assert plan.skipped == [1, 2]


def test_negative_credits_are_treated_as_zero():
    plan = allocate_quota([1], remaining=-5)
    assert plan.process == []
    assert plan.skipped == [1]


def test_empty_queue_is_a_valid_plan():
    plan = allocate_quota([], remaining=5)
    assert plan.process == [] and plan.skipped == []
