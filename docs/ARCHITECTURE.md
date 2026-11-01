# Architecture Documentation

## Overview

Celebration & Event Planner uses a **deterministic-engine-plus-AI** architecture. The reliable, fast, testable work (arithmetic, scheduling, filtering) is done by pure Python functions. The LLM is used only for creative personalisation where a wrong answer is acceptable.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser (SPA)                            │
│  index.html + styles.css + app.js                               │
│  - No frameworks, no CDN, no inline scripts                     │
│  - DOM mutations via textContent/createElement only             │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS (same-origin)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Application                          │
│                                                                 │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────────────────┐ │
│  │  Middleware  │  │  Routers   │  │    Error Handlers       │ │
│  │  - GZip     │  │  /api/plan │  │  - Typed exceptions     │ │
│  │  - Security │  │  /api/ideas│  │  - Generic messages     │ │
│  │    Headers  │  │  /api/exp. │  │  - No PII in logs       │ │
│  │  - Rate     │  │  /healthz  │  └─────────────────────────┘ │
│  │    Limiter  │  └─────┬──────┘                              │ │
│  │  - Body     │        │                                     │ │
│  │    Limit    │        ▼                                     │ │
│  └──────────────┘  ┌──────────────────────────────────────┐   │ │
│                    │          Services Layer               │   │ │
│                    │                                       │   │ │
│  ┌─────────────────┼───────────────────────────────────┐  │   │ │
│  │  Deterministic  │              AI Layer             │  │   │ │
│  │  (no network)   │                                   │  │   │ │
│  │                 │  ┌────────────────────────────┐   │  │   │ │
│  │  budget.py ─────┤  │       ideas.py             │   │  │   │ │
│  │  timeline.py ───┤  │  - Prompt builder          │   │  │   │ │
│  │  vendors.py ────┤  │  - TTL LRU cache           │   │  │   │ │
│  │  exports.py ────┤  │  - Schema validation       │   │  │   │ │
│  │                 │  │  - Curated fallback        │   │  │   │ │
│  └─────────────────┘  └──────────┬─────────────────┘   │  │   │ │
│                                  │                      │  │   │ │
│                    └─────────────┼──────────────────────┘  │   │ │
│                                  ▼                          │   │ │
│                    ┌─────────────────────────┐              │   │ │
│                    │   google-genai SDK      │              │   │ │
│                    │   gemini-3.5-flash-lite │              │   │ │
│                    └─────────────┬───────────┘              │   │ │
│                                  │                          │   │ │
└──────────────────────────────────┼──────────────────────────────┘
                                   │ (may fail gracefully)
                                   ▼
                       ┌─────────────────────┐
                       │  Gemini API         │
                       │  (external service) │
                       └─────────────────────┘
```

## Request Flow

### POST /api/plan (deterministic, <50 ms)

```
Browser → POST /api/plan
  → Pydantic validation (422 on failure)
  → budget.compute_budget(req) → BudgetResponse
  → timeline.compute_timeline(req) → TimelineResponse
  → vendors.compute_vendors(req, budget, timeline) → VendorsResponse
  → Collect warnings
  → Return PlanResponse (JSON)
```

### POST /api/ideas (AI, async, with fallback)

```
Browser → POST /api/ideas
  → Pydantic validation
  → Compute budget for feasibility tier
  → Check SHA-256 LRU cache → HIT → return cached IdeasResponse
  → MISS → api_key_configured? → NO → curated fallback
  → YES → GeminiClient.generate(prompt, system)
    → Timeout? → curated fallback
    → Auth error? → curated fallback (no retry)
    → Invalid JSON? → retry once → curated fallback
    → Schema validation failure? → retry once → curated fallback
    → SUCCESS → cache result → return IdeasResponse{source: "gemini"}
```

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/healthz` | None | Health check |
| `POST` | `/api/plan` | None | Deterministic plan (budget + timeline + vendors) |
| `POST` | `/api/ideas` | None | AI/curated personalised ideas |
| `POST` | `/api/export/ics` | None | Timeline as iCalendar file |
| `POST` | `/api/export/csv` | None | Budget + checklist as CSV |

## Data Flow — How Context Changes the Plan

### Example 1: Tight Budget Wedding

- Input: `total_budget=10000, guest_count=200, currency=USD, event_type=wedding`
- Per-guest: `$50` — below the workable threshold of `$120`
- `budget.py::_feasibility()` classifies as `tight`
- Suggestions computed: reduce to ~83 guests (`10000 / 120`), or increase budget by `$14,000`
- Timeline compressed if `lead_days < 365`

### Example 2: Community Event with Outdoor + Children

- Input: `event_type=community, location_type=outdoor, has_children=True`
- `budget.py::_apply_context_adjustments()` adds `rentals_tent_weather` (6%) and `kids_entertainment` (5%)
- Weights renormalised to preserve 10% contingency
- `timeline.py::_outdoor_tasks()` injects weather-backup task due 60 days before event
- `timeline.py::_children_tasks()` injects children's zone task due 30 days before
- `vendors.py::compute_vendors()` adds `entertainer` to the vendor list

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `app/config.py` | Only place env vars are read; typed, frozen settings |
| `app/schemas.py` | All Pydantic request/response models; `extra="forbid"` |
| `app/security.py` | Security headers, rate limiter, body size guard, sanitiser |
| `app/errors.py` | Typed exceptions, safe handlers, request ID correlation |
| `app/main.py` | App factory, middleware wiring, routing |
| `app/services/budget.py` | Allocation engine (pure functions) |
| `app/services/timeline.py` | Backward planner (pure functions) |
| `app/services/vendors.py` | Vendor checklist builder (pure functions) |
| `app/services/ideas.py` | Gemini client, cache, prompt, validation, fallback |
| `app/services/exports.py` | ICS and CSV generation |
| `app/data/` | Static JSON data files loaded once at startup |
| `public/` | Frontend: HTML, CSS, JS — no build step |
