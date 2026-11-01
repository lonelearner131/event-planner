"""
Timeline engine — backward-planning scheduler.

Pure functions, no I/O after startup, fully testable.

Design:
- Load milestone templates from data/lead_times.json once at import time.
- Compute due dates by subtracting offset_days_before from the event date.
- If the actual lead time is shorter than the ideal, compress proportionally.
- Never emit due dates in the past; clamp to today and flag.
- Inject context-specific tasks (outdoor, community, cultural notes).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import (
    EventType,
    LocationType,
    PlanRequest,
    TaskPhase,
    TaskPriority,
    TimelineResponse,
    TimelineTask,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"


def _load_json(filename: str) -> Any:
    with (_DATA_DIR / filename).open(encoding="utf-8") as fh:
        return json.load(fh)


_LEAD_TIMES: dict[str, Any] = _load_json("lead_times.json")


def _today() -> date:
    return datetime.now(tz=UTC).date()


# ---------------------------------------------------------------------------
# Context-specific extra tasks
# ---------------------------------------------------------------------------


def _outdoor_tasks(req: PlanRequest, event_date: date) -> list[dict[str, Any]]:
    """Extra tasks for outdoor events."""
    if req.location_type not in {LocationType.OUTDOOR, LocationType.EITHER}:
        return []
    return [
        {
            "id": "ctx-outdoor-01",
            "title": "Arrange weather backup plan (tent / indoor alternative)",
            "offset_days_before": 60,
            "priority": "critical",
            "description": (
                "For outdoor events, a weather contingency is essential. Book a marquee or"
                " confirm an indoor fallback venue. Make this decision at least 60 days out."
            ),
            "linked_vendor_category": "rentals_tent_weather",
            "phase": "Details",
        },
        {
            "id": "ctx-outdoor-02",
            "title": "Check weather forecast and brief team",
            "offset_days_before": 3,
            "priority": "high",
            "description": "Monitor the 3-day forecast and activate the weather plan if needed.",
            "linked_vendor_category": None,
            "phase": "Final Week",
        },
    ]


def _community_tasks(req: PlanRequest) -> list[dict[str, Any]]:
    """Extra tasks for community events."""
    if req.event_type != EventType.COMMUNITY:
        return []
    tasks = []
    if req.is_public:
        tasks.append(
            {
                "id": "ctx-comm-01",
                "title": "Submit public event notification to local council",
                "offset_days_before": 56,
                "priority": "critical",
                "description": (
                    "Most councils require notification for public events. Submit the notification"
                    " early — some councils require 6–8 weeks' notice."
                ),
                "linked_vendor_category": "permit_office",
                "phase": "Foundations",
            }
        )
    if req.ticketed:
        tasks.append(
            {
                "id": "ctx-comm-02",
                "title": "Set up ticketing platform and pricing tiers",
                "offset_days_before": 42,
                "priority": "high",
                "description": (
                    "Choose an accessible ticketing platform. Offer concessionary and"
                    " free/assisted tickets. Publish ticket link across all channels."
                ),
                "linked_vendor_category": None,
                "phase": "Details",
            }
        )
    if req.expected_volunteers and req.expected_volunteers > 0:
        tasks.append(
            {
                "id": "ctx-comm-03",
                "title": "Publish volunteer recruitment call",
                "offset_days_before": 49,
                "priority": "high",
                "description": (
                    f"Recruit up to {req.expected_volunteers} volunteers. Publish roles,"
                    " time commitment, and any training requirements."
                ),
                "linked_vendor_category": None,
                "phase": "Booking",
            }
        )
    return tasks


def _cultural_tasks(req: PlanRequest) -> list[dict[str, Any]]:
    """Inject ceremony coordination tasks when cultural/religious notes are present."""
    if not req.cultural_or_religious_notes:
        return []
    if req.event_type != EventType.WEDDING:
        return []
    return [
        {
            "id": "ctx-cult-01",
            "title": "Coordinate cultural/religious ceremony requirements",
            "offset_days_before": 120,
            "priority": "high",
            "description": (
                "Review specific requirements for any cultural or religious elements of the"
                " ceremony. Coordinate with the officiant, venue, and relevant community elders"
                " or religious leaders."
            ),
            "linked_vendor_category": "officiant",
            "phase": "Details",
        }
    ]


def _children_tasks(req: PlanRequest) -> list[dict[str, Any]]:
    """Extra tasks when children are attending."""
    if not req.has_children:
        return []
    return [
        {
            "id": "ctx-child-01",
            "title": "Arrange dedicated children's area and supervision",
            "offset_days_before": 30,
            "priority": "high",
            "description": (
                "Set up a safe, supervised children's zone with age-appropriate activities."
                " If hiring an entertainer, confirm DBS/background checks."
            ),
            "linked_vendor_category": "entertainer",
            "phase": "Details",
        }
    ]


def _accessibility_tasks(req: PlanRequest) -> list[dict[str, Any]]:
    """Extra tasks when accessibility needs are flagged."""
    if not req.accessibility_needs:
        return []
    return [
        {
            "id": "ctx-a11y-01",
            "title": "Conduct accessibility audit of venue and facilities",
            "offset_days_before": 60,
            "priority": "critical",
            "description": (
                "Walk through the venue with accessibility needs in mind. Check: wheelchair"
                " access, accessible bathrooms, hearing loop, step-free routes, and seating."
                " Address any gaps with the venue coordinator."
            ),
            "linked_vendor_category": "venue",
            "phase": "Details",
        }
    ]


# ---------------------------------------------------------------------------
# Date compression
# ---------------------------------------------------------------------------


def _compress_offset(offset_days: int, ratio: float) -> int:
    """
    Scale an offset proportionally for compressed plans.

    Example: if ideal lead is 365 days but actual is 183, ratio = 0.5.
    A task normally due 300 days before becomes 150 days before.
    """
    return max(0, round(offset_days * ratio))


# ---------------------------------------------------------------------------
# Task builder
# ---------------------------------------------------------------------------


def _build_task(
    raw: dict[str, Any],
    event_date: date,
    phase: str,
    compressed: bool,
    ratio: float,
    clamped_ids: set[str],
    warnings: list[str],
) -> TimelineTask:
    """
    Convert a raw task dict into a TimelineTask.

    Applies date compression and clamps past due dates to today.
    """
    today = _today()
    raw_offset = raw.get("offset_days_before", 0)

    # Tasks after event day have negative offsets (post-event)
    if raw_offset < 0:
        # Post-event: count forward from event date
        due = event_date + timedelta(days=abs(raw_offset))
        due = max(due, today)
    else:
        # Pre-event: compress if needed
        actual_offset = _compress_offset(raw_offset, ratio) if compressed else raw_offset
        due = event_date - timedelta(days=actual_offset)
        if due <= today:
            due = today
            clamped_ids.add(raw["id"])

    is_urgent = (
        raw["id"] in clamped_ids
        or (compressed and raw.get("priority") == "critical")
        or (due == today)
    )

    return TimelineTask(
        id=raw["id"],
        title=raw["title"],
        phase=TaskPhase(phase),
        due_date=due,
        priority=TaskPriority(raw.get("priority", "normal")),
        depends_on=raw.get("depends_on", []),
        linked_vendor_category=raw.get("linked_vendor_category"),
        description=raw.get("description", ""),
        urgent=is_urgent,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_timeline(req: PlanRequest) -> TimelineResponse:
    """
    Compute a backward-planned timeline for the given request.

    Returns tasks sorted by due_date ascending within each phase,
    with compression metadata and warnings.
    """
    today = _today()
    event_date = req.event_date
    lead_days = (event_date - today).days

    template = _LEAD_TIMES[req.event_type.value]
    ideal_lead = template["ideal_lead_days"]
    phases: dict[str, list[dict[str, Any]]] = template["phases"]

    compressed = lead_days < ideal_lead
    ratio = min(lead_days / ideal_lead, 1.0) if ideal_lead > 0 else 1.0

    warnings: list[str] = []
    clamped_ids: set[str] = set()

    if compressed:
        warnings.append(
            f"Your event is {lead_days} days away — less than the recommended"
            f" {ideal_lead} days for a {req.event_type.value}. The timeline has been"
            " compressed. Tasks marked URGENT need immediate attention."
        )

    # Collect context-specific tasks grouped by phase
    extra_by_phase: dict[str, list[dict[str, Any]]] = {}
    all_extra = (
        _outdoor_tasks(req, event_date)
        + _community_tasks(req)
        + _cultural_tasks(req)
        + _children_tasks(req)
        + _accessibility_tasks(req)
    )
    for extra in all_extra:
        ph = extra.pop("phase", "Details")
        extra_by_phase.setdefault(ph, []).append(extra)

    # Build tasks
    tasks: list[TimelineTask] = []
    for phase_name, phase_tasks in phases.items():
        # Merge in extra context tasks for this phase
        all_phase_tasks = phase_tasks + extra_by_phase.get(phase_name, [])
        for raw in all_phase_tasks:
            task = _build_task(
                raw, event_date, phase_name, compressed, ratio, clamped_ids, warnings
            )
            tasks.append(task)

    if clamped_ids:
        warnings.append(
            f"{len(clamped_ids)} task(s) had due dates in the past and have been"
            " moved to today. These are marked URGENT — act on them immediately."
        )

    # Sort by due_date, then by priority
    priority_order = {TaskPriority.CRITICAL: 0, TaskPriority.HIGH: 1, TaskPriority.NORMAL: 2}
    tasks.sort(key=lambda t: (t.due_date, priority_order[t.priority]))

    # Dependency acyclicity is guaranteed by data design (template IDs are sequential)
    # No runtime cycle check needed; the template is the authoritative source.

    return TimelineResponse(
        tasks=tasks,
        compressed=compressed,
        lead_days=lead_days,
        warnings=warnings,
    )
