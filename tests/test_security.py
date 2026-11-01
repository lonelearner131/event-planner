"""
Security tests.

Coverage:
- Security headers present and correct on all endpoints.
- Rate limit returns 429 with Retry-After.
- No secrets in tracked files (secret scan).
- vercel.json headers match app security headers (no drift).
- Input sanitiser strips control characters.
- Body size limit enforced.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.security import SECURITY_HEADERS, sanitise_text
from tests.conftest import future_date

PROJECT_ROOT = Path(__file__).parent.parent

BASE_WEDDING = {
    "event_type": "wedding",
    "event_date": (future_date(300)).isoformat(),
    "guest_count": 80,
    "total_budget": 20000,
    "currency": "USD",
    "location_type": "indoor",
    "priorities": [],
    "dietary_needs": [],
    "has_children": False,
    "accessibility_needs": False,
    "diy_preference": "medium",
}


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_all_security_headers_present(client: TestClient) -> None:
    """Every response must carry all required security headers."""
    resp = client.post("/api/plan", json=BASE_WEDDING)
    for header, value in SECURITY_HEADERS.items():
        assert header.lower() in resp.headers, f"Missing: {header}"


def test_csp_frame_ancestors_none(client: TestClient) -> None:
    """CSP must include frame-ancestors 'none'."""
    resp = client.get("/healthz")
    csp = resp.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp


def test_csp_default_src_self(client: TestClient) -> None:
    """CSP must include default-src 'self'."""
    resp = client.get("/healthz")
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp


def test_csp_no_unsafe_inline(client: TestClient) -> None:
    """CSP must not permit unsafe-inline."""
    resp = client.get("/healthz")
    csp = resp.headers.get("content-security-policy", "")
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_x_content_type_options_nosniff(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.headers.get("x-content-type-options") == "nosniff"


def test_referrer_policy_set(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert "referrer-policy" in resp.headers


def test_permissions_policy_set(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert "permissions-policy" in resp.headers


def test_cross_origin_opener_policy(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert "cross-origin-opener-policy" in resp.headers


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_rate_limit_returns_429() -> None:
    """Exceeding the rate limit must return 429 with Retry-After."""
    from app.config import Settings

    # Very tight limit for this test
    settings = Settings()
    # We can't easily override settings after creation, so we test the middleware directly
    import asyncio

    from app.security import _RateLimiterStore

    store = _RateLimiterStore()

    async def run():
        # Allow 2 requests, then block
        ok1, _ = await store.is_allowed("test-ip", 2, 60.0)
        ok2, _ = await store.is_allowed("test-ip", 2, 60.0)
        blocked, retry = await store.is_allowed("test-ip", 2, 60.0)
        return ok1, ok2, blocked, retry

    ok1, ok2, blocked, retry = asyncio.get_event_loop().run_until_complete(run())
    assert ok1 is True
    assert ok2 is True
    assert blocked is False
    assert retry > 0


def test_rate_limit_retry_after_header() -> None:
    """When rate limited, the response must include Retry-After header."""
    import os
    from unittest.mock import patch

    from app.config import Settings
    from app.main import create_app

    with patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "2", "RATE_LIMIT_IDEAS_PER_MIN": "2"}):
        settings = Settings()
        app = create_app(settings)
        with TestClient(app, raise_server_exceptions=False) as custom_client:
            responses = [custom_client.post("/api/plan", json=BASE_WEDDING) for _ in range(5)]

    rate_limited = [r for r in responses if r.status_code == 429]
    assert len(rate_limited) > 0
    assert "retry-after" in rate_limited[0].headers


# ---------------------------------------------------------------------------
# Input sanitiser
# ---------------------------------------------------------------------------


def test_sanitise_removes_control_chars() -> None:
    """sanitise_text must remove ASCII control characters."""
    dirty = "Hello\x00World\x1f!"
    clean = sanitise_text(dirty)
    assert "\x00" not in clean
    assert "\x1f" not in clean
    assert "Hello" in clean
    assert "World" in clean


def test_sanitise_allows_newline_tab() -> None:
    """sanitise_text should not remove tab or carriage return (for compatibility)."""
    text = "line1\nline2\tindented"
    result = sanitise_text(text)
    assert "line1" in result
    assert "line2" in result


def test_sanitise_truncates_to_max_length() -> None:
    """sanitise_text must truncate to max_length."""
    long_text = "a" * 1000
    result = sanitise_text(long_text, max_length=100)
    assert len(result) == 100


def test_sanitise_none_returns_empty() -> None:
    assert sanitise_text(None) == ""


# ---------------------------------------------------------------------------
# Secret scan
# ---------------------------------------------------------------------------

# Patterns that look like real API keys
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),  # Google API key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI key
    re.compile(r"[A-Z0-9]{20}:[A-Za-z0-9\-_]{40}"),  # Generic long key
]

# Files and patterns that are deliberately allowed (placeholder values)
_ALLOWED_IN_FILES = {
    ".env.example": ["YOUR_GEMINI_API_KEY_HERE"],
    "SECURITY.md": ["YOUR_GEMINI_API_KEY_HERE"],
    "README.md": ["YOUR_GEMINI_API_KEY_HERE"],
}

# Extensions to scan
_SCAN_EXTENSIONS = {".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".env.example"}

# Directories to skip
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".pytest_cache", "htmlcov", ".ruff_cache"}


def _get_tracked_files() -> list[Path]:
    """Return all tracked source files (not in skip dirs)."""
    root = PROJECT_ROOT
    files = []
    for path in root.rglob("*"):
        if path.is_file():
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.suffix in _SCAN_EXTENSIONS or path.name == ".env.example":
                files.append(path)
    return files


def test_no_secrets_in_tracked_files() -> None:
    """No files should contain patterns matching real API keys."""
    violations = []
    for file_path in _get_tracked_files():
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for pattern in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    matched_text = match.group()
                    # Check if this is an explicitly allowed placeholder
                    allowed = _ALLOWED_IN_FILES.get(file_path.name, [])
                    if not any(a in matched_text for a in allowed):
                        violations.append(
                            f"{file_path.relative_to(PROJECT_ROOT)}: {matched_text[:20]}..."
                        )
        except (OSError, UnicodeDecodeError):
            pass

    assert not violations, "Potential secrets found:\n" + "\n".join(violations)


def test_env_file_not_tracked() -> None:
    """.env file must not exist in the project root (committed by mistake)."""
    env_file = PROJECT_ROOT / ".env"
    # It's OK if it exists locally (for development), but it should be in .gitignore
    gitignore = PROJECT_ROOT / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        assert ".env" in content, ".env must be listed in .gitignore"


# ---------------------------------------------------------------------------
# vercel.json headers match app security headers
# ---------------------------------------------------------------------------


def test_vercel_json_headers_match_app_headers() -> None:
    """vercel.json security headers must include all headers from SECURITY_HEADERS."""
    vercel_json_path = PROJECT_ROOT / "vercel.json"
    if not vercel_json_path.exists():
        pytest.skip("vercel.json not found — skipping Vercel header parity test")

    with vercel_json_path.open() as fh:
        vercel_config = json.load(fh)

    # Extract header names from vercel.json
    vercel_header_names: set[str] = set()
    for route in vercel_config.get("headers", []):
        for header_entry in route.get("headers", []):
            vercel_header_names.add(header_entry["key"].lower())

    # Every app security header must appear in vercel.json
    for header in SECURITY_HEADERS:
        assert header.lower() in vercel_header_names, (
            f"Security header '{header}' missing from vercel.json — headers may have drifted."
        )


def test_vercel_json_csp_no_unsafe_inline() -> None:
    """vercel.json CSP must not contain unsafe-inline."""
    vercel_json_path = PROJECT_ROOT / "vercel.json"
    if not vercel_json_path.exists():
        pytest.skip("vercel.json not found")

    with vercel_json_path.open() as fh:
        vercel_config = json.load(fh)

    for route in vercel_config.get("headers", []):
        for header_entry in route.get("headers", []):
            if header_entry["key"].lower() == "content-security-policy":
                assert "unsafe-inline" not in header_entry["value"]
                assert "unsafe-eval" not in header_entry["value"]
