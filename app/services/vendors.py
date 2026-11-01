"""
Vendor checklist service.

Builds a context-filtered vendor checklist from the vendor catalog,
enriched with budget amounts (from the pre-computed budget) and book-by dates
derived from the timeline.

Pure functions — no I/O after startup.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from app.schemas import (
    BudgetResponse,
    EventType,
    PlanRequest,
    TimelineResponse,
    VendorChecklistItem,
    VendorsResponse,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename: str) -> Any:
    with (_DATA_DIR / filename).open(encoding="utf-8") as fh:
        return json.load(fh)


_VENDOR_CATALOG: dict[str, Any] = _load_json("vendor_catalog.json")

# ---------------------------------------------------------------------------
# Vendor selection rules
# ---------------------------------------------------------------------------

# Maps vendor catalog keys to the event types they apply to
_EVENT_VENDORS: dict[str, list[str]] = {
    "wedding": [
        "venue",
        "caterer",
        "photographer",
        "decorator",
        "music",
        "officiant",
        "transport",
        "bakery",
    ],
    "birthday": [
        "venue",
        "caterer",
        "decorator",
        "music",
        "bakery",
    ],
    "community": [
        "venue",
        "caterer",
        "decorator",
        "music",
        "permit_office",
        "security",
        "first_aid",
        "av_sound",
    ],
}

# Category names as they appear in the budget line items (Title Case)
_BUDGET_CATEGORY_MAP: dict[str, str] = {
    "venue": "Venue",
    "caterer": "Catering",
    "photographer": "Photography",
    "decorator": "Decor",
    "music": "Music",
    "officiant": "Officiant",
    "transport": "Transport",
    "bakery": "Cake Desserts",
    "entertainer": "Kids Entertainment",
    "permit_office": "Permits Insurance",
    "security": "First Aid Security",
    "first_aid": "First Aid Security",
    "av_sound": "Equipment Av",
}

# Vendor key → timeline linked_vendor_category values for book-by date lookup
_VENDOR_TIMELINE_LINK: dict[str, str] = {
    "venue": "venue",
    "caterer": "caterer",
    "photographer": "photographer",
    "decorator": "decorator",
    "music": "music",
    "officiant": "officiant",
    "transport": "transport",
    "bakery": "bakery",
    "entertainer": "entertainer",
    "permit_office": "permit_office",
    "security": "security",
    "first_aid": "first_aid",
    "av_sound": "av_sound",
}


def _why_needed(vendor_key: str, req: PlanRequest) -> str:
    """Generate a context-aware explanation of why this vendor is needed."""
    base = {
        "venue": f"A venue is essential to host your {req.event_type.value} for {req.guest_count} guests.",
        "caterer": f"Catering for {req.guest_count} guests is the core hospitality of your event.",
        "photographer": "Photography preserves lifelong memories of the day.",
        "decorator": (
            f"Décor sets the atmosphere and brings your"
            f" {req.theme_or_vibe or req.event_type.value} theme to life."
        ),
        "music": "Music drives energy, creates atmosphere, and keeps guests engaged.",
        "officiant": "A legally authorised officiant is required to conduct a valid marriage ceremony.",
        "transport": (
            "Transport logistics ensure the wedding party and guests move safely and on time."
            if req.event_type == EventType.WEDDING
            else "Transport helps manage guest logistics for a large event."
        ),
        "bakery": (
            "A custom cake is a centrepiece moment and a beloved tradition."
            if req.event_type == EventType.WEDDING
            else "A birthday cake is the symbolic centrepiece of the celebration."
        ),
        "entertainer": "Children at the event need dedicated, age-appropriate entertainment.",
        "permit_office": "Permits are a legal requirement for public community events.",
        "security": "Licensed security ensures the safety of all attendees at a public event.",
        "first_aid": (
            f"First aid provision is a legal requirement for events of {req.guest_count} guests."
        ),
        "av_sound": f"PA and AV equipment ensures {req.guest_count} guests can hear and participate.",
    }
    return base.get(vendor_key, f"Required vendor for your {req.event_type.value}.")


def _book_by_date(
    vendor_key: str,
    timeline: TimelineResponse,
    event_date: date,
) -> date | None:
    """
    Derive a book-by date from the timeline — earliest task linked to this vendor category.
    Falls back to None if no linked task is found.
    """
    linked_cat = _VENDOR_TIMELINE_LINK.get(vendor_key)
    if not linked_cat:
        return None
    matching_dates = [
        t.due_date
        for t in timeline.tasks
        if t.linked_vendor_category == linked_cat and t.priority in {"critical", "high"}
    ]
    if matching_dates:
        return min(matching_dates)
    return None


def _budget_amount_for(vendor_key: str, budget: BudgetResponse) -> int:
    """Look up the allocated budget amount for this vendor category."""
    budget_cat = _BUDGET_CATEGORY_MAP.get(vendor_key, "").lower()
    for item in budget.line_items:
        if item.category.lower() == budget_cat:
            return item.amount
    # Fallback: look for partial match
    for item in budget.line_items:
        cat_lower = item.category.lower().replace(" ", "_")
        if vendor_key.startswith(cat_lower) or cat_lower.startswith(vendor_key):
            return item.amount
    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_vendors(
    req: PlanRequest,
    budget: BudgetResponse,
    timeline: TimelineResponse,
) -> VendorsResponse:
    """
    Build a context-filtered vendor checklist for the given request.

    Selects vendors based on event type, then applies context filters:
    - children → entertainer
    - community + is_public → permit_office, security, first_aid
    """
    vendor_keys = list(_EVENT_VENDORS.get(req.event_type.value, []))

    # Context additions
    if req.has_children and "entertainer" not in vendor_keys:
        vendor_keys.append("entertainer")

    # For community events, ensure safety vendors are present
    if req.event_type == EventType.COMMUNITY:
        for key in ("permit_office", "security", "first_aid"):
            if key not in vendor_keys:
                vendor_keys.append(key)

    items: list[VendorChecklistItem] = []
    for vendor_key in vendor_keys:
        template = _VENDOR_CATALOG.get(vendor_key)
        if not template:
            logger.warning("No vendor template found for key: %s", vendor_key)
            continue

        book_by = _book_by_date(vendor_key, timeline, req.event_date)
        amount = _budget_amount_for(vendor_key, budget)
        why = _why_needed(vendor_key, req)

        items.append(
            VendorChecklistItem(
                id=template["id"],
                category=template["category"],
                why_needed=why,
                linked_budget_amount=amount,
                book_by_date=book_by,
                questions_to_ask=template["questions_to_ask"],
                red_flags=template["red_flags"],
                contract_checklist=template["contract_checklist"],
            )
        )

    return VendorsResponse(items=items)
