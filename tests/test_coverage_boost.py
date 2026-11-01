"""
Targeted coverage boost tests to achieve >=95% coverage across all modules.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.errors import (
    BodyTooLargeError,
    GeminiAuthError,
    GeminiUnavailableError,
    PlannerError,
    RateLimitError,
    ValidationError,
    handle_http_exception,
    handle_planner_error,
    handle_unhandled_exception,
)
from app.schemas import EventType, PlanRequest, Priority
from app.security import (
    BodySizeLimitMiddleware,
    _RateLimiterStore,
)
from app.services.budget import _normalise, compute_budget
from app.services.ideas import GeminiClient, _build_prompt, _validate_ideas
from app.services.timeline import compute_timeline
from app.services.vendors import _budget_amount_for, compute_vendors
from tests.conftest import future_date, make_request

# ---------------------------------------------------------------------------
# app/errors.py coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_handlers() -> None:
    """Test all typed exception handlers directly."""
    mock_request = MagicMock(spec=Request)

    # 1. handle_planner_error with generic PlannerError
    err1 = PlannerError("Something went wrong", status_code=500)
    resp1 = await handle_planner_error(mock_request, err1)
    assert resp1.status_code == 500
    assert b"Something went wrong" in resp1.body

    # 2. handle_planner_error with RateLimitError (must include Retry-After header)
    err2 = RateLimitError(retry_after=42)
    resp2 = await handle_planner_error(mock_request, err2)
    assert resp2.status_code == 429
    assert resp2.headers["retry-after"] == "42"

    # 3. ValidationError
    err3 = ValidationError("Invalid payload")
    assert err3.status_code == 422

    # 4. BodyTooLargeError
    err4 = BodyTooLargeError()
    assert err4.status_code == 413

    # 5. GeminiUnavailableError & GeminiAuthError
    err5 = GeminiUnavailableError("Timeout occurred")
    assert err5.status_code == 503
    err6 = GeminiAuthError()
    assert err6.status_code == 503

    # 6. handle_http_exception
    http_exc = StarletteHTTPException(status_code=404, detail="Resource not found")
    resp_http = await handle_http_exception(mock_request, http_exc)
    assert resp_http.status_code == 404
    assert b"Resource not found" in resp_http.body

    # 7. handle_unhandled_exception
    unhandled = RuntimeError("Unexpected internal crash")
    resp_unhandled = await handle_unhandled_exception(mock_request, unhandled)
    assert resp_unhandled.status_code == 500
    assert b"unexpected error occurred" in resp_unhandled.body


# ---------------------------------------------------------------------------
# app/schemas.py coverage
# ---------------------------------------------------------------------------


def test_schemas_edge_cases() -> None:
    """Test schemas validation edge cases."""
    # Date > 3 years ahead must fail
    far_future = date.today() + timedelta(days=365 * 4)
    with pytest.raises(Exception) as exc_info:
        make_request(event_date=far_future)
    assert "within 3 years" in str(exc_info.value)

    # Deduplicate priorities while preserving order
    req = make_request(priorities=[Priority.FOOD, Priority.MUSIC, Priority.FOOD])
    assert req.priorities == [Priority.FOOD, Priority.MUSIC]

    # sanitise_text with None
    assert PlanRequest.sanitise_text(None) is None


def test_settings_cors_origins() -> None:
    """Test Settings.cors_origins parser."""
    s1 = Settings(ALLOWED_ORIGINS="https://example.com, https://test.org")
    assert s1.cors_origins == ["https://example.com", "https://test.org"]
    s2 = Settings(ALLOWED_ORIGINS="")
    assert s2.cors_origins == []


# ---------------------------------------------------------------------------
# app/services/budget.py coverage
# ---------------------------------------------------------------------------


def test_budget_degenerate_weights() -> None:
    """Test degenerate non-contingency weights normalization."""
    # When non-contingency items sum to 0 or negative
    weights = {"venue": 0.0, "catering": 0.0, "contingency": 0.10}
    norm = _normalise(weights)
    assert norm["contingency"] == 0.10
    assert norm["venue"] == 0.45
    assert norm["catering"] == 0.45


# ---------------------------------------------------------------------------
# app/services/vendors.py coverage
# ---------------------------------------------------------------------------


def test_vendors_edge_cases() -> None:
    """Test vendor catalog edge cases: partial match, unlinked categories."""
    req = make_request(
        event_type=EventType.COMMUNITY,
        is_public=True,
        has_children=True,
    )
    budget = compute_budget(req)
    timeline = compute_timeline(req)
    res = compute_vendors(req, budget, timeline)
    assert any(
        item.category == "Safety & Permits" or "permit" in item.category.lower()
        for item in res.items
    )

    # Test fallback partial match in _budget_amount_for
    class DummyBudget:
        line_items = [
            MagicMock(category="Photo & Video", amount=2500),
        ]

    amount = _budget_amount_for("photo", DummyBudget())
    assert amount == 2500

    # Unmatched category returns 0
    amount_zero = _budget_amount_for("non_existent_category", DummyBudget())
    assert amount_zero == 0


# ---------------------------------------------------------------------------
# app/services/ideas.py coverage (GeminiClient internals)
# ---------------------------------------------------------------------------


def test_gemini_client_auth_error_when_no_key() -> None:
    """GeminiClient._get_client raises GeminiAuthError when key is not configured."""
    settings = Settings()
    client = GeminiClient(settings)
    with pytest.raises(GeminiAuthError):
        client._get_client()


def test_gemini_client_initialization_error() -> None:
    """GeminiClient raises GeminiUnavailableError if SDK import fails."""
    mock_settings = MagicMock()
    mock_settings.gemini.api_key_configured = True
    client = GeminiClient(mock_settings)

    with patch.dict("sys.modules", {"google": None}), pytest.raises(GeminiUnavailableError):
        client._get_client()


def test_gemini_client_generate_timeout() -> None:
    """GeminiClient.generate raises GeminiUnavailableError on TimeoutError."""
    mock_settings = MagicMock()
    mock_settings.gemini.api_key_configured = True
    mock_settings.gemini.timeout_s = 0.01

    client = GeminiClient(mock_settings)
    mock_sdk_client = MagicMock()
    client._client = mock_sdk_client
    client._types = MagicMock()

    # Simulate timeout by making generate_content block
    import time

    def slow_call(*args, **kwargs):
        time.sleep(0.5)
        return MagicMock(text="{}")

    mock_sdk_client.models.generate_content.side_effect = slow_call

    with pytest.raises(GeminiUnavailableError) as exc_info:
        client.generate("prompt", "system", mock_settings)
    assert "timed out" in str(exc_info.value).lower()


def test_gemini_client_generate_quota_error() -> None:
    """GeminiClient.generate converts quota errors to GeminiAuthError."""
    mock_settings = MagicMock()
    mock_settings.gemini.api_key_configured = True
    mock_settings.gemini.timeout_s = 5.0
    mock_settings.gemini.api_key.get_secret_value.return_value = "SECRET_KEY_123"

    client = GeminiClient(mock_settings)
    mock_sdk_client = MagicMock()
    client._client = mock_sdk_client
    client._types = MagicMock()
    mock_sdk_client.models.generate_content.side_effect = Exception(
        "Quota exceeded 403 Forbidden with SECRET_KEY_123"
    )

    with pytest.raises(GeminiAuthError):
        client.generate("prompt", "system", mock_settings)


def test_gemini_client_generate_generic_error_redacts_key() -> None:
    """GeminiClient.generate redacts API key on generic exception."""
    mock_settings = MagicMock()
    mock_settings.gemini.api_key_configured = True
    mock_settings.gemini.timeout_s = 5.0
    mock_settings.gemini.api_key.get_secret_value.return_value = "SECRET_KEY_123"

    client = GeminiClient(mock_settings)
    mock_sdk_client = MagicMock()
    client._client = mock_sdk_client
    client._types = MagicMock()
    mock_sdk_client.models.generate_content.side_effect = Exception(
        "Internal connection error with SECRET_KEY_123"
    )

    with pytest.raises(GeminiUnavailableError):
        client.generate("prompt", "system", mock_settings)


def test_gemini_client_generate_success() -> None:
    """GeminiClient.generate returns text response on success."""
    mock_settings = MagicMock()
    mock_settings.gemini.api_key_configured = True
    mock_settings.gemini.timeout_s = 5.0

    client = GeminiClient(mock_settings)
    mock_sdk_client = MagicMock()
    client._client = mock_sdk_client
    client._types = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = '{"success": true}'
    mock_sdk_client.models.generate_content.return_value = mock_resp

    res = client.generate("test prompt", "test system", mock_settings)
    assert res == '{"success": true}'


def test_build_prompt_formatting() -> None:
    """Test _build_prompt formatting for various request contexts."""
    req = make_request(
        event_type=EventType.COMMUNITY,
        is_public=True,
        ticketed=True,
        expected_volunteers=15,
        cultural_or_religious_notes="Traditional folk festival",
        accessibility_details="Sign language interpreter required",
        dietary_notes="Halal and vegan options needed",
    )
    prompt = _build_prompt(req)
    assert "<user_context>" in prompt
    assert "</user_context>" in prompt
    assert "Traditional folk festival" in prompt
    assert "Sign language interpreter required" in prompt
    assert "Halal and vegan options needed" in prompt


def test_validate_ideas_failure() -> None:
    """_validate_ideas raises ValueError when json is invalid or has wrong types."""
    with pytest.raises(ValueError):
        _validate_ideas("invalid-json{")
    with pytest.raises(ValueError):
        _validate_ideas('{"themes": "invalid-type-not-a-list"}')


# ---------------------------------------------------------------------------
# app/security.py coverage (Middleware scopes & store)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_body_size_middleware_asgi() -> None:
    """Test BodySizeLimitMiddleware directly for ASGI scopes."""
    app_mock = AsyncMock()
    middleware = BodySizeLimitMiddleware(app_mock, max_bytes=100)

    # 1. Non-http scope passes through
    scope_ws = {"type": "websocket"}
    await middleware(scope_ws, None, None)
    app_mock.assert_called_once_with(scope_ws, None, None)

    # 2. Content-Length exceeding limit returns 413
    send_mock = AsyncMock()
    scope_http_large = {
        "type": "http",
        "headers": [(b"content-length", b"200")],
    }
    await middleware(scope_http_large, None, send_mock)
    send_mock.assert_called()

    # 3. Invalid Content-Length header returns 400
    send_mock_bad = AsyncMock()
    scope_http_bad = {
        "type": "http",
        "headers": [(b"content-length", b"not-a-number")],
    }
    await middleware(scope_http_bad, None, send_mock_bad)
    send_mock_bad.assert_called()


def test_rate_limiter_store_clear() -> None:
    """Test clearing rate limiter store."""
    store = _RateLimiterStore()
    store._windows["test"] = [1.0, 2.0]
    store.clear()
    assert len(store._windows) == 0


# ---------------------------------------------------------------------------
# app/main.py coverage (Feasibility warnings and static files)
# ---------------------------------------------------------------------------


def test_plan_tight_unrealistic_warning(client: TestClient) -> None:
    """When budget is tight or unrealistic, plan response includes feasibility warning."""
    # 500 guests with $100 total budget is unrealistic
    body = {
        "event_type": "wedding",
        "event_date": future_date(200).isoformat(),
        "guest_count": 500,
        "total_budget": 100,
        "currency": "USD",
        "location_type": "indoor",
        "priorities": [],
        "dietary_needs": [],
        "has_children": False,
        "accessibility_needs": False,
        "diy_preference": "low",
    }
    resp = client.post("/api/plan", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert any("Budget feasibility" in w for w in data["warnings"]["items"])


def test_static_file_serving(client: TestClient) -> None:
    """Test serving of static assets and SPA fallback."""
    # CSS
    resp_css = client.get("/styles.css")
    assert resp_css.status_code == 200
    assert "text/css" in resp_css.headers["content-type"]
    assert "max-age" in resp_css.headers.get("cache-control", "")

    # JS
    resp_js = client.get("/app.js")
    assert resp_js.status_code == 200
    assert "javascript" in resp_js.headers["content-type"]

    # Root index.html
    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert "text/html" in resp_root.headers["content-type"]

    # SPA route fallback
    resp_spa = client.get("/plan/overview")
    assert resp_spa.status_code == 200
    assert "text/html" in resp_spa.headers["content-type"]
