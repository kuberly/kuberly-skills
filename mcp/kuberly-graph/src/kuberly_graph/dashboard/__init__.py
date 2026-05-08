"""Live web dashboard mounted on FastMCP's streamable-http transport.

Exposes:
  GET  /dashboard                — SPA shell (HTML)
  GET  /dashboard/static/<file>  — bundled CSS/JS
  GET  /api/v1/...               — JSON wrappers around existing tools

The dashboard is registered via :func:`register_dashboard`, which uses
``FastMCP.custom_route`` so the routes ride along on the same Starlette app
that serves ``/mcp``. No new MCP tools are added.
"""

from __future__ import annotations

from .routes import register_dashboard

__all__ = ["register_dashboard"]
