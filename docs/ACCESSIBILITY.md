# Accessibility Documentation

## Standards Compliance

This application targets **WCAG 2.2 Level AA** conformance.

## WCAG Criterion-by-Criterion Checklist

### Perceivable

| Criterion | What was done | How verified |
|-----------|--------------|--------------|
| **1.1.1 Non-text content** | All icons are `aria-hidden="true"` with adjacent visible text labels. Decorative emoji are `aria-hidden`. | HTML review + accessibility test |
| **1.3.1 Info and relationships** | Semantic HTML5 elements: `<header>`, `<nav>`, `<main>`, `<section>`, `<footer>`, `<fieldset>`, `<legend>`, `<table>`, `<caption>`, `<thead>`, `<th scope="col">`. All form controls use `<label for="">`. Error messages use `aria-describedby`. | `tests/test_accessibility.py` |
| **1.3.2 Meaningful sequence** | Content order in HTML matches logical reading order. CSS visual reordering not used. | Manual keyboard walkthrough |
| **1.3.3 Sensory characteristics** | Instructions never rely on colour alone (e.g. required fields use `*` text mark, not just red border). Status use text labels alongside colour. | Code review |
| **1.3.4 Orientation** | No CSS locks orientation. | Code review |
| **1.3.5 Identify input purpose** | `autocomplete` attributes set on all personal data inputs. | HTML review |
| **1.4.1 Use of color** | Budget feasibility status shown as text label + colour class. Budget bars have numeric text equivalent in the table. Priority badges show text. | HTML review |
| **1.4.3 Contrast (minimum)** | Colour palette designed with ≥4.5:1 text contrast in both light and dark modes. `--clr-text` (#1c1a18) on `--clr-surface` (#ffffff) = 16.7:1. Accent brown (#8b4513) on light background = 5.2:1. | CSS custom properties + WebAIM Contrast Checker |
| **1.4.4 Resize text** | All font sizes in `rem`. Layout tested at 200% zoom. No fixed-height containers that clip text. | Manual test at 200% zoom |
| **1.4.5 Images of text** | No images of text used. | Code review |
| **1.4.10 Reflow** | Responsive layout using CSS Grid `auto-fit minmax`. Tested at 320px viewport width with no horizontal scrolling. | Manual test at 320px |
| **1.4.11 Non-text contrast** | Input borders meet 3:1 against background. Focus ring is 3px solid primary blue (3:1+). | CSS review |
| **1.4.12 Text spacing** | No fixed line heights or letter spacing that would cause overflow at WCAG text-spacing values. | Code review |
| **1.4.13 Content on hover/focus** | No custom tooltips or hover-triggered content. | Code review |

### Operable

| Criterion | What was done | How verified |
|-----------|--------------|--------------|
| **2.1.1 Keyboard** | All interactive elements are keyboard-operable (native elements only). No custom widgets. Vendor cards expand via `<button>`. Status buttons are `<button>` elements. | Manual keyboard walkthrough |
| **2.1.2 No keyboard trap** | No custom focus management that traps keyboard users. Modal loading overlay does not trap focus (it is `role="status"` not a dialog). | Manual keyboard walkthrough |
| **2.4.1 Bypass blocks** | "Skip to main content" link as first focusable element, targets `#main-content`. | `tests/test_accessibility.py::test_skip_link_present` |
| **2.4.2 Page titled** | `<title>Celebration & Event Planner</title>` — meaningful and descriptive. | `tests/test_accessibility.py::test_page_has_title` |
| **2.4.3 Focus order** | Tab order follows DOM order which matches visual order. No positive `tabindex`. | `tests/test_accessibility.py::test_no_positive_tabindex` |
| **2.4.4 Link purpose** | All links have meaningful text. Navigation links are labelled by context. | HTML review |
| **2.4.6 Headings and labels** | Logical heading hierarchy: one H1, section H2s, card H3s. All inputs have visible labels. | `tests/test_accessibility.py` |
| **2.4.7 Focus visible** | Custom focus ring: `outline: 3px solid var(--clr-primary); outline-offset: 2px` on all interactive elements via `:focus-visible`. 3:1+ contrast ratio. | CSS review |
| **2.5.3 Label in name** | Button labels match their visible text. | HTML review |
| **2.5.8 Target size** | All interactive elements have `min-height: 44px; min-width: 44px`. | CSS review |

### Understandable

| Criterion | What was done | How verified |
|-----------|--------------|--------------|
| **3.1.1 Language of page** | `<html lang="en">` | `tests/test_accessibility.py::test_html_has_lang_attribute` |
| **3.2.1 On focus** | No context changes on focus. | Code review |
| **3.2.2 On input** | Community-specific fields appear on event type change — announced to screen readers via `aria-hidden` toggling. The change is user-initiated (selection), not automatic. | HTML review |
| **3.3.1 Error identification** | Error summary with `role="alert"` receives focus on submission failure. Inline errors use `aria-invalid="true"` and `aria-describedby`. | `tests/test_accessibility.py::test_error_summary_has_role_alert` |
| **3.3.2 Labels or instructions** | All inputs have visible labels with hint text (linked via `aria-describedby`). Required fields marked with `*` and `aria-required="true"`. | `tests/test_accessibility.py` |
| **3.3.3 Error suggestion** | Error messages describe the problem in plain language and link directly to the offending field. | `app.js::showErrors()` |
| **3.3.4 Error prevention** | Form requires confirmation (submit button) before sending data. Client-side validation runs before API call. | HTML + JS review |

### Robust

| Criterion | What was done | How verified |
|-----------|--------------|--------------|
| **4.1.1 Parsing** | Valid HTML5 markup. No duplicate IDs. | HTML review |
| **4.1.2 Name, role, value** | All custom controls use native HTML elements. `aria-expanded`, `aria-busy`, `aria-live`, `aria-labelledby`, `aria-describedby` used correctly. | `tests/test_accessibility.py` |
| **4.1.3 Status messages** | Results section uses `aria-live="polite"`. Loading state uses `aria-busy`. Error summary uses `role="alert"` with `aria-live="assertive"`. | `tests/test_accessibility.py::test_live_regions_have_aria_live` |

## Motion and Colour Preferences

- **`prefers-reduced-motion`**: Spinner animation disabled. Fade-in animation on ideas content disabled. Urgent task pulse animation disabled. Scroll behaviour set to `auto`.
- **`prefers-color-scheme: dark`**: Full dark mode implemented via CSS custom properties with equivalent contrast ratios.

## Print Stylesheet

A `@media print` stylesheet:
- Hides the form, navigation, hero, and export controls.
- Shows only the results section, expanding all vendor card bodies.
- Uses black text on white background for maximum print contrast.
- Prevents result cards from splitting across page breaks.

## Manual Keyboard-only Walkthrough

1. **Page load**: Focus starts on "Skip to main content" link (first tab stop).
2. **Skip link**: Press Enter → focus moves to `#main-content` (the `<main>` element has `tabindex="-1"` to receive focus).
3. **Preset buttons**: Tab to preset buttons → press Enter to load a sample.
4. **Form fields**: Tab through all form fields in logical order. Community fields appear when "Community Event" is selected.
5. **Submit**: Tab to "Generate My Plan" → press Enter → loading state announced as `role="status"`.
6. **Results**: After loading, focus moves to results heading (`#results-heading`). Results section announces via `aria-live="polite"`.
7. **Budget table**: Navigable by keyboard (table is in a `role="region"` with `tabindex="0"` scroll container).
8. **Vendor cards**: Tab to each vendor card header button → press Enter to expand/collapse.
9. **Status buttons**: Tab to status buttons within a vendor card → press Enter/Space to toggle.
10. **Export buttons**: Tab to export buttons → press Enter to trigger download.
11. **What-if**: Tab to inputs and the Recompute button → press Enter to update.

## Automated Test Coverage

The file `tests/test_accessibility.py` asserts:
- `lang` attribute on `<html>`
- Exactly one `<h1>`
- Labels for all visible inputs
- Skip link present and targeting a valid anchor
- Landmark elements: `<header>`, `<main>`, `<footer>`, `<nav>`
- No positive `tabindex`
- No inline event handlers
- `role="alert"` on error summary
- `aria-live` on results section
- `aria-busy` on loading overlay
- `aria-required="true"` on required inputs
- `<legend>` in all `<fieldset>` elements
- Non-empty button labels
- `<title>` and meta description present
