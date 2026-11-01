"""Test configuration and shared fixtures."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import (
    Currency,
    DiyPreference,
    EventType,
    LocationType,
    PlanRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def future_date(days: int = 365) -> date:
    """Return a date N days in the future."""
    today = datetime.now(tz=UTC).date()
    from datetime import timedelta

    return today + timedelta(days=days)


def make_request(**overrides: Any) -> PlanRequest:
    """Build a valid PlanRequest with sensible defaults and optional overrides."""
    defaults: dict[str, Any] = {
        "event_type": EventType.WEDDING,
        "event_date": future_date(300),
        "guest_count": 80,
        "total_budget": 20000,
        "currency": Currency.USD,
        "location_type": LocationType.INDOOR,
        "priorities": [],
        "dietary_needs": [],
        "has_children": False,
        "accessibility_needs": False,
        "diy_preference": DiyPreference.MEDIUM,
    }
    defaults.update(overrides)
    return PlanRequest(**defaults)


# ---------------------------------------------------------------------------
# Settings fixture — no real API key needed
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Ensure clean rate-limiter state for each test."""
    from app.security import clear_rate_limit_store

    clear_rate_limit_store()


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Return settings with the placeholder API key (Gemini disabled)."""
    import os

    # Ensure no real .env file is loaded in tests
    os.environ["ENV"] = "test"
    os.environ["GEMINI_API_KEY"] = "YOUR_GEMINI_API_KEY_HERE"
    os.environ["RATE_LIMIT_PER_MIN"] = "1000"
    os.environ["RATE_LIMIT_IDEAS_PER_MIN"] = "100"
    from app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    """Return a FastAPI TestClient backed by test settings."""
    app = create_app(settings=test_settings)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Mock Gemini client
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gemini_client() -> MagicMock:
    """Return a mock Gemini client that returns valid ideas JSON."""
    mock = MagicMock()
    mock.generate.return_value = json.dumps(
        {
            "themes": [
                {
                    "name": "Garden Romance",
                    "description": "Lush and floral.",
                    "palette": ["blush", "sage"],
                },
                {
                    "name": "Boho Chic",
                    "description": "Relaxed and warm.",
                    "palette": ["terracotta", "cream"],
                },
                {
                    "name": "Timeless Elegance",
                    "description": "Classic.",
                    "palette": ["ivory", "black"],
                },
            ],
            "activities": ["Live music", "Photo booth", "Lawn games", "Cocktail hour"],
            "menu_suggestions": [
                "3-course dinner",
                "Grazing table",
                "Mocktail bar",
                "Dessert station",
            ],
            "decor_ideas": ["Floral arch", "Fairy lights", "Pillar candles", "Personalised menus"],
            "music_vibes": ["Jazz quartet", "Live band", "DJ set", "First dance playlist"],
            "sample_invitation": "You are cordially invited to celebrate our wedding on [Date] at [Venue].",
            "money_saving_ideas": [
                "Book on a weekday",
                "Choose seasonal flowers",
                "DIY stationery",
            ],
            "accessibility_touches": [
                "Reserved front seating",
                "Large-print menus",
                "Step-free access",
            ],
        }
    )
    return mock
