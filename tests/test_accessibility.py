"""
Accessibility structure tests.

Parses index.html with BeautifulSoup and asserts:
- lang attribute present on <html>
- Exactly one <h1>
- Every <input> and <select> has an associated <label>
- A skip link present
- Landmark elements: header, main, footer
- No positive tabindex
- No inline event handlers (onclick, onchange, etc.)
- All form inputs have aria-describedby or aria-required where appropriate
- Error summary has role=alert
- Live regions use aria-live
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from bs4 import BeautifulSoup, Tag

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

# Try public/ first, fall back to app/static/ for backwards compat
_PROJECT_ROOT = Path(__file__).parent.parent
_HTML_PATHS = [
    _PROJECT_ROOT / "public" / "index.html",
    _PROJECT_ROOT / "app" / "static" / "index.html",
]
HTML_FILE = next((p for p in _HTML_PATHS if p.exists()), None)

pytestmark = pytest.mark.skipif(
    not BS4_AVAILABLE or HTML_FILE is None,
    reason="BeautifulSoup4 or index.html not available",
)


def get_soup() -> BeautifulSoup:
    """Parse index.html and return a BeautifulSoup object."""
    content = HTML_FILE.read_text(encoding="utf-8")
    return BeautifulSoup(content, "lxml")


# ---------------------------------------------------------------------------
# Language attribute
# ---------------------------------------------------------------------------


def test_html_has_lang_attribute() -> None:
    """<html> must have lang='en' (or another valid lang code)."""
    soup = get_soup()
    html_tag = soup.find("html")
    assert html_tag is not None, "<html> tag not found"
    lang = html_tag.get("lang", "")
    assert lang, "<html> missing lang attribute"
    assert len(lang) >= 2, f"lang attribute too short: '{lang}'"


# ---------------------------------------------------------------------------
# Heading hierarchy
# ---------------------------------------------------------------------------


def test_exactly_one_h1() -> None:
    """Page must have exactly one <h1>."""
    soup = get_soup()
    h1_tags = soup.find_all("h1")
    assert len(h1_tags) == 1, f"Expected 1 <h1>, found {len(h1_tags)}"


def test_h1_is_not_empty() -> None:
    """<h1> must have non-empty text."""
    soup = get_soup()
    h1 = soup.find("h1")
    assert h1 is not None
    assert h1.get_text(strip=True), "<h1> is empty"


# ---------------------------------------------------------------------------
# Labels for inputs
# ---------------------------------------------------------------------------


def _get_all_interactive_inputs(soup: BeautifulSoup) -> list[Tag]:
    """Return all inputs and selects that are not hidden or type=submit/button/reset."""
    skip_types = {"hidden", "submit", "button", "reset"}
    inputs = []
    for tag in soup.find_all(["input", "select"]):
        if isinstance(tag, Tag):
            input_type = tag.get("type", "text").lower()
            if input_type not in skip_types:
                inputs.append(tag)
    return inputs


def test_all_inputs_have_labels() -> None:
    """Every visible input/select must have an associated <label> or aria-label."""
    soup = get_soup()
    inputs = _get_all_interactive_inputs(soup)

    unlabelled = []
    for inp in inputs:
        input_id = inp.get("id")
        aria_label = inp.get("aria-label")
        aria_labelledby = inp.get("aria-labelledby")

        if aria_label or aria_labelledby:
            continue  # aria-label or aria-labelledby is sufficient

        # Check for wrapping label — works even without an id
        parent_label = inp.find_parent("label")
        if parent_label:
            continue

        if input_id:
            label = soup.find("label", {"for": input_id})
            if label:
                continue

        # Skip checkboxes/radios grouped by fieldset+legend (WCAG 1.3.1)
        input_type = inp.get("type", "").lower()
        if input_type in {"checkbox", "radio"} and inp.find_parent("fieldset"):
            continue

        unlabelled.append(f"<{inp.name} id='{input_id}' name='{inp.get('name')}'>")

    assert not unlabelled, "Inputs without labels:\n" + "\n".join(unlabelled)

    assert not unlabelled, "Inputs without labels:\n" + "\n".join(unlabelled)


# ---------------------------------------------------------------------------
# Skip link
# ---------------------------------------------------------------------------


def test_skip_link_present() -> None:
    """Page must contain a skip-to-main-content link."""
    soup = get_soup()
    skip_links = soup.find_all("a", class_="skip-link")
    if not skip_links:
        # Also check for any link whose text contains "skip"
        skip_links = [a for a in soup.find_all("a") if "skip" in a.get_text(strip=True).lower()]
    assert skip_links, "No skip link found on page"


def test_skip_link_targets_main_content() -> None:
    """Skip link must point to #main-content or a valid main landmark."""
    soup = get_soup()
    skip_links = soup.find_all("a", class_="skip-link")
    if not skip_links:
        skip_links = [a for a in soup.find_all("a") if "skip" in a.get_text(strip=True).lower()]
    if skip_links:
        href = skip_links[0].get("href", "")
        assert href.startswith("#"), f"Skip link href should be an anchor, got: {href}"
        # The target should exist
        target_id = href.lstrip("#")
        target = soup.find(id=target_id)
        assert target is not None, f"Skip link target #{target_id} not found in page"


# ---------------------------------------------------------------------------
# Landmark elements
# ---------------------------------------------------------------------------


def test_header_landmark_present() -> None:
    soup = get_soup()
    header = soup.find("header")
    assert header is not None, "No <header> element found"


def test_main_landmark_present() -> None:
    soup = get_soup()
    main = soup.find("main")
    assert main is not None, "No <main> element found"


def test_footer_landmark_present() -> None:
    soup = get_soup()
    footer = soup.find("footer")
    assert footer is not None, "No <footer> element found"


def test_nav_present() -> None:
    soup = get_soup()
    nav = soup.find("nav")
    assert nav is not None, "No <nav> element found"


# ---------------------------------------------------------------------------
# No positive tabindex
# ---------------------------------------------------------------------------


def test_no_positive_tabindex() -> None:
    """No element should have tabindex > 0 (disrupts natural focus order)."""
    soup = get_soup()
    violations = []
    for tag in soup.find_all(tabindex=True):
        try:
            ti = int(tag.get("tabindex", "0"))
            if ti > 0:
                violations.append(f"<{tag.name} id='{tag.get('id')}' tabindex='{ti}'>")
        except ValueError:
            pass
    assert not violations, "Elements with positive tabindex found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# No inline event handlers
# ---------------------------------------------------------------------------


_INLINE_HANDLERS = [
    "onclick",
    "onchange",
    "onsubmit",
    "onkeyup",
    "onkeydown",
    "onmouseover",
    "onfocus",
    "onblur",
    "oninput",
]


def test_no_inline_event_handlers() -> None:
    """No element should have inline event handler attributes (strict CSP)."""
    soup = get_soup()
    violations = []
    for handler in _INLINE_HANDLERS:
        for tag in soup.find_all(attrs={handler: True}):
            violations.append(f"<{tag.name} id='{tag.get('id')}' {handler}='{tag.get(handler)}'>")
    assert not violations, "Inline event handlers found:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# ARIA roles
# ---------------------------------------------------------------------------


def test_error_summary_has_role_alert() -> None:
    """Error summary must have role=alert."""
    soup = get_soup()
    error_summary = soup.find(id="error-summary")
    assert error_summary is not None, "Error summary element not found"
    assert error_summary.get("role") == "alert", "Error summary missing role='alert'"


def test_live_regions_have_aria_live() -> None:
    """Dynamic result regions must have aria-live."""
    soup = get_soup()
    results_section = soup.find(id="results-section")
    assert results_section is not None
    aria_live = results_section.get("aria-live")
    assert aria_live in {"polite", "assertive"}, (
        f"Results section missing or wrong aria-live: {aria_live}"
    )


def test_loading_overlay_has_aria_busy() -> None:
    soup = get_soup()
    loading = soup.find(id="loading-overlay")
    assert loading is not None
    assert loading.get("aria-busy") is not None


# ---------------------------------------------------------------------------
# Form accessibility
# ---------------------------------------------------------------------------


def test_required_fields_have_aria_required() -> None:
    """Required inputs must have aria-required='true'."""
    soup = get_soup()
    required_inputs = soup.find_all(attrs={"required": True})
    for inp in required_inputs:
        aria_req = inp.get("aria-required")
        assert aria_req == "true", f"<{inp.name} id='{inp.get('id')}'> missing aria-required='true'"


def test_fieldsets_have_legends() -> None:
    """All <fieldset> elements must have a <legend>."""
    soup = get_soup()
    fieldsets = soup.find_all("fieldset")
    for fs in fieldsets:
        legend = fs.find("legend")
        assert legend is not None, f"<fieldset> missing <legend>: {str(fs)[:100]}"


def test_no_empty_button_labels() -> None:
    """Buttons must have accessible labels (text or aria-label)."""
    soup = get_soup()
    buttons = soup.find_all("button")
    for btn in buttons:
        text = btn.get_text(strip=True)
        aria_label = btn.get("aria-label", "")
        aria_labelledby = btn.get("aria-labelledby", "")
        assert text or aria_label or aria_labelledby, (
            f"Button with id='{btn.get('id')}' has no accessible label"
        )


# ---------------------------------------------------------------------------
# Title tag
# ---------------------------------------------------------------------------


def test_page_has_title() -> None:
    soup = get_soup()
    title = soup.find("title")
    assert title is not None, "Page missing <title>"
    assert title.get_text(strip=True), "<title> is empty"


def test_page_has_meta_description() -> None:
    soup = get_soup()
    meta_desc = soup.find("meta", attrs={"name": "description"})
    assert meta_desc is not None, "Page missing meta description"
    content = meta_desc.get("content", "")
    assert len(content) > 20, "Meta description too short"


# ---------------------------------------------------------------------------
# Charset and viewport
# ---------------------------------------------------------------------------


def test_page_has_charset() -> None:
    soup = get_soup()
    charset = soup.find("meta", charset=True)
    assert charset is not None, "Page missing charset meta tag"


def test_page_has_viewport() -> None:
    soup = get_soup()
    viewport = soup.find("meta", attrs={"name": "viewport"})
    assert viewport is not None, "Page missing viewport meta tag"
