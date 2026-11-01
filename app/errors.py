"""
Typed exceptions and safe error handlers.

All handlers return generic, user-safe messages.  Detailed information
is logged server-side without PII or secrets.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed application exceptions
# ---------------------------------------------------------------------------


class PlannerError(Exception):
    """Base exception for all planner-domain errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class ValidationError(PlannerError):
    """Invalid input from the caller."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422)


class GeminiUnavailableError(PlannerError):
    """Gemini API is unavailable or returned an unusable response."""

    def __init__(self, reason: str = "AI service unavailable") -> None:
        super().__init__(reason, status_code=503)


class GeminiAuthError(PlannerError):
    """Gemini API key is missing or invalid — do not retry."""

    def __init__(self) -> None:
        super().__init__("AI service not configured", status_code=503)


class RateLimitError(PlannerError):
    """Request rate limit exceeded."""

    def __init__(self, retry_after: int = 60) -> None:
        super().__init__("Rate limit exceeded", status_code=429)
        self.retry_after = retry_after


class BodyTooLargeError(PlannerError):
    """Request body exceeds the configured limit."""

    def __init__(self) -> None:
        super().__init__("Request body too large", status_code=413)


# ---------------------------------------------------------------------------
# Error handlers (registered on the FastAPI app)
# ---------------------------------------------------------------------------


def _request_id() -> str:
    """Generate a short opaque request ID for log correlation."""
    return uuid.uuid4().hex[:12]


async def handle_planner_error(request: Request, exc: PlannerError) -> JSONResponse:
    """Handle domain-level planner errors."""
    rid = _request_id()
    logger.error("PlannerError [rid=%s] status=%d: %s", rid, exc.status_code, str(exc))
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc), "request_id": rid},
        headers=headers,
    )


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request validation errors with friendly messages."""
    rid = _request_id()
    errors = []
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err["loc"] if p != "body")
        errors.append({"field": loc, "message": err["msg"]})
    logger.warning("Validation error [rid=%s]: %d field(s)", rid, len(errors))
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation failed", "errors": errors, "request_id": rid},
    )


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle Starlette HTTP exceptions generically."""
    rid = _request_id()
    logger.warning("HTTPException [rid=%s] status=%d", rid, exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "request_id": rid},
    )


async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler — never expose internal details."""
    rid = _request_id()
    logger.exception("Unhandled exception [rid=%s]", rid)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred.", "request_id": rid},
    )
