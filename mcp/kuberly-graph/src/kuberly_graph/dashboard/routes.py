"""Register dashboard routes on the FastMCP Starlette app.

We attach handlers via ``mcp.custom_route``; the FastMCP SDK splices them
into the Starlette app returned by ``streamable_http_app()`` so a single
ASGI process serves both ``/mcp`` and the dashboard.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import api


_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Comma-separated list of allowed origins for /api/v1/* requests. Default is
# "*" — fine for read-only public dashboards. Set
# DASHBOARD_CORS_ORIGINS=https://graph-ui.example.com,http://localhost:5173
# to lock down to specific frontends. The Vite dev server defaults to :5173.
_CORS_ENV_KEY = "DASHBOARD_CORS_ORIGINS"


def _cors_allow_origin(request: Request) -> str:
    """Return the origin we should echo back in `Access-Control-Allow-Origin`.

    The default ``*`` is permissive but safe — these endpoints are read-only
    and carry no auth surface. When the operator pins specific origins via
    ``$DASHBOARD_CORS_ORIGINS``, only matching origins get through (the rest
    receive ``null`` and the browser blocks them).
    """
    raw = os.getenv(_CORS_ENV_KEY, "*").strip()
    if raw == "*" or raw == "":
        return "*"
    allow = {o.strip() for o in raw.split(",") if o.strip()}
    origin = request.headers.get("origin", "")
    return origin if origin in allow else "null"


def _cors_headers(request: Request) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": _cors_allow_origin(request),
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, Accept",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }


def _wrap_cors(
    handler: Callable[[Request], Awaitable[Response]],
) -> Callable[[Request], Awaitable[Response]]:
    """Wrap an endpoint handler so its response carries CORS headers.

    Idempotent: if the underlying handler already set CORS headers (none do
    today, but defends against future changes), they are overwritten with the
    centralised values.
    """

    async def _wrapped(request: Request) -> Response:
        resp = await handler(request)
        for k, v in _cors_headers(request).items():
            resp.headers[k] = v
        return resp

    return _wrapped


def _static_response(name: str) -> Response:
    """Serve a file from the bundled `static/` dir.

    Defends against path traversal — only files directly inside _STATIC_DIR
    are served.
    """
    target = (_STATIC_DIR / name).resolve()
    if not target.is_file():
        return JSONResponse({"error": f"not found: {name}"}, status_code=404)
    try:
        target.relative_to(_STATIC_DIR)
    except ValueError:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(str(target), media_type=media_type or "application/octet-stream")


def register_dashboard(mcp_app) -> None:
    """Attach all `/dashboard*` and `/api/v1/*` routes to the FastMCP app.

    Idempotent — safe to call once per process. FastMCP internally appends
    these routes to its Starlette app at ``streamable_http_app()`` time.

    All ``/api/v1/*`` responses carry CORS headers so a separate-origin SPA
    (the new ``ui/`` Vite project) can talk to this server. Allowed origins
    are controlled by the ``DASHBOARD_CORS_ORIGINS`` env var (default ``*``).
    """

    @mcp_app.custom_route("/dashboard", methods=["GET"], name="dashboard_index")
    async def _dashboard_index(_request: Request) -> Response:
        return _static_response("index.html")

    @mcp_app.custom_route(
        "/dashboard/static/{filename:path}",
        methods=["GET"],
        name="dashboard_static",
        include_in_schema=False,
    )
    async def _dashboard_static(request: Request) -> Response:
        return _static_response(request.path_params["filename"])

    # ---------- CORS preflight ----------
    @mcp_app.custom_route(
        "/api/v1/{rest:path}",
        methods=["OPTIONS"],
        name="api_cors_preflight",
        include_in_schema=False,
    )
    async def _api_preflight(request: Request) -> Response:
        return Response(status_code=204, headers=_cors_headers(request))

    # ---------- JSON API (CORS-wrapped) ----------
    mcp_app.custom_route("/api/v1/layers", methods=["GET"])(_wrap_cors(api.layers_endpoint))
    mcp_app.custom_route("/api/v1/stats", methods=["GET"])(_wrap_cors(api.stats_endpoint))
    mcp_app.custom_route("/api/v1/graph", methods=["GET"])(_wrap_cors(api.graph_endpoint))
    mcp_app.custom_route("/api/v1/nodes", methods=["GET"])(_wrap_cors(api.nodes_endpoint))
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}/neighbors", methods=["GET"])(
        _wrap_cors(api.node_neighbors_endpoint)
    )
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}/blast", methods=["GET"])(
        _wrap_cors(api.node_blast_endpoint)
    )
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}", methods=["GET"])(
        _wrap_cors(api.node_detail_endpoint)
    )
    mcp_app.custom_route("/api/v1/search", methods=["GET"])(_wrap_cors(api.search_endpoint))
    mcp_app.custom_route("/api/v1/search/cross", methods=["GET"])(
        _wrap_cors(api.cross_search_endpoint)
    )
    mcp_app.custom_route("/api/v1/anomalies", methods=["GET"])(_wrap_cors(api.anomalies_endpoint))
    mcp_app.custom_route("/api/v1/service/{name:path}/mermaid", methods=["GET"])(
        _wrap_cors(api.service_mermaid_endpoint)
    )
    mcp_app.custom_route("/api/v1/service/{name:path}", methods=["GET"])(
        _wrap_cors(api.service_one_pager_endpoint)
    )
    # ---- v0.52.0 dashboard polish ----
    mcp_app.custom_route("/api/v1/meta-overview", methods=["GET"])(
        _wrap_cors(api.meta_overview_endpoint)
    )
    mcp_app.custom_route("/api/v1/aws-services", methods=["GET"])(
        _wrap_cors(api.aws_services_endpoint)
    )
    mcp_app.custom_route("/api/v1/compliance", methods=["GET"])(
        _wrap_cors(api.compliance_endpoint)
    )
    mcp_app.custom_route("/api/v1/communities", methods=["GET"])(
        _wrap_cors(api.communities_endpoint)
    )
