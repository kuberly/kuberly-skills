"""Stdio MCP server using FastMCP (official `mcp` Python SDK)."""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import mcp.types as types  # type: ignore[import-untyped]
from mcp.server.fastmcp import FastMCP  # type: ignore[import-untyped]

from kuberly_mcp.dispatch import dispatch_tool
from kuberly_mcp.manifest import mcp_tool_objects

# Injected by kuberly_platform.run_mcp_server (avoids importing __main__ as kuberly_platform).
RenderFn = Callable[..., str]
EmitFn = Callable[..., None]


@dataclass(frozen=True)
class AppRuntime:
    """Holds graph + callables for one stdio session (lifespan + direct access)."""

    graph: Any
    render_tool_result: RenderFn
    emit_telemetry: EmitFn


class KuberlyFastMCP(FastMCP[AppRuntime]):
    """FastMCP app with manifest-driven `tools/list` and shared dispatch/telemetry."""

    def __init__(self, runtime: AppRuntime, *, instructions: str | None) -> None:
        rt = runtime

        @asynccontextmanager
        async def lifespan(_app: FastMCP[AppRuntime]) -> AsyncIterator[AppRuntime]:
            yield rt

        super().__init__(
            name="kuberly-platform",
            instructions=instructions,
            lifespan=lifespan,
        )
        self._runtime = runtime

    async def list_tools(self) -> list[types.Tool]:  # type: ignore[override]
        """Expose exact JSON Schemas from `manifest.py` (including `format` enum)."""
        return mcp_tool_objects()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # type: ignore[override]
        """Route to `dispatch_tool` + `render_tool_result`; mirror low-level `CallToolResult` shape."""
        args = arguments or {}
        fmt = args.get("format", "compact")
        t0 = time.monotonic()
        rt = self._runtime
        try:
            result = dispatch_tool(rt.graph, name, args)
            text = rt.render_tool_result(name, result, args, rt.graph, fmt=fmt)
            rt.emit_telemetry(
                rt.graph,
                name,
                fmt,
                args,
                text,
                duration_ms=int((time.monotonic() - t0) * 1000),
                error=None,
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                isError=False,
            )
        except Exception as exc:  # noqa: BLE001 — surface to MCP client
            err = f"{type(exc).__name__}: {exc}"
            rt.emit_telemetry(
                rt.graph,
                name,
                fmt,
                args,
                "",
                duration_ms=int((time.monotonic() - t0) * 1000),
                error=err,
            )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Error: {exc}")],
                isError=True,
            )


def run_stdio_server_blocking(
    graph: Any,
    *,
    render_tool_result: RenderFn,
    emit_telemetry: EmitFn,
    server_version: str = "0.32.6",
    instructions: str | None = None,
) -> None:
    sys.stderr.write(
        f"kuberly-platform MCP (FastMCP / mcp SDK) started ({len(graph.nodes)} nodes, "
        f"{len(graph.edges)} edges)\n"
    )
    sys.stderr.flush()
    rt = AppRuntime(graph=graph, render_tool_result=render_tool_result, emit_telemetry=emit_telemetry)
    app = KuberlyFastMCP(rt, instructions=instructions)
    app._mcp_server.version = server_version  # InitializationOptions.server_version
    app.run(transport="stdio")
