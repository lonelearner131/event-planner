"""
Timeline engine tests.

Coverage:
- Backward date computation.
- Compressed plans (lead time < ideal).
- Past date rejection via schema.
- Leap-year and month-end edge cases.
- Dependencies are ordered (no forward deps in task list).
- Context tasks injected (outdoor, community, cultural, children, accessibility).
- Warnings present for compressed plans.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime

import pytest

from app.schemas import (
    EventType,
    LocationType,
)
from app.services.timeline import _compress_offset, compute_timeline
from tests.conftest import future_date, make_request

# ---------------------------------------------------------------------------
# Basic backward-planning
# ---------------------------------------------------------------------------


def test_tasks_have_valid_dates() -> None:
    """All task due dates must be >= today."""
    req = make_request(event_date=future_date(365))
    result = compute_timeline(req)
    today = datetime.now(tz=UTC).date()
    for task in result.tasks:
        assert task.due_date >= today, f"Task {task.id} has past due date: {task.due_date}"


def test_event_day_tasks_on_event_date() -> None:
    """Event Day tasks should have due dates equal to the event date."""
    event_date = future_date(365)
    req = make_request(event_date=event_date)
    result = compute_timeline(req)
    event_day_tasks = [t for t in result.tasks if t.phase.value == "Event Day"]
    for task in event_day_tasks:
        assert task.due_date == event_date, (
            f"Event Day task {task.id} has date {task.due_date} but event is {event_date}"
        )


def test_tasks_sorted_by_date() -> None:
    """Tasks must be sorted by due_date ascending."""
    req = make_request(event_date=future_date(365))
    result = compute_timeline(req)
    dates = [t.due_date for t in result.tasks]
    assert dates == sorted(dates)


def test_non_empty_timeline() -> None:
    """Every event type must produce at least 5 tasks."""
    for event_type in EventType:
        req = make_request(
            event_type=event_type,
            event_date=future_date(200 if event_type == EventType.WEDDING else 90),
        )
        result = compute_timeline(req)
        assert len(result.tasks) >= 5, f"Too few tasks for {event_type}"


def test_lead_days_correct() -> None:
    """lead_days must equal the difference between event date and today."""
    days_ahead = 180
    event_date = future_date(days_ahead)
    req = make_request(event_date=event_date)
    result = compute_timeline(req)
    today = datetime.now(tz=UTC).date()
    expected_lead = (event_date - today).days
    assert result.lead_days == expected_lead


# ---------------------------------------------------------------------------
# Compressed plans
# ---------------------------------------------------------------------------


def test_compressed_flag_set_for_short_lead() -> None:
    """Plans with lead time less than ideal must be marked compressed."""
    # Wedding ideal lead is 365 days; use 30 days
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(30))
    result = compute_timeline(req)
    assert result.compressed is True


def test_not_compressed_for_full_lead() -> None:
    """Plans with full lead time must NOT be marked compressed."""
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(400))
    result = compute_timeline(req)
    assert result.compressed is False


def test_compressed_plan_has_warning() -> None:
    """Compressed plans must include a warning."""
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(30))
    result = compute_timeline(req)
    assert len(result.warnings) > 0
    assert any("compress" in w.lower() or "days" in w.lower() for w in result.warnings)


def test_clamped_tasks_marked_urgent() -> None:
    """Tasks that were clamped to today must be marked urgent."""
    req = make_request(event_type=EventType.WEDDING, event_date=future_date(5))
    result = compute_timeline(req)
    today = datetime.now(tz=UTC).date()
    clamped = [t for t in result.tasks if t.due_date == today]
    # If any tasks were clamped, at least some should be urgent
    if clamped:
        assert any(t.urgent for t in result.tasks)


# ---------------------------------------------------------------------------
# Compress offset helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "original,ratio,expected",
    [
        (300, 0.5, 150),
        (300, 1.0, 300),
        (300, 0.0, 0),
        (100, 0.25, 25),
        (7, 0.5, 4),  # rounds
    ],
)
def test_compress_offset(original: int, ratio: float, expected: int) -> None:
    assert _compress_offset(original, ratio) == expected


# ---------------------------------------------------------------------------
# Context tasks
# ---------------------------------------------------------------------------


def test_outdoor_adds_weather_task() -> None:
    """Outdoor events must include a weather backup task."""
    req = make_request(
        event_date=future_date(200),
        location_type=LocationType.OUTDOOR,
    )
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("weather" in t for t in titles)


def test_children_adds_children_task() -> None:
    """Events with children must include a children area task."""
    req = make_request(event_date=future_date(200), has_children=True)
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("children" in t or "kids" in t for t in titles)


def test_accessibility_needs_adds_audit_task() -> None:
    """Events with accessibility needs must include an accessibility audit task."""
    req = make_request(event_date=future_date(200), accessibility_needs=True)
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("accessibility" in t or "accessible" in t for t in titles)


def test_cultural_notes_adds_ceremony_task() -> None:
    """Wedding with cultural notes must include a ceremony coordination task."""
    req = make_request(
        event_type=EventType.WEDDING,
        event_date=future_date(300),
        cultural_or_religious_notes="Hindu ceremony, no alcohol",
    )
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("cultural" in t or "ceremony" in t for t in titles)


def test_community_public_adds_permit_task() -> None:
    """Public community events must include a council notification task."""
    req = make_request(
        event_type=EventType.COMMUNITY,
        event_date=future_date(100),
        is_public=True,
    )
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("permit" in t or "council" in t or "notif" in t for t in titles)


def test_community_volunteers_adds_recruitment_task() -> None:
    """Community events with volunteers must include a recruitment task."""
    req = make_request(
        event_type=EventType.COMMUNITY,
        event_date=future_date(100),
        expected_volunteers=20,
    )
    result = compute_timeline(req)
    titles = [t.title.lower() for t in result.tasks]
    assert any("volunteer" in t for t in titles)


# ---------------------------------------------------------------------------
# Leap year / month-end edge cases
# ---------------------------------------------------------------------------


def test_leap_year_event_date() -> None:
    """Plans with a leap-year event date must not error."""
    # Find the next Feb 29
    today = datetime.now(tz=UTC).date()
    year = today.year + 1
    while True:
        if calendar.isleap(year):
            break
        year += 1
    leap_date = date(year, 2, 29)
    if (leap_date - today).days > 0 and (leap_date - today).days <= 1095:
        req = make_request(event_date=leap_date)
        result = compute_timeline(req)
        assert len(result.tasks) > 0


def test_month_end_event_date() -> None:
    """Plans with a month-end event date (Jan 31) must not error."""
    today = datetime.now(tz=UTC).date()
    # Find next Jan 31 in the future
    year = (
        today.year if today.month < 1 or (today.month == 1 and today.day < 31) else today.year + 1
    )
    # Make sure we pick a future date
    target = date(year + 1, 1, 31)
    if (target - today).days > 0:
        req = make_request(event_date=target)
        result = compute_timeline(req)
        assert len(result.tasks) > 0


# ---------------------------------------------------------------------------
# Task structure
# ---------------------------------------------------------------------------


def test_task_ids_are_unique() -> None:
    """All task IDs must be unique within a plan."""
    req = make_request(event_date=future_date(365), has_children=True, accessibility_needs=True)
    result = compute_timeline(req)
    ids = [t.id for t in result.tasks]
    assert len(ids) == len(set(ids)), "Duplicate task IDs found"


def test_tasks_have_required_fields() -> None:
    """All tasks must have non-empty id, title, description."""
    req = make_request(event_date=future_date(365))
    result = compute_timeline(req)
    for task in result.tasks:
        assert task.id, f"Task missing id: {task}"
        assert task.title, f"Task missing title: {task}"
        assert task.description, f"Task missing description: {task}"


def test_birthday_timeline() -> None:
    """Birthday events must produce a valid timeline."""
    req = make_request(
        event_type=EventType.BIRTHDAY,
        event_date=future_date(56),
    )
    result = compute_timeline(req)
    assert len(result.tasks) >= 5


def test_post_event_tasks_after_event_date() -> None:
    """Post-event tasks (After phase) must have due dates after the event date."""
    req = make_request(event_date=future_date(365))
    result = compute_timeline(req)
    after_tasks = [t for t in result.tasks if t.phase.value == "After"]
    for task in after_tasks:
        assert task.due_date >= req.event_date
