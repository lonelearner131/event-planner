"""
Export service tests.

Coverage:
- ICS validity (VCALENDAR, VEVENT, PRODID, proper CRLF).
- ICS special-character escaping.
- CSV formula-injection escaping.
- CSV BOM present for Excel.
- CSV contains expected sections.
"""

from __future__ import annotations

import pytest

from app.schemas import PlanResponse, Warnings
from app.services.budget import compute_budget
from app.services.exports import _csv_safe, _ics_escape, generate_csv, generate_ics
from app.services.timeline import compute_timeline
from app.services.vendors import compute_vendors
from tests.conftest import future_date, make_request

# ---------------------------------------------------------------------------
# CSV injection guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=SUM(A1:A10)", "'=SUM(A1:A10)"),
        ("+19725551234", "'+19725551234"),
        ("-1+1+1", "'-1+1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("Normal text", "Normal text"),
        ("100", "100"),
        ("", ""),
        (None, ""),
        ("\t=evil", "'\t=evil"),
    ],
)
def test_csv_injection_escaping(value: object, expected: str) -> None:
    """Formula-injection trigger characters must be prefixed with a single quote."""
    assert _csv_safe(value) == expected


def test_csv_injection_in_category_name() -> None:
    """Category names that start with = must be escaped."""
    result = _csv_safe("=Venue")
    assert result.startswith("'")


# ---------------------------------------------------------------------------
# ICS escaping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("simple text", "simple text"),
        ("text;with;semicolons", "text\\;with\\;semicolons"),
        ("text,with,commas", "text\\,with\\,commas"),
        ("line\nbreak", "line\\nbreak"),
        ("back\\slash", "back\\\\slash"),
        ("combined;text,\nwith\\all", "combined\\;text\\,\\nwith\\\\all"),
    ],
)
def test_ics_escape(text: str, expected: str) -> None:
    assert _ics_escape(text) == expected


# ---------------------------------------------------------------------------
# ICS generation
# ---------------------------------------------------------------------------


def _make_plan_response(req):
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    vendors = compute_vendors(req, budget, timeline)
    return PlanResponse(
        budget=budget,
        timeline=timeline,
        vendors=vendors,
        warnings=Warnings(items=[]),
    )


def test_ics_has_vcalendar_wrapper() -> None:
    req = make_request(event_date=future_date(365))
    ics = generate_ics(req, compute_timeline(req))
    text = ics.decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR")
    assert text.rstrip().endswith("END:VCALENDAR")


def test_ics_contains_prodid() -> None:
    req = make_request(event_date=future_date(365))
    ics = generate_ics(req, compute_timeline(req))
    assert b"PRODID" in ics
    assert b"CelebrationPlanner" in ics


def test_ics_has_vevents() -> None:
    req = make_request(event_date=future_date(365))
    ics = generate_ics(req, compute_timeline(req))
    text = ics.decode("utf-8")
    assert "BEGIN:VEVENT" in text
    assert "END:VEVENT" in text


def test_ics_uses_crlf() -> None:
    """ICS lines must be separated by CRLF per RFC 5545."""
    req = make_request(event_date=future_date(365))
    ics = generate_ics(req, compute_timeline(req))
    assert b"\r\n" in ics


def test_ics_events_count_matches_tasks() -> None:
    """Number of VEVENT blocks should match number of tasks."""
    req = make_request(event_date=future_date(365))
    timeline = compute_timeline(req)
    ics = generate_ics(req, timeline)
    text = ics.decode("utf-8")
    vevent_count = text.count("BEGIN:VEVENT")
    assert vevent_count == len(timeline.tasks)


def test_ics_escapes_special_chars_in_titles() -> None:
    """Task titles with special characters must be escaped in ICS."""
    req = make_request(
        event_date=future_date(365),
        theme_or_vibe="Test; with, special\\chars",
    )
    timeline = compute_timeline(req)
    ics = generate_ics(req, timeline)
    text = ics.decode("utf-8")
    # Should not have unescaped semicolons in SUMMARY lines
    summary_lines = [line for line in text.split("\r\n") if line.startswith("SUMMARY:")]
    for line in summary_lines:
        content = line[len("SUMMARY:") :]
        # Semicolons should only appear as \; (escaped)
        assert ";" not in content.replace("\\;", "")


# ---------------------------------------------------------------------------
# CSV generation
# ---------------------------------------------------------------------------


def test_csv_has_bom() -> None:
    """CSV must start with UTF-8 BOM for Excel compatibility."""
    req = make_request(event_date=future_date(365))
    plan = _make_plan_response(req)
    csv_bytes = generate_csv(req, plan)
    assert csv_bytes[:3] == b"\xef\xbb\xbf"


def test_csv_contains_budget_section() -> None:
    req = make_request(event_date=future_date(365))
    plan = _make_plan_response(req)
    csv_bytes = generate_csv(req, plan)
    assert b"BUDGET" in csv_bytes


def test_csv_contains_timeline_section() -> None:
    req = make_request(event_date=future_date(365))
    plan = _make_plan_response(req)
    csv_bytes = generate_csv(req, plan)
    assert b"TIMELINE" in csv_bytes


def test_csv_contains_vendor_section() -> None:
    req = make_request(event_date=future_date(365))
    plan = _make_plan_response(req)
    csv_bytes = generate_csv(req, plan)
    assert b"VENDOR" in csv_bytes


def test_csv_formula_injection_in_vendor_name() -> None:
    """Vendor category names that start with = must be escaped in CSV output."""
    # Directly test the _csv_safe function with a formula-starting value
    assert _csv_safe("=DANGEROUS").startswith("'")


def test_csv_is_valid_utf8() -> None:
    req = make_request(event_date=future_date(365))
    plan = _make_plan_response(req)
    csv_bytes = generate_csv(req, plan)
    # Should not raise
    decoded = csv_bytes.decode("utf-8-sig")
    assert len(decoded) > 100
