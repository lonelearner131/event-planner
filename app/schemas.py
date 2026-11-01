"""
Pydantic v2 request/response schemas with strict validation.

All free-text fields are length-limited and control-character-stripped.
Enums enforce valid values.  `extra="forbid"` prevents unexpected fields.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Sentinel for max future date (3 years from now computed at import time)
# ---------------------------------------------------------------------------
_MAX_YEARS_AHEAD = 3


def _today() -> date:
    return datetime.now(tz=UTC).date()


def _max_event_date() -> date:
    today = _today()
    return today.replace(year=today.year + _MAX_YEARS_AHEAD)


# ---------------------------------------------------------------------------
# Control-character sanitiser (applied to all free-text fields)
# ---------------------------------------------------------------------------
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_ctrl(value: str) -> str:
    """Remove ASCII control characters (excluding tab, newline, carriage return)."""
    return _CTRL_RE.sub("", value).strip()


# ---------------------------------------------------------------------------
# Enum types
# ---------------------------------------------------------------------------


class EventType(str, Enum):
    """Supported event categories."""

    WEDDING = "wedding"
    BIRTHDAY = "birthday"
    COMMUNITY = "community"


class LocationType(str, Enum):
    """Venue location type."""

    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    EITHER = "either"


class Currency(str, Enum):
    """Supported currencies."""

    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


class Priority(str, Enum):
    """Guest-selectable event priorities."""

    FOOD = "food"
    MUSIC = "music"
    PHOTOGRAPHY = "photography"
    DECOR = "decor"
    ATTIRE = "attire"
    ENTERTAINMENT = "entertainment"
    GUEST_EXPERIENCE = "guest_experience"


class DietaryNeed(str, Enum):
    """Common dietary requirements."""

    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    HALAL = "halal"
    KOSHER = "kosher"
    GLUTEN_FREE = "gluten_free"
    NUT_FREE = "nut_free"
    DAIRY_FREE = "dairy_free"


class DiyPreference(str, Enum):
    """DIY involvement level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Free-text field type aliases with length limits
# ---------------------------------------------------------------------------
CityStr = Annotated[str, Field(max_length=80)]
ThemeStr = Annotated[str, Field(max_length=200)]
CulturalStr = Annotated[str, Field(max_length=300)]
AccessibilityStr = Annotated[str, Field(max_length=300)]
DietaryNoteStr = Annotated[str, Field(max_length=200)]


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """Validated input for /api/plan and /api/ideas."""

    model_config = {"extra": "forbid"}

    # Core fields
    event_type: EventType
    event_date: date
    guest_count: int = Field(..., ge=1, le=5000)
    total_budget: int = Field(..., ge=1, le=100_000_000)
    currency: Currency = Currency.USD

    # Venue
    location_type: LocationType = LocationType.INDOOR
    city_region: CityStr | None = Field(default=None, max_length=80)

    # Context
    theme_or_vibe: ThemeStr | None = Field(default=None, max_length=200)
    priorities: list[Priority] = Field(default_factory=list, max_length=7)
    dietary_needs: list[DietaryNeed] = Field(default_factory=list, max_length=7)
    dietary_notes: DietaryNoteStr | None = Field(default=None, max_length=200)
    has_children: bool = False
    accessibility_needs: bool = False
    accessibility_details: AccessibilityStr | None = Field(default=None, max_length=300)
    cultural_or_religious_notes: CulturalStr | None = Field(default=None, max_length=300)
    diy_preference: DiyPreference = DiyPreference.MEDIUM

    # Community-specific
    is_public: bool | None = None
    expected_volunteers: int | None = Field(default=None, ge=0, le=10000)
    ticketed: bool | None = None

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("event_date")
    @classmethod
    def date_must_be_future(cls, v: date) -> date:
        """Reject past dates and dates more than 3 years ahead."""
        today = _today()
        if v <= today:
            msg = "event_date must be in the future."
            raise ValueError(msg)
        if v > _max_event_date():
            msg = f"event_date must be within {_MAX_YEARS_AHEAD} years from today."
            raise ValueError(msg)
        return v

    @field_validator(
        "city_region",
        "theme_or_vibe",
        "cultural_or_religious_notes",
        "accessibility_details",
        "dietary_notes",
        mode="before",
    )
    @classmethod
    def sanitise_text(cls, v: object) -> str | None:
        """Strip control characters from all free-text fields."""
        if v is None:
            return None
        return _strip_ctrl(str(v)) or None

    @field_validator("priorities")
    @classmethod
    def deduplicate_priorities(cls, v: list[Priority]) -> list[Priority]:
        """Remove duplicate priorities while preserving order."""
        seen: set[Priority] = set()
        result = []
        for p in v:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result

    @model_validator(mode="after")
    def community_fields_only_for_community(self) -> PlanRequest:
        """Community-specific fields are ignored for other event types."""
        if self.event_type != EventType.COMMUNITY:
            # Allow the fields but they have no effect on non-community plans
            pass
        return self


# ---------------------------------------------------------------------------
# Budget response models
# ---------------------------------------------------------------------------


class BudgetLineItem(BaseModel):
    """A single line item in the budget breakdown."""

    category: str
    amount: int
    percent: float
    rationale: str
    saving_tip: str


class FeasibilityStatus(str, Enum):
    """Budget feasibility classification."""

    COMFORTABLE = "comfortable"
    WORKABLE = "workable"
    TIGHT = "tight"
    UNREALISTIC = "unrealistic"


class BudgetFeasibility(BaseModel):
    """Feasibility analysis for the given budget."""

    status: FeasibilityStatus
    per_guest_cost: int
    explanation: str
    suggestions: list[str]


class BudgetResponse(BaseModel):
    """Complete budget breakdown response."""

    total: int
    currency: Currency
    line_items: list[BudgetLineItem]
    feasibility: BudgetFeasibility
    contingency_amount: int
    contingency_percent: float


# ---------------------------------------------------------------------------
# Timeline response models
# ---------------------------------------------------------------------------


class TaskPriority(str, Enum):
    """Task urgency level."""

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"


class TaskPhase(str, Enum):
    """Phase grouping for timeline tasks."""

    FOUNDATIONS = "Foundations"
    BOOKING = "Booking"
    DETAILS = "Details"
    FINAL_WEEK = "Final Week"
    EVENT_DAY = "Event Day"
    AFTER = "After"


class TimelineTask(BaseModel):
    """A single task in the event timeline."""

    id: str
    title: str
    phase: TaskPhase
    due_date: date
    priority: TaskPriority
    depends_on: list[str] = Field(default_factory=list)
    linked_vendor_category: str | None = None
    description: str
    urgent: bool = False


class TimelineResponse(BaseModel):
    """Complete backward-planned timeline."""

    tasks: list[TimelineTask]
    compressed: bool
    lead_days: int
    warnings: list[str]


# ---------------------------------------------------------------------------
# Vendor checklist models
# ---------------------------------------------------------------------------


class VendorStatus(str, Enum):
    """Vendor booking progress."""

    TODO = "todo"
    CONTACTED = "contacted"
    BOOKED = "booked"


class VendorChecklistItem(BaseModel):
    """A single vendor category checklist entry."""

    id: str
    category: str
    why_needed: str
    linked_budget_amount: int
    book_by_date: date | None
    questions_to_ask: list[str]
    red_flags: list[str]
    contract_checklist: list[str]
    status: VendorStatus = VendorStatus.TODO


class VendorsResponse(BaseModel):
    """Complete vendor checklist."""

    items: list[VendorChecklistItem]


# ---------------------------------------------------------------------------
# Ideas response models (AI layer)
# ---------------------------------------------------------------------------


class IdeasSource(str, Enum):
    """Where the ideas originated."""

    GEMINI = "gemini"
    CURATED = "curated"


class ThemeIdea(BaseModel):
    """A single event theme suggestion."""

    name: str
    description: str
    palette: list[str]  # color names, not hex


class IdeasContent(BaseModel):
    """The ideas payload (AI or curated)."""

    themes: list[ThemeIdea] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    menu_suggestions: list[str] = Field(default_factory=list)
    decor_ideas: list[str] = Field(default_factory=list)
    music_vibes: list[str] = Field(default_factory=list)
    sample_invitation: str = ""
    money_saving_ideas: list[str] = Field(default_factory=list)
    accessibility_touches: list[str] = Field(default_factory=list)


class IdeasResponse(BaseModel):
    """Ideas endpoint response including provenance."""

    ideas: IdeasContent
    source: IdeasSource
    ai_notice: str | None = None


# ---------------------------------------------------------------------------
# Plan response (aggregate)
# ---------------------------------------------------------------------------


class Warnings(BaseModel):
    """Top-level warnings for the plan."""

    items: list[str] = Field(default_factory=list)


class PlanResponse(BaseModel):
    """Aggregate deterministic plan response."""

    budget: BudgetResponse
    timeline: TimelineResponse
    vendors: VendorsResponse
    warnings: Warnings


# ---------------------------------------------------------------------------
# Export request (re-uses plan input)
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Request body for export endpoints."""

    model_config = {"extra": "forbid"}

    plan: PlanResponse
    request: PlanRequest


# ---------------------------------------------------------------------------
# Health response
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    gemini_configured: bool
    version: str

    model_config: Any = {"json_schema_extra": {"example": {"status": "ok"}}}
