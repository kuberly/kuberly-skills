"""Register dashboard routes on the FastMCP Starlette app.

We attach handlers via ``mcp.custom_route``; the FastMCP SDK splices them
into the Starlette app returned by ``streamable_http_app()`` so a single
ASGI process serves both ``/mcp`` and the dashboard.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from . import api


_STATIC_DIR = Path(__file__).resolve().parent / "static"


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

    # ---------- JSON API ----------
    mcp_app.custom_route("/api/v1/layers", methods=["GET"])(api.layers_endpoint)
    mcp_app.custom_route("/api/v1/stats", methods=["GET"])(api.stats_endpoint)
    mcp_app.custom_route("/api/v1/graph", methods=["GET"])(api.graph_endpoint)
    mcp_app.custom_route("/api/v1/nodes", methods=["GET"])(api.nodes_endpoint)
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}/neighbors", methods=["GET"])(
        api.node_neighbors_endpoint
    )
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}/blast", methods=["GET"])(
        api.node_blast_endpoint
    )
    mcp_app.custom_route("/api/v1/nodes/{node_id:path}", methods=["GET"])(
        api.node_detail_endpoint
    )
    mcp_app.custom_route("/api/v1/search", methods=["GET"])(api.search_endpoint)
    mcp_app.custom_route("/api/v1/search/cross", methods=["GET"])(
        api.cross_search_endpoint
    )
    mcp_app.custom_route("/api/v1/anomalies", methods=["GET"])(api.anomalies_endpoint)
    mcp_app.custom_route("/api/v1/service/{name:path}/mermaid", methods=["GET"])(
        api.service_mermaid_endpoint
    )
    mcp_app.custom_route("/api/v1/service/{name:path}", methods=["GET"])(
        api.service_one_pager_endpoint
    )
    # ---- v0.52.0 dashboard polish ----
    mcp_app.custom_route("/api/v1/meta-overview", methods=["GET"])(
        api.meta_overview_endpoint
    )
    mcp_app.custom_route("/api/v1/aws-services", methods=["GET"])(
        api.aws_services_endpoint
    )
    mcp_app.custom_route("/api/v1/compliance", methods=["GET"])(
        api.compliance_endpoint
    )
    mcp_app.custom_route("/api/v1/communities", methods=["GET"])(
        api.communities_endpoint
    )
