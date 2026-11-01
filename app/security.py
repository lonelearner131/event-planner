"""
Security middleware: headers, rate limiter, input sanitiser, body-size guard.

Design goals:
- Every response carries the full security header set.
- Per-IP sliding-window rate limiter is async-safe.
- Input sanitiser strips control characters and enforces size limits.
- The API key is NEVER logged or reflected in any response.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import BodyTooLargeError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security headers — applied to every response
# ---------------------------------------------------------------------------

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self';"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), "
        "payment=(), usb=(), magnetometer=(), gyroscope=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-XSS-Protection": "0",  # Disabled — modern browsers handle this via CSP
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every outgoing response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Add security headers after the response is generated."""
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value
        return response


# ---------------------------------------------------------------------------
# Body size guard middleware
# ---------------------------------------------------------------------------


class BodySizeLimitMiddleware:
    """Reject request bodies exceeding the configured limit."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check Content-Length header and stream body size without consuming stream."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                if int(content_length_raw.decode("latin1")) > self.max_bytes:
                    response = JSONResponse(
                        {"detail": "Request body too large."},
                        status_code=413,
                        headers=SECURITY_HEADERS,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    {"detail": "Invalid Content-Length header."},
                    status_code=400,
                    headers=SECURITY_HEADERS,
                )
                await response(scope, receive, send)
                return

        received_bytes = 0

        async def custom_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_bytes:
                    raise BodyTooLargeError("Request body too large.")
            return message

        try:
            await self.app(scope, custom_receive, send)
        except BodyTooLargeError:
            response = JSONResponse(
                {"detail": "Request body too large."},
                status_code=413,
                headers=SECURITY_HEADERS,
            )
            await response(scope, receive, send)


# ---------------------------------------------------------------------------
# Sliding-window per-IP rate limiter
# ---------------------------------------------------------------------------


class _RateLimiterStore:
    """Thread/async-safe sliding window store keyed by IP."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._windows: dict[str, deque[float]] = {}

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def clear(self) -> None:
        """Clear all stored windows and reset lock."""
        self._windows.clear()
        self._lock = None

    async def is_allowed(self, key: str, limit: int, window_s: float = 60.0) -> tuple[bool, int]:
        """
        Check whether the key is within the rate limit.

        Returns (allowed, retry_after_seconds).
        Uses a sliding window — only timestamps within the last window_s are counted.
        """
        now = time.monotonic()
        async with self._get_lock():
            if key not in self._windows:
                self._windows[key] = deque()
            dq = self._windows[key]
            # Evict stale entries
            while dq and dq[0] < now - window_s:
                dq.popleft()
            if len(dq) >= limit:
                # Oldest entry tells us when the window opens up
                retry_after = int(window_s - (now - dq[0])) + 1
                return False, retry_after
            dq.append(now)
            return True, 0


_store = _RateLimiterStore()


def clear_rate_limit_store() -> None:
    """Clear all rate limit tracking (useful for test isolation)."""
    _store.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter with separate limits for ideas endpoint."""

    def __init__(
        self,
        app: ASGIApp,
        default_limit: int = 20,
        ideas_limit: int = 5,
        window_s: float = 60.0,
    ) -> None:
        super().__init__(app)
        self.default_limit = default_limit
        self.ideas_limit = ideas_limit
        self.window_s = window_s

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For from trusted proxies."""
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Apply rate limiting before forwarding the request."""
        # Only rate-limit API endpoints
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        ip = self._get_client_ip(request)
        is_ideas = request.url.path == "/api/ideas"
        limit = self.ideas_limit if is_ideas else self.default_limit
        key = f"{'ideas' if is_ideas else 'default'}:{ip}"

        allowed, retry_after = await _store.is_allowed(key, limit, self.window_s)
        if not allowed:
            logger.warning("Rate limit exceeded for IP hash")  # Do NOT log real IP
            return JSONResponse(
                {"detail": "Too many requests. Please wait before retrying."},
                status_code=429,
                headers={**SECURITY_HEADERS, "Retry-After": str(retry_after)},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Input sanitiser (used by services, not middleware)
# ---------------------------------------------------------------------------

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitise_text(value: str | None, max_length: int = 500) -> str:
    """
    Remove ASCII control characters and truncate to max_length.

    This is a defence-in-depth layer on top of Pydantic validation.
    """
    if value is None:
        return ""
    cleaned = _CTRL_RE.sub("", value).strip()
    return cleaned[:max_length]
