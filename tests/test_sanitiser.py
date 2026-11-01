"""
Sanitiser unit tests.

Coverage:
- Control character stripping.
- Truncation to max_length.
- None input returns empty string.
- Printable characters preserved.
"""

from __future__ import annotations

import pytest

from app.security import sanitise_text


@pytest.mark.parametrize(
    "char",
    [
        "\x00",
        "\x01",
        "\x07",
        "\x08",  # NUL, SOH, BEL, BS
        "\x0b",
        "\x0c",  # VT, FF
        "\x0e",
        "\x1f",  # SO, US
        "\x7f",  # DEL
    ],
)
def test_control_chars_removed(char: str) -> None:
    """Control characters must be stripped."""
    result = sanitise_text(f"before{char}after")
    assert char not in result
    assert "before" in result
    assert "after" in result


def test_newline_preserved() -> None:
    """Newlines are allowed through (not stripped)."""
    result = sanitise_text("line1\nline2")
    assert "\n" in result


def test_tab_preserved() -> None:
    """Tabs are allowed through."""
    result = sanitise_text("col1\tcol2")
    assert "\t" in result


def test_none_input() -> None:
    assert sanitise_text(None) == ""


def test_empty_string() -> None:
    assert sanitise_text("") == ""


def test_max_length_truncation() -> None:
    long_text = "a" * 1000
    result = sanitise_text(long_text, max_length=200)
    assert len(result) == 200


def test_default_max_length() -> None:
    long_text = "a" * 1000
    result = sanitise_text(long_text)
    assert len(result) <= 500  # Default max_length


def test_unicode_preserved() -> None:
    text = "Héllo Wörld 日本語 🎉"
    result = sanitise_text(text)
    assert "Héllo" in result
    assert "🎉" in result


def test_strip_leading_trailing_whitespace() -> None:
    result = sanitise_text("  hello world  ")
    assert result == "hello world"


def test_printable_ascii_preserved() -> None:
    """All standard printable ASCII must pass through unchanged."""
    printable = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789 !@#$%^&*()_+-=[]{}|;':\",./<>?"
    result = sanitise_text(printable)
    # Control chars removed but printable should remain
    assert "abcdefghijklmnopqrstuvwxyz" in result
    assert "0123456789" in result
