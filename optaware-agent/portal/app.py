"""OptAware web portal — mounts static assets and template routes onto FastAPI."""

from __future__ import annotations

import socket
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_PORTAL_DIR = Path(__file__).parent
_TEMPLATES_DIR = _PORTAL_DIR / "templates"
_STATIC_DIR = _PORTAL_DIR / "static"

_VERSION = "0.1.0"


def _get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def setup_portal(app: FastAPI) -> None:
    """Mount static files and register template routes onto *app*.

    This function is intentionally side-effect-free with respect to the
    FastAPI application state — it only adds routes and mounts.  Call it once
    during application startup, after all API routers have been registered so
    that the catch-all redirect does not shadow any API paths.
    """

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Serve static assets under /static.  ``html=False`` keeps the mount lean.
    app.mount(
        "/static",
        StaticFiles(directory=str(_STATIC_DIR)),
        name="static",
    )

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _ctx(request: Request, page: str, title: str) -> dict:
        """Build the base template context shared by every page."""
        return {
            "request": request,
            "page": page,
            "title": title,
            "hostname": _get_hostname(),
            "version": _VERSION,
        }

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        """Redirect the root URL to the dashboard."""
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "dashboard", "Dashboard")
        return templates.TemplateResponse("dashboard.html", ctx)

    @app.get("/events", response_class=HTMLResponse, include_in_schema=False)
    async def events(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "events", "Events")
        return templates.TemplateResponse("events.html", ctx)

    @app.get("/actions", response_class=HTMLResponse, include_in_schema=False)
    async def actions(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "actions", "Actions")
        return templates.TemplateResponse("actions.html", ctx)

    @app.get("/ask", response_class=HTMLResponse, include_in_schema=False)
    async def ask(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "ask", "Ask OptAware")
        return templates.TemplateResponse("ask.html", ctx)

    @app.get("/settings", response_class=HTMLResponse, include_in_schema=False)
    async def settings(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "settings", "Settings")
        return templates.TemplateResponse("settings.html", ctx)

    @app.get("/services", response_class=HTMLResponse, include_in_schema=False)
    async def services(request: Request) -> HTMLResponse:
        ctx = _ctx(request, "services", "Services")
        return templates.TemplateResponse("services.html", ctx)

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login(request: Request) -> HTMLResponse:
        return templates.TemplateResponse("login.html", {"request": request})
