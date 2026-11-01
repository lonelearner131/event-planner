"""
Budget allocation engine.

Pure functions — no I/O, no global state, fully testable.

Design rationale:
- Load weights from a data file so budget rules can be updated without code changes.
- The largest-remainder (Hamilton) method ensures line items sum EXACTLY to the total.
- Context adjustments (guest count, priorities, outdoor, children, etc.) are applied
  by modifying the weight dictionary and re-normalising, keeping the logic transparent.
- The LLM never does arithmetic; this module owns all numbers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.schemas import (
    BudgetFeasibility,
    BudgetLineItem,
    BudgetResponse,
    DiyPreference,
    EventType,
    FeasibilityStatus,
    LocationType,
    PlanRequest,
    Priority,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data loading — once at import time
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename: str) -> Any:
    """Load a JSON data file from the data directory."""
    with (_DATA_DIR / filename).open(encoding="utf-8") as fh:
        return json.load(fh)


_BUDGET_WEIGHTS: dict[str, Any] = _load_json("budget_weights.json")
_COST_BANDS: dict[str, Any] = _load_json("cost_bands.json")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTINGENCY_MIN_PERCENT: float = 0.05  # 5% floor
CONTINGENCY_DEFAULT_PERCENT: float = 0.10  # 10% default
LARGE_GUEST_THRESHOLD: int = 150  # above this, catering share increases
HIGH_GUEST_CATERING_BOOST: float = 0.03  # extra 3% catering for big events


# ---------------------------------------------------------------------------
# Weight adjustment helpers
# ---------------------------------------------------------------------------


def _base_weights(event_type: EventType) -> dict[str, float]:
    """Return a mutable copy of base allocation weights for the given event type."""
    raw: dict[str, float] = dict(_BUDGET_WEIGHTS[event_type.value])
    raw.pop("_notes", None)
    return raw


def _apply_priority_boosts(
    weights: dict[str, float], priorities: list[Priority]
) -> dict[str, float]:
    """
    Boost weight for each user-selected priority.

    Boosts come from the data file to avoid magic numbers.
    Only categories that already exist in the weights dict are boosted;
    new categories are added if absent.
    """
    boosts: dict[str, dict[str, float]] = _BUDGET_WEIGHTS.get("priority_boosts", {})
    for priority in priorities:
        for category, boost in boosts.get(priority.value, {}).items():
            weights[category] = weights.get(category, 0.0) + boost
    return weights


def _apply_context_adjustments(weights: dict[str, float], req: PlanRequest) -> dict[str, float]:
    """
    Apply non-priority context adjustments:
    - Outdoor: add tent/weather-backup budget.
    - Children: add kids entertainment.
    - Accessibility needs: reserve accessibility facilities budget.
    - DIY level: reduce decor and stationery (high DIY also reduces some catering for community).
    - Large guest count: shift more budget toward catering.
    """
    # Outdoor
    if req.location_type in {LocationType.OUTDOOR, LocationType.EITHER}:
        additions: dict[str, float] = _BUDGET_WEIGHTS.get("outdoor_additions", {})
        for cat, val in additions.items():
            weights[cat] = weights.get(cat, 0.0) + val

    # Children
    if req.has_children:
        additions = _BUDGET_WEIGHTS.get("children_additions", {})
        for cat, val in additions.items():
            weights[cat] = weights.get(cat, 0.0) + val

    # Accessibility
    if req.accessibility_needs:
        additions = _BUDGET_WEIGHTS.get("accessibility_additions", {})
        for cat, val in additions.items():
            weights[cat] = weights.get(cat, 0.0) + val

    # DIY preference — reduce certain categories
    if req.diy_preference in {DiyPreference.MEDIUM, DiyPreference.HIGH}:
        reductions: dict[str, float] = _BUDGET_WEIGHTS.get("diy_reductions", {}).get(
            req.diy_preference.value, {}
        )
        for cat, delta in reductions.items():
            if cat in weights:
                weights[cat] = max(0.0, weights[cat] + delta)  # delta is negative

    # Large guest count boosts catering
    if req.guest_count > LARGE_GUEST_THRESHOLD:
        weights["catering"] = weights.get("catering", 0.0) + HIGH_GUEST_CATERING_BOOST

    return weights


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """
    Re-normalise weights so they sum to exactly 1.0.

    The contingency line is preserved at the minimum floor.
    All other lines are scaled proportionally.
    """
    contingency = max(
        weights.get("contingency", CONTINGENCY_DEFAULT_PERCENT),
        CONTINGENCY_MIN_PERCENT,
    )
    weights["contingency"] = contingency

    non_contingency = {k: v for k, v in weights.items() if k != "contingency"}
    total_non_contingency = sum(non_contingency.values())

    if total_non_contingency <= 0:
        # Degenerate case — distribute evenly
        n = max(len(non_contingency), 1)
        scale = (1.0 - contingency) / n
        return dict.fromkeys(non_contingency, scale) | {"contingency": contingency}

    scale = (1.0 - contingency) / total_non_contingency
    result = {k: v * scale for k, v in non_contingency.items()}
    result["contingency"] = contingency
    return result


# ---------------------------------------------------------------------------
# Largest-remainder (Hamilton) method
# ---------------------------------------------------------------------------


def _largest_remainder(weights: dict[str, float], total: int) -> dict[str, int]:
    """
    Allocate `total` integer units across categories using the Hamilton method.

    Guarantees: sum of all allocated amounts == total.
    No rounding drift.
    """
    # Floor allocations
    exact = {k: v * total for k, v in weights.items()}
    floors = {k: int(v) for k, v in exact.items()}
    remainders = {k: exact[k] - floors[k] for k in exact}

    shortfall = total - sum(floors.values())
    # Distribute remaining units to the largest remainders
    sorted_keys = sorted(remainders, key=lambda k: remainders[k], reverse=True)
    for i in range(shortfall):
        floors[sorted_keys[i % len(sorted_keys)]] += 1

    return floors


# ---------------------------------------------------------------------------
# Feasibility analysis
# ---------------------------------------------------------------------------


def _feasibility(req: PlanRequest, total: int) -> BudgetFeasibility:
    """
    Compare per-guest cost against cost bands for this event type and currency.

    Returns a FeasibilityStatus with a plain-language explanation and
    2-4 concrete, computed suggestions.
    """
    per_guest = total // max(req.guest_count, 1)
    bands: dict[str, int] = _COST_BANDS[req.event_type.value][req.currency.value]

    comfortable_threshold = bands["comfortable"]
    workable_threshold = bands["workable"]
    tight_threshold = bands["tight"]

    if per_guest >= comfortable_threshold:
        status = FeasibilityStatus.COMFORTABLE
        explanation = (
            f"Your budget of {req.currency.value} {per_guest:,} per guest is above the"
            f" comfortable threshold of {req.currency.value} {comfortable_threshold:,}."
            " You have good flexibility to prioritise what matters most."
        )
    elif per_guest >= workable_threshold:
        status = FeasibilityStatus.WORKABLE
        explanation = (
            f"At {req.currency.value} {per_guest:,} per guest your budget is workable."
            " You'll need to make deliberate trade-offs but a quality event is achievable."
        )
    elif per_guest >= tight_threshold:
        status = FeasibilityStatus.TIGHT
        explanation = (
            f"Your budget of {req.currency.value} {per_guest:,} per guest is tight for a"
            f" {req.event_type.value}. Significant savings will be needed in every category."
        )
    else:
        status = FeasibilityStatus.UNREALISTIC
        explanation = (
            f"At {req.currency.value} {per_guest:,} per guest this budget is below the minimum"
            f" viable level for a {req.event_type.value}. Consider the suggestions below."
        )

    # Computed, concrete suggestions
    suggestions: list[str] = []

    # Guest-count reduction
    target_per_guest = (
        workable_threshold if per_guest < workable_threshold else comfortable_threshold
    )
    if per_guest < target_per_guest:
        new_count = total // target_per_guest
        if new_count >= 1 and new_count < req.guest_count:
            suggestions.append(
                f"Reduce the guest list from {req.guest_count} to approximately"
                f" {new_count} to reach a workable {req.currency.value} {target_per_guest:,}"
                " per guest."
            )

    # Budget increase
    workable_total = req.guest_count * workable_threshold
    if total < workable_total:
        gap = workable_total - total
        suggestions.append(
            f"Increasing the total budget by {req.currency.value} {gap:,} would bring you"
            f" to a workable {req.currency.value} {workable_threshold:,} per guest."
        )

    # DIY suggestion
    if req.diy_preference == DiyPreference.LOW:
        suggestions.append(
            "Switch DIY preference to 'high' — self-handling décor and stationery can"
            " save 8–12% of the total budget."
        )

    # Off-peak suggestion for weddings
    if req.event_type == EventType.WEDDING:
        suggestions.append(
            "Book on a Friday or Sunday — venues typically charge 20–30% less than a Saturday."
        )

    # Catering simplification
    if req.event_type != EventType.COMMUNITY:
        suggestions.append(
            "Switch from plated service to a buffet or sharing-platter format —"
            " this reduces catering labour costs by 15–25%."
        )

    return BudgetFeasibility(
        status=status,
        per_guest_cost=per_guest,
        explanation=explanation,
        suggestions=suggestions[:4],  # Max 4 suggestions
    )


# ---------------------------------------------------------------------------
# Rationale helpers
# ---------------------------------------------------------------------------

_RATIONALE_TEMPLATES: dict[str, str] = {
    "venue": "Venue is typically the single largest cost and is booked first.",
    "catering": "Catering scales directly with guest count — the biggest per-head expense.",
    "photography": "Photography preserves lifelong memories; couples consistently rank it high.",
    "decor": "Décor sets the atmosphere and is a high DIY-potential category.",
    "music": "Music drives the energy of the event and is hard to recover from if underfunded.",
    "attire": "Wedding attire including fittings and accessories is a one-time significant spend.",
    "transport": "Transport coordinates guest logistics and the wedding party movement.",
    "stationery": "Invitations and printed materials set expectations before the event.",
    "contingency": "Reserve fund covers unexpected costs — the safety net for every plan.",
    "entertainment": "Entertainment keeps guests engaged and creates memorable moments.",
    "cake_desserts": "Cake is a centrepiece moment; desserts complement the main menu.",
    "invitations": "Invitations are the first impression guests receive of the event.",
    "equipment_av": "AV ensures every guest can hear and participate fully.",
    "permits_insurance": "Permits and insurance are legal requirements for public events.",
    "publicity_marketing": "Publicity drives attendance — essential for community events to succeed.",
    "first_aid_security": "Safety is non-negotiable; required by most authorities for public events.",
    "volunteer_refreshments": "Keeping volunteers fed and hydrated sustains performance across a long day.",
    "rentals_tent_weather": "Outdoor events need weather contingency; tents and marquees protect the event.",
    "kids_entertainment": "Children need dedicated activities so parents can enjoy the event.",
    "accessibility_facilities": "Accessible facilities ensure every guest can participate equally.",
}

_SAVING_TIPS: dict[str, str] = {
    "venue": "Compare weekday rates; mid-week bookings can save 20–40%.",
    "catering": "A buffet rather than plated service reduces staff costs by 15–25%.",
    "photography": "A shorter coverage package (e.g. ceremony only) significantly reduces cost.",
    "decor": "DIY centrepieces and rented items instead of purchased ones can halve decor spend.",
    "music": "A DJ typically costs 40–60% less than a live band for equivalent coverage.",
    "attire": "Sample sales and rental options can reduce attire spend by 30–70%.",
    "transport": "Arrange guest carpooling or use a local minibus rather than individual cars.",
    "stationery": "Digital invitations cost a fraction of printed ones and reach guests faster.",
    "contingency": "Keep this — it almost always gets used; reducing it creates financial risk.",
    "entertainment": "Local acts and community performers are affordable and add authenticity.",
    "cake_desserts": "A smaller centrepiece cake with a sheet cake for serving costs far less.",
    "invitations": "Canva digital invitations are free and look professional.",
    "equipment_av": "Hire rather than buy; compare quotes from at least 3 suppliers.",
    "permits_insurance": "Apply early — rush fees for late applications can be significant.",
    "publicity_marketing": "Free social media and community board promotion can replace paid advertising.",
    "first_aid_security": "This is a non-negotiable safety expense — do not reduce below the minimum.",
    "volunteer_refreshments": "Keep meals simple: sandwiches, fruit, and hot drinks are sufficient.",
    "rentals_tent_weather": "Share a marquee with a neighbouring vendor to split hire costs.",
    "kids_entertainment": "A well-resourced arts-and-crafts station is inexpensive and keeps kids engaged.",
    "accessibility_facilities": "Many accessibility measures (clear signage, reserved seating) cost nothing.",
}


def _rationale(category: str) -> str:
    return _RATIONALE_TEMPLATES.get(
        category, f"Standard allocation for {category.replace('_', ' ')}."
    )


def _saving_tip(category: str) -> str:
    return _SAVING_TIPS.get(category, "Compare at least 3 suppliers and negotiate payment terms.")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_budget(req: PlanRequest) -> BudgetResponse:
    """
    Compute a fully context-aware budget breakdown for the given plan request.

    Steps:
    1. Load base weights for the event type.
    2. Apply priority boosts.
    3. Apply context adjustments (outdoor, children, DIY, etc.).
    4. Normalise weights to 1.0.
    5. Allocate integer amounts using largest-remainder.
    6. Run feasibility analysis.
    7. Assemble response.
    """
    weights = _base_weights(req.event_type)
    weights = _apply_priority_boosts(weights, req.priorities)
    weights = _apply_context_adjustments(weights, req)
    weights = _normalise(weights)
    allocations = _largest_remainder(weights, req.total_budget)

    # Sanity check — the engine guarantee
    if sum(allocations.values()) != req.total_budget:
        msg = "Budget allocation sum mismatch — this is a bug in the largest-remainder method."
        raise RuntimeError(msg)

    line_items = [
        BudgetLineItem(
            category=cat.replace("_", " ").title(),
            amount=amount,
            percent=round(weights[cat] * 100, 1),
            rationale=_rationale(cat),
            saving_tip=_saving_tip(cat),
        )
        for cat, amount in sorted(allocations.items(), key=lambda x: x[1], reverse=True)
    ]

    contingency_amount = allocations.get("contingency", 0)
    contingency_percent = round(weights.get("contingency", 0) * 100, 1)

    feasibility = _feasibility(req, req.total_budget)

    return BudgetResponse(
        total=req.total_budget,
        currency=req.currency,
        line_items=line_items,
        feasibility=feasibility,
        contingency_amount=contingency_amount,
        contingency_percent=contingency_percent,
    )
