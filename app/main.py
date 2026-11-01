"""
FastAPI application factory.

Wires together middleware, routers, static file serving, and error handlers.
OpenAPI docs are enabled only in non-production environments.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.errors import (
    PlannerError,
    handle_http_exception,
    handle_planner_error,
    handle_unhandled_exception,
    handle_validation_error,
)
from app.schemas import (
    ExportRequest,
    HealthResponse,
    IdeasResponse,
    PlanRequest,
    PlanResponse,
    Warnings,
)
from app.security import (
    BodySizeLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.services.budget import compute_budget
from app.services.exports import generate_csv, generate_ics
from app.services.ideas import get_ideas
from app.services.timeline import compute_timeline
from app.services.vendors import compute_vendors

logger = logging.getLogger(__name__)

_PUBLIC_DIR = Path(__file__).parent.parent / "public"
_APP_VERSION = "1.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    """
    Application factory — creates and configures the FastAPI app.

    Accepts optional settings override for testing.
    """
    cfg = settings or get_settings()

    logging.basicConfig(level=cfg.log_level)

    app = FastAPI(
        title="Celebration & Event Planner",
        description="GenAI-powered planning assistant for weddings, birthdays, and community events.",
        version=_APP_VERSION,
        docs_url="/docs" if cfg.openapi_enabled else None,
        redoc_url="/redoc" if cfg.openapi_enabled else None,
        openapi_url="/openapi.json" if cfg.openapi_enabled else None,
    )

    # -----------------------------------------------------------------------
    # Middleware (applied in reverse registration order — first registered runs last)
    # -----------------------------------------------------------------------
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        default_limit=cfg.rate_limit_per_min,
        ideas_limit=cfg.rate_limit_ideas_per_min,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=cfg.max_body_size_bytes)

    # CORS — restrictive by default
    origins = cfg.cors_origins or []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Accept"],
    )

    # -----------------------------------------------------------------------
    # Error handlers
    # -----------------------------------------------------------------------
    app.add_exception_handler(PlannerError, handle_planner_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handle_unhandled_exception)

    # -----------------------------------------------------------------------
    # API Routes
    # -----------------------------------------------------------------------

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    async def health_check(cfg: Settings = Depends(get_settings)) -> HealthResponse:
        """Health check endpoint — returns service status and AI configuration."""
        return HealthResponse(
            status="ok",
            gemini_configured=cfg.gemini.api_key_configured,
            version=_APP_VERSION,
        )

    @app.post("/api/plan", response_model=PlanResponse, tags=["plan"])
    async def plan(req: PlanRequest, cfg: Settings = Depends(get_settings)) -> PlanResponse:
        """
        Compute a deterministic event plan.

        Returns budget breakdown, timeline, vendor checklist, and warnings.
        This endpoint is fast (<50 ms) because it never calls external services.
        """
        budget = compute_budget(req)
        timeline = compute_timeline(req)
        vendors = compute_vendors(req, budget, timeline)

        # Collect global warnings
        all_warnings = list(timeline.warnings)
        if budget.feasibility.status.value in {"tight", "unrealistic"}:
            all_warnings.append(
                f"Budget feasibility: {budget.feasibility.status.value}. "
                f"{budget.feasibility.explanation}"
            )

        return PlanResponse(
            budget=budget,
            timeline=timeline,
            vendors=vendors,
            warnings=Warnings(items=all_warnings),
        )

    @app.post("/api/ideas", response_model=IdeasResponse, tags=["ideas"])
    async def ideas(req: PlanRequest, cfg: Settings = Depends(get_settings)) -> IdeasResponse:
        """
        Fetch personalised event ideas from Gemini (or curated fallback).

        This endpoint is separate from /api/plan so the deterministic plan
        renders instantly while ideas load asynchronously.
        """
        # Compute budget first to get feasibility status for curated fallback tier
        budget = compute_budget(req)
        feasibility_status = budget.feasibility.status.value
        return await get_ideas(req, feasibility_status=feasibility_status, settings=cfg)

    @app.post("/api/export/ics", tags=["export"])
    async def export_ics(body: ExportRequest) -> Response:
        """Export the event timeline as an iCalendar (.ics) file."""
        ics_bytes = generate_ics(body.request, body.plan.timeline)
        return Response(
            content=ics_bytes,
            media_type="text/calendar",
            headers={
                "Content-Disposition": 'attachment; filename="event-timeline.ics"',
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/export/csv", tags=["export"])
    async def export_csv(body: ExportRequest) -> Response:
        """Export the budget and vendor checklist as a CSV file."""
        csv_bytes = generate_csv(body.request, body.plan)
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="event-plan.csv"',
                "Cache-Control": "no-store",
            },
        )

    # -----------------------------------------------------------------------
    # Static file serving
    # -----------------------------------------------------------------------
    if _PUBLIC_DIR.exists():
        # Serve static assets with cache headers
        app.mount("/static", StaticFiles(directory=str(_PUBLIC_DIR)), name="static")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            """Serve the SPA index.html for all non-API routes."""
            requested = _PUBLIC_DIR / full_path
            if requested.is_file() and requested.suffix in {".css", ".js", ".ico", ".png", ".svg"}:
                return FileResponse(
                    str(requested),
                    headers={"Cache-Control": "public, max-age=3600, immutable"},
                )
            return FileResponse(
                str(_PUBLIC_DIR / "index.html"),
                headers={"Cache-Control": "no-cache"},
            )

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by uvicorn and Vercel)
# ---------------------------------------------------------------------------
app = create_app()
