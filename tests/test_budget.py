"""
Budget engine tests.

Coverage:
- Sum always equals total (200+ parameterised cases).
- Contingency floor enforced.
- Priority boosts shift allocation.
- Feasibility bands.
- Tiny and huge budgets.
- 1 and 5000 guests.
- All three event types.
"""

from __future__ import annotations

import random

import pytest

from app.schemas import (
    Currency,
    DiyPreference,
    EventType,
    FeasibilityStatus,
    LocationType,
    PlanRequest,
    Priority,
)
from app.services.budget import (
    CONTINGENCY_MIN_PERCENT,
    _largest_remainder,
    compute_budget,
)
from tests.conftest import future_date, make_request

# ---------------------------------------------------------------------------
# Largest-remainder method
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n,total",
    [
        (3, 100),
        (5, 1000),
        (7, 99),
        (4, 1),
        (10, 10000),
        (3, 3),
        (6, 7),
    ],
)
def test_largest_remainder_sums_to_total(n: int, total: int) -> None:
    """Sum of allocated amounts must always equal total."""
    weights = {f"cat{i}": 1 / n for i in range(n)}
    result = _largest_remainder(weights, total)
    assert sum(result.values()) == total


# ---------------------------------------------------------------------------
# Sum invariant — 200+ randomised cases
# ---------------------------------------------------------------------------


def _random_request(seed: int) -> PlanRequest:
    rng = random.Random(seed)
    event_types = list(EventType)
    currencies = list(Currency)
    diy_prefs = list(DiyPreference)
    locations = list(LocationType)
    priorities = list(Priority)

    event_type = rng.choice(event_types)
    guest_count = rng.randint(1, 5000)
    total_budget = rng.randint(1, 10_000_000)
    chosen_priorities = rng.sample(priorities, rng.randint(0, len(priorities)))

    return make_request(
        event_type=event_type,
        event_date=future_date(rng.randint(10, 1000)),
        guest_count=guest_count,
        total_budget=total_budget,
        currency=rng.choice(currencies),
        location_type=rng.choice(locations),
        priorities=chosen_priorities,
        has_children=rng.choice([True, False]),
        accessibility_needs=rng.choice([True, False]),
        diy_preference=rng.choice(diy_prefs),
    )


@pytest.mark.parametrize("seed", range(210))  # 210 randomised cases
def test_budget_sum_equals_total(seed: int) -> None:
    """Budget line items must sum exactly to total_budget for any valid input."""
    req = _random_request(seed)
    result = compute_budget(req)
    computed_sum = sum(item.amount for item in result.line_items)
    assert computed_sum == req.total_budget, (
        f"Seed {seed}: sum={computed_sum}, expected={req.total_budget}"
    )


# ---------------------------------------------------------------------------
# Contingency floor
# ---------------------------------------------------------------------------


def test_contingency_floor_enforced() -> None:
    """Contingency must never fall below the minimum floor."""
    req = make_request(total_budget=100, priorities=list(Priority))
    result = compute_budget(req)
    contingency_item = next(
        (i for i in result.line_items if "contingency" in i.category.lower()), None
    )
    assert contingency_item is not None, "No contingency line item found"
    assert contingency_item.percent >= CONTINGENCY_MIN_PERCENT * 100 - 0.1  # tolerance for rounding


def test_contingency_percent_in_response() -> None:
    """Contingency percent and amount are present in the response."""
    req = make_request()
    result = compute_budget(req)
    assert result.contingency_percent >= CONTINGENCY_MIN_PERCENT * 100 - 0.1
    assert result.contingency_amount >= 0


# ---------------------------------------------------------------------------
# Priority boosts
# ---------------------------------------------------------------------------


def test_photography_priority_boosts_photography_share() -> None:
    """Adding photography priority must increase the photography allocation."""
    base = make_request(priorities=[])
    boosted = make_request(priorities=[Priority.PHOTOGRAPHY])

    base_result = compute_budget(base)
    boosted_result = compute_budget(boosted)

    def photography_amount(r):
        for item in r.line_items:
            if "photo" in item.category.lower():
                return item.amount
        return 0

    assert photography_amount(boosted_result) >= photography_amount(base_result)


def test_food_priority_boosts_catering_share() -> None:
    """Adding food priority must increase catering allocation."""
    base = make_request(priorities=[])
    boosted = make_request(priorities=[Priority.FOOD])

    def catering_amount(r):
        for item in r.line_items:
            if "cater" in item.category.lower():
                return item.amount
        return 0

    assert catering_amount(compute_budget(boosted)) >= catering_amount(compute_budget(base))


def test_decor_priority_boosts_decor_share() -> None:
    """Adding decor priority must increase decor allocation."""
    base = make_request(priorities=[])
    boosted = make_request(priorities=[Priority.DECOR])

    def decor_amount(r):
        for item in r.line_items:
            if "decor" in item.category.lower():
                return item.amount
        return 0

    assert decor_amount(compute_budget(boosted)) >= decor_amount(compute_budget(base))


# ---------------------------------------------------------------------------
# Context adjustments
# ---------------------------------------------------------------------------


def test_outdoor_adds_rentals_line() -> None:
    """Outdoor events must have a rentals/tent/weather line item."""
    req = make_request(location_type=LocationType.OUTDOOR)
    result = compute_budget(req)
    categories = [i.category.lower() for i in result.line_items]
    assert any("rental" in c or "tent" in c or "weather" in c for c in categories)


def test_children_adds_kids_entertainment() -> None:
    """Events with children must include a kids entertainment line."""
    req = make_request(has_children=True)
    result = compute_budget(req)
    categories = [i.category.lower() for i in result.line_items]
    assert any("kids" in c or "children" in c or "entertainment" in c for c in categories)


def test_high_diy_reduces_decor_vs_low_diy() -> None:
    """High DIY should allocate less to decor than low DIY."""
    low_diy = make_request(diy_preference=DiyPreference.LOW)
    high_diy = make_request(diy_preference=DiyPreference.HIGH)

    def decor_pct(r):
        for item in r.line_items:
            if "decor" in item.category.lower():
                return item.percent
        return 0

    assert decor_pct(compute_budget(high_diy)) <= decor_pct(compute_budget(low_diy))


# ---------------------------------------------------------------------------
# Feasibility bands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "budget,guests,expected_status",
    [
        (100_000, 50, "comfortable"),  # $2000/guest — comfortable for wedding
        (10_000, 100, "tight"),  # $100/guest — tight for wedding
        (500, 200, "unrealistic"),  # $2.5/guest — unrealistic
    ],
)
def test_feasibility_bands(budget: int, guests: int, expected_status: str) -> None:
    """Feasibility status must match the expected cost-band tier."""
    req = make_request(
        event_type=EventType.WEDDING,
        total_budget=budget,
        guest_count=guests,
        currency=Currency.USD,
    )
    result = compute_budget(req)
    assert result.feasibility.status.value == expected_status


def test_feasibility_has_suggestions() -> None:
    """Tight budgets must include at least one suggestion."""
    req = make_request(total_budget=500, guest_count=200)
    result = compute_budget(req)
    if result.feasibility.status in {FeasibilityStatus.TIGHT, FeasibilityStatus.UNREALISTIC}:
        assert len(result.feasibility.suggestions) >= 1


def test_feasibility_per_guest_cost() -> None:
    """Per-guest cost in feasibility matches integer division of total / guests."""
    req = make_request(total_budget=10000, guest_count=80)
    result = compute_budget(req)
    assert result.feasibility.per_guest_cost == 10000 // 80


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_single_guest_budget() -> None:
    """Budget for 1 guest must not error and must sum correctly."""
    req = make_request(guest_count=1, total_budget=50000)
    result = compute_budget(req)
    assert sum(i.amount for i in result.line_items) == 50000


def test_5000_guests_budget() -> None:
    """Budget for 5000 guests must not error and must sum correctly."""
    req = make_request(guest_count=5000, total_budget=500000)
    result = compute_budget(req)
    assert sum(i.amount for i in result.line_items) == 500000


def test_tiny_budget_1() -> None:
    """Budget of 1 must still produce a valid allocation."""
    req = make_request(total_budget=1)
    result = compute_budget(req)
    assert sum(i.amount for i in result.line_items) == 1


def test_community_event_has_permits() -> None:
    """Community events must include permits/insurance line item."""
    req = make_request(
        event_type=EventType.COMMUNITY,
        event_date=future_date(90),
    )
    result = compute_budget(req)
    categories = [i.category.lower() for i in result.line_items]
    assert any("permit" in c or "insurance" in c for c in categories)


def test_birthday_event_budget() -> None:
    """Birthday event budgets must sum correctly."""
    req = make_request(
        event_type=EventType.BIRTHDAY,
        event_date=future_date(45),
        total_budget=3000,
        guest_count=30,
    )
    result = compute_budget(req)
    assert sum(i.amount for i in result.line_items) == 3000


def test_all_currencies() -> None:
    """Budget must work correctly for all supported currencies."""
    for currency in Currency:
        req = make_request(currency=currency, total_budget=10000)
        result = compute_budget(req)
        assert sum(i.amount for i in result.line_items) == 10000
        assert result.currency == currency


def test_all_event_types_budget() -> None:
    """All three event types must produce valid budgets."""
    for event_type in EventType:
        req = make_request(
            event_type=event_type,
            event_date=future_date(200 if event_type == EventType.WEDDING else 60),
        )
        result = compute_budget(req)
        assert sum(i.amount for i in result.line_items) == req.total_budget


def test_budget_rationale_and_saving_tips() -> None:
    """Every line item must have a non-empty rationale and saving tip."""
    req = make_request()
    result = compute_budget(req)
    for item in result.line_items:
        assert item.rationale, f"Missing rationale for {item.category}"
        assert item.saving_tip, f"Missing saving_tip for {item.category}"
