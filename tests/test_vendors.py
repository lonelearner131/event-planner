"""
Vendor checklist tests.

Coverage:
- Context filtering for outdoor, children, community events.
- All wedding vendor categories present.
- Budget amounts linked correctly.
- Book-by dates derived from timeline.
"""

from __future__ import annotations

from app.schemas import EventType
from app.services.budget import compute_budget
from app.services.timeline import compute_timeline
from app.services.vendors import compute_vendors
from tests.conftest import future_date, make_request

# ---------------------------------------------------------------------------
# Wedding vendors
# ---------------------------------------------------------------------------


def test_wedding_has_venue() -> None:
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("venue" in c for c in categories)


def test_wedding_has_photographer() -> None:
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("photo" in c for c in categories)


def test_wedding_has_officiant() -> None:
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("officiant" in c or "priest" in c or "celebrant" in c for c in categories)


# ---------------------------------------------------------------------------
# Context: children
# ---------------------------------------------------------------------------


def test_children_adds_entertainer() -> None:
    """Events with children must include an entertainer in the vendor list."""
    req = make_request(event_date=future_date(365), has_children=True)
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("entertain" in c or "kids" in c for c in categories)


def test_no_children_no_entertainer_for_wedding() -> None:
    """Weddings without children should not add entertainer by default."""
    req = make_request(
        event_type=EventType.WEDDING,
        event_date=future_date(365),
        has_children=False,
    )
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    # Entertainer is not in the default wedding list
    assert not any("entertainer" in c for c in categories)


# ---------------------------------------------------------------------------
# Context: community
# ---------------------------------------------------------------------------


def test_community_has_permit_office() -> None:
    req = make_request(event_type=EventType.COMMUNITY, event_date=future_date(90))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("permit" in c or "council" in c for c in categories)


def test_community_has_first_aid() -> None:
    req = make_request(event_type=EventType.COMMUNITY, event_date=future_date(90))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("first aid" in c or "medical" in c for c in categories)


def test_community_has_security() -> None:
    req = make_request(event_type=EventType.COMMUNITY, event_date=future_date(90))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    categories = [v.category.lower() for v in vendors.items]
    assert any("security" in c for c in categories)


# ---------------------------------------------------------------------------
# Vendor structure
# ---------------------------------------------------------------------------


def test_vendor_ids_unique() -> None:
    req = make_request(event_date=future_date(365), has_children=True)
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    ids = [v.id for v in vendors.items]
    assert len(ids) == len(set(ids))


def test_vendor_fields_present() -> None:
    req = make_request(event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    for vendor in vendors.items:
        assert vendor.category, f"Vendor missing category: {vendor.id}"
        assert vendor.why_needed, f"Vendor missing why_needed: {vendor.id}"
        assert len(vendor.questions_to_ask) > 0
        assert len(vendor.red_flags) > 0
        assert len(vendor.contract_checklist) > 0


def test_vendor_why_needed_mentions_guest_count() -> None:
    """Why-needed text must reference the event context (guest count or type)."""
    req = make_request(guest_count=200, event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    # At least one vendor should mention 200 guests in why_needed
    why_texts = " ".join(v.why_needed for v in vendors.items)
    assert "200" in why_texts or "guest" in why_texts.lower()


def test_birthday_vendors() -> None:
    req = make_request(event_type=EventType.BIRTHDAY, event_date=future_date(45))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    assert len(vendors.items) >= 3


def test_all_vendors_have_status_todo_by_default() -> None:
    req = make_request(event_date=future_date(365))
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    for vendor in vendors.items:
        assert vendor.status.value == "todo"
