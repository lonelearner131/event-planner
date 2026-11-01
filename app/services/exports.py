"""
Export service — .ics (iCalendar) and CSV generation.

Security:
- ICS content is properly escaped per RFC 5545.
- CSV cells starting with = + - @ are prefixed with a single quote to
  prevent formula injection in spreadsheet applications.
- Neither format includes user-supplied text without sanitisation.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime

from app.schemas import PlanRequest, PlanResponse, TimelineResponse

logger = logging.getLogger(__name__)

# ICS constants
_PRODID = "-//CelebrationPlanner//Celebration & Event Planner//EN"
_CRLF = "\r\n"

# CSV formula injection — cells starting with these chars are dangerous in spreadsheets
_FORMULA_START = frozenset({"=", "+", "-", "@", "\t", "\r"})


# ---------------------------------------------------------------------------
# ICS helpers
# ---------------------------------------------------------------------------


def _ics_escape(text: str) -> str:
    """Escape ICS TEXT per RFC 5545 §3.3.11."""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "")
    return text


def _fold_line(line: str, max_len: int = 75) -> str:
    """
    Fold long ICS lines per RFC 5545 §3.1.

    Lines longer than 75 octets must be folded with CRLF + space.
    """
    if len(line.encode("utf-8")) <= max_len:
        return line
    parts = []
    while line.encode("utf-8").__len__() > max_len:
        # Find safe split point (avoid splitting multi-byte chars)
        split_at = max_len
        while line[:split_at].encode("utf-8").__len__() > max_len:
            split_at -= 1
        parts.append(line[:split_at])
        line = " " + line[split_at:]
    parts.append(line)
    return _CRLF.join(parts)


def _ics_date(d: date) -> str:
    """Format a date as ICS DATE value."""
    return d.strftime("%Y%m%d")


def _ics_datetime_now() -> str:
    """Format current UTC datetime as ICS DATETIME."""
    return datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# CSV injection guard
# ---------------------------------------------------------------------------


def _csv_safe(value: str | int | float | None) -> str:
    """
    Prefix cells that could be interpreted as spreadsheet formulas.

    Prepends a single quote to trigger plain-text mode in Excel/Sheets.
    """
    text = str(value) if value is not None else ""
    if text and text[0] in _FORMULA_START:
        return f"'{text}"
    return text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_ics(req: PlanRequest, timeline: TimelineResponse) -> bytes:
    """
    Generate an iCalendar (.ics) file from the timeline tasks.

    Returns UTF-8 encoded bytes.
    Each task becomes a VEVENT with a DATE (not DATETIME) so it appears
    as an all-day event in calendar applications.
    """
    dtstamp = _ics_datetime_now()
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(req.event_type.value.title())} Planning Timeline",
        "X-WR-TIMEZONE:UTC",
    ]

    for task in timeline.tasks:
        uid = f"{task.id}@celebration-planner"
        summary = _ics_escape(task.title)
        description = _ics_escape(task.description)
        due_str = _ics_date(task.due_date)

        lines += [
            "BEGIN:VEVENT",
            _fold_line(f"UID:{uid}"),
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{due_str}",
            f"DTEND;VALUE=DATE:{due_str}",
            _fold_line(f"SUMMARY:{summary}"),
            _fold_line(f"DESCRIPTION:{description}"),
            f"PRIORITY:{'1' if task.priority.value == 'critical' else '5'}",
            "STATUS:NEEDS-ACTION",
            f"CATEGORIES:{_ics_escape(task.phase.value)}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return (_CRLF.join(lines) + _CRLF).encode("utf-8")


def generate_csv(req: PlanRequest, plan: PlanResponse) -> bytes:
    """
    Generate a CSV file with budget breakdown and vendor checklist.

    Returns UTF-8 encoded bytes with BOM for Excel compatibility.
    All user-supplied text is sanitised against CSV injection.
    """
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_ALL)

    # Budget section
    writer.writerow(["BUDGET BREAKDOWN"])
    writer.writerow(["Category", "Amount", "Currency", "Percent", "Saving Tip"])
    for item in plan.budget.line_items:
        writer.writerow(
            [
                _csv_safe(item.category),
                _csv_safe(item.amount),
                _csv_safe(req.currency.value),
                _csv_safe(f"{item.percent}%"),
                _csv_safe(item.saving_tip),
            ]
        )
    writer.writerow([])
    writer.writerow(
        ["Total", _csv_safe(plan.budget.total), _csv_safe(req.currency.value), "100%", ""]
    )
    writer.writerow([])

    # Feasibility
    writer.writerow(["FEASIBILITY"])
    writer.writerow(["Status", "Per Guest Cost", "Explanation"])
    writer.writerow(
        [
            _csv_safe(plan.budget.feasibility.status.value),
            _csv_safe(plan.budget.feasibility.per_guest_cost),
            _csv_safe(plan.budget.feasibility.explanation),
        ]
    )
    writer.writerow([])

    # Vendor checklist
    writer.writerow(["VENDOR CHECKLIST"])
    writer.writerow(["Category", "Why Needed", "Budget Amount", "Book By", "Status"])
    for vendor in plan.vendors.items:
        writer.writerow(
            [
                _csv_safe(vendor.category),
                _csv_safe(vendor.why_needed),
                _csv_safe(vendor.linked_budget_amount),
                _csv_safe(str(vendor.book_by_date) if vendor.book_by_date else "ASAP"),
                _csv_safe(vendor.status.value),
            ]
        )
    writer.writerow([])

    # Timeline
    writer.writerow(["TIMELINE"])
    writer.writerow(["Phase", "Task", "Due Date", "Priority", "Description"])
    for task in plan.timeline.tasks:
        writer.writerow(
            [
                _csv_safe(task.phase.value),
                _csv_safe(task.title),
                _csv_safe(task.due_date.isoformat()),
                _csv_safe(task.priority.value),
                _csv_safe(task.description),
            ]
        )

    # Return UTF-8 BOM + content for Excel compatibility
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
