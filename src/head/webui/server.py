"""aiohttp web server for the Codecast WebUI dashboard.

Serves an htmx + Jinja2 server-side-rendered dashboard on a configurable
port (default 31949).  No build step needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp_jinja2
import jinja2
from aiohttp import web

from .auth import auth_middleware, requires_auth

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Route handlers -- full pages
# ---------------------------------------------------------------------------


@aiohttp_jinja2.template("dashboard.html")
async def dashboard(request: web.Request) -> dict[str, Any]:
    config = request.app.get("config")
    peers = _get_peers(config)
    return {
        "title": "Dashboard",
        "peers": peers,
        "peer_count": len(peers),
        "session_count": 0,
    }


@aiohttp_jinja2.template("peers.html")
async def peers_page(request: web.Request) -> dict[str, Any]:
    config = request.app.get("config")
    peers = _get_peers(config)
    return {
        "title": "Peers",
        "peers": peers,
    }


@aiohttp_jinja2.template("sessions.html")
async def sessions_page(request: web.Request) -> dict[str, Any]:
    return {
        "title": "Sessions",
        "sessions": [],
    }


@aiohttp_jinja2.template("settings.html")
async def settings_page(request: web.Request) -> dict[str, Any]:
    config = request.app.get("config")
    bind = request.app.get("bind", "127.0.0.1")
    return {
        "title": "Settings",
        "config": config,
        "auth_required": requires_auth(bind),
    }


@aiohttp_jinja2.template("login.html")
async def login_page(request: web.Request) -> dict[str, Any]:
    return {"title": "Login", "error": None}


# ---------------------------------------------------------------------------
# API routes -- htmx partial HTML fragments
# ---------------------------------------------------------------------------


async def api_status(request: web.Request) -> web.Response:
    """Return an HTML fragment with current system status."""
    config = request.app.get("config")
    peers = _get_peers(config)
    html = (
        f'<div class="status-grid">'
        f'<div class="status-card"><h3>Peers</h3><p class="status-value">{len(peers)}</p></div>'
        f'<div class="status-card"><h3>Sessions</h3><p class="status-value">0</p></div>'
        f'<div class="status-card"><h3>Status</h3><p class="status-value ok">Online</p></div>'
        f"</div>"
    )
    return web.Response(text=html, content_type="text/html")


async def api_peers(request: web.Request) -> web.Response:
    """Return an HTML fragment with the peer list."""
    config = request.app.get("config")
    peers = _get_peers(config)
    if not peers:
        html = '<p class="muted">No peers configured.</p>'
    else:
        rows = []
        for p in peers:
            transport = p.get("transport", "ssh")
            host = p.get("host", "-")
            rows.append(
                f"<tr><td>{p['id']}</td><td>{transport}</td><td>{host}</td>"
                f'<td><span class="badge">unknown</span></td></tr>'
            )
        html = (
            '<table class="data-table">'
            "<thead><tr><th>ID</th><th>Transport</th><th>Host</th><th>Status</th></tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody></table>"
        )
    return web.Response(text=html, content_type="text/html")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_peers(config: Any) -> list[dict[str, Any]]:
    """Extract a list of peer dicts from config (handles None gracefully)."""
    if config is None:
        return []
    peers_dict = getattr(config, "peers", None)
    if not peers_dict:
        return []
    result = []
    for pid, peer in peers_dict.items():
        result.append(
            {
                "id": pid,
                "transport": getattr(peer, "transport", "ssh"),
                "host": getattr(peer, "ssh_host", None) or getattr(peer, "address", None) or "-",
                "daemon_port": getattr(peer, "daemon_port", 9100),
            }
        )
    return result


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


async def create_app(config: Any = None, bind: str = "127.0.0.1") -> web.Application:
    """Create and configure the aiohttp application."""
    app = web.Application(middlewares=[auth_middleware])

    # Jinja2 template loader
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
    )

    # Store config and bind address
    app["config"] = config
    app["bind"] = bind
    app["session_tokens"] = set()

    # Full-page routes
    app.router.add_get("/", dashboard)
    app.router.add_get("/peers", peers_page)
    app.router.add_get("/sessions", sessions_page)
    app.router.add_get("/settings", settings_page)
    app.router.add_get("/login", login_page)

    # API routes (htmx fragments)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/peers", api_peers)

    # Static files
    app.router.add_static("/static", STATIC_DIR)

    return app


async def start_webui(
    config: Any = None,
    host: str = "127.0.0.1",
    port: int = 31949,
) -> web.AppRunner:
    """Start the WebUI server and return the runner (for cleanup)."""
    app = await create_app(config, bind=host)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"WebUI running at http://{host}:{port}")
    return runner
