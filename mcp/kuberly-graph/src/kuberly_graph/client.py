"""MCP client helpers — port of the legacy `scripts/kuberly_graph_client.py`.

Two connection modes:
  - endpoint = {"url": "http://host:port/mcp"}  -> streamable HTTP transport
  - endpoint = {"stdio_cmd": "<cmd>" | [argv]}  -> spawn subprocess, stdio
                + optional `env` dict

Hard-fails on any connection error (re-raised as ConnectionError) so callers
can sys.exit(1) with a clean message.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import shlex
from typing import Any, Awaitable, Callable, TypeVar


T = TypeVar("T")


def _run_coro_sync(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run an async coroutine from sync code, robust to a running event loop.

    If there is no running loop in the current thread, use ``asyncio.run``
    directly (the CLI path). If a loop is already running (FastMCP server
    transport — sync tool dispatch is invoked from inside the loop), we
    cannot call ``asyncio.run`` here; instead, hand the coroutine off to a
    short-lived worker thread that owns its own fresh event loop.

    ``coro_factory`` is a zero-arg callable that returns a fresh coroutine
    object — we may need to construct the coroutine in the worker thread so
    its event-loop bindings resolve there, not in the caller's thread.
    """

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(coro_factory())

    def _worker() -> T:
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_worker).result()


def _endpoint_str(endpoint: dict) -> str:
    if endpoint.get("url"):
        return f"url={endpoint['url']}"
    if endpoint.get("stdio_cmd"):
        cmd = endpoint["stdio_cmd"]
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return f"stdio_cmd={cmd}"
    return repr(endpoint)


def _validate_endpoint(endpoint: dict) -> None:
    has_url = bool(endpoint.get("url"))
    has_stdio = bool(endpoint.get("stdio_cmd"))
    if has_url == has_stdio:
        raise ValueError(
            "endpoint must set exactly one of {'url', 'stdio_cmd'}; got "
            f"{sorted(k for k, v in endpoint.items() if v)}"
        )


def _extract_json_from_call_result(result: Any) -> Any:
    # FastMCP wraps non-string return values in `structuredContent` (MCP
    # ≥1.27). When present, it's the canonical result — prefer it over the
    # text content blocks (which split list-shaped results across one
    # TextContent per item).
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured
    content = getattr(result, "content", None)
    if not content:
        return None
    if len(content) > 1:
        items: list[Any] = []
        ok = True
        for part in content:
            text = getattr(part, "text", None)
            if text is None:
                ok = False
                break
            try:
                items.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                ok = False
                break
        if ok:
            return items
    for part in content:
        text = getattr(part, "text", None)
        if text is None:
            continue
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    return None


def _resources_from_payload(payload: Any) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("items", "resources", "data", "result"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if "metadata" in payload:
            return [payload]
    return []


def _import_mcp_session():
    try:
        from mcp import ClientSession  # type: ignore[import-not-found]
    except Exception as exc:
        raise ConnectionError(f"mcp Python SDK not installed: {exc}") from exc
    return ClientSession


async def _open_session(endpoint: dict, runner):
    _validate_endpoint(endpoint)
    ClientSession = _import_mcp_session()

    async def _run(read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await runner(session)

    try:
        if endpoint.get("url"):
            from mcp.client.streamable_http import streamablehttp_client  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {}
            headers = endpoint.get("headers")
            if isinstance(headers, dict) and headers:
                kwargs["headers"] = headers
            async with streamablehttp_client(endpoint["url"], **kwargs) as ctx:
                read, write = ctx[0], ctx[1]
                return await _run(read, write)
        else:
            from mcp import StdioServerParameters  # type: ignore[import-not-found]
            from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

            cmd = endpoint["stdio_cmd"]
            if isinstance(cmd, str):
                parts = shlex.split(cmd)
            elif isinstance(cmd, list):
                parts = list(cmd)
            else:
                raise ValueError("stdio_cmd must be a str or list")
            if not parts:
                raise ValueError("stdio_cmd is empty")
            env = endpoint.get("env")
            params = StdioServerParameters(
                command=parts[0], args=parts[1:], env=env
            )
            async with stdio_client(params) as (read, write):
                return await _run(read, write)
    except ConnectionError:
        raise
    except Exception as exc:
        raise ConnectionError(
            f"MCP unreachable at {_endpoint_str(endpoint)}: {exc}"
        ) from exc


async def call_mcp_tool(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Generic MCP tool call.

    Returns the parsed JSON content of the first TextContent block, or
    `{"error": <message>}` if the tool itself raised. Raises ConnectionError
    on transport failure.
    """

    async def _runner(session):
        try:
            result = await session.call_tool(tool_name, arguments or {})
        except Exception as exc:
            return {"error": f"tool {tool_name} failed: {exc}"}
        return _extract_json_from_call_result(result)

    return await _open_session(endpoint, _runner)


def call_tool(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Sync wrapper around `call_mcp_tool`.

    Safe to invoke from inside a running event loop (e.g. the FastMCP
    transport): in that case the coroutine is dispatched to a worker thread
    with its own loop. CLI invocations have no running loop and use
    ``asyncio.run`` directly.
    """
    return _run_coro_sync(lambda: call_mcp_tool(endpoint, tool_name, arguments))


async def fetch_live_resources(
    endpoint: dict,
    kinds: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict]]:
    """Connect to an MCP server and call resources_list for each
    (apiVersion, kind). Returns {(apiVersion, kind): [resource, ...]}.
    Hard-fails on transport error.
    """
    out: dict[tuple[str, str], list[dict]] = {}

    async def _runner(session):
        for api_version, kind in kinds:
            try:
                result = await session.call_tool(
                    "resources_list",
                    {"apiVersion": api_version, "kind": kind},
                )
            except Exception as exc:
                out[(api_version, kind)] = []
                print(f"  warn: resources_list {api_version}/{kind} failed: {exc}")
                continue
            payload = _extract_json_from_call_result(result)
            out[(api_version, kind)] = _resources_from_payload(payload)
        return None

    await _open_session(endpoint, _runner)
    return out


def fetch_live_resources_sync(
    endpoint: dict,
    kinds: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict]]:
    """Sync wrapper around ``fetch_live_resources``.

    Safe under a running event loop (FastMCP transport): in that case the
    coroutine runs in a worker thread with its own fresh loop. CLI path
    uses ``asyncio.run`` directly.
    """
    return _run_coro_sync(lambda: fetch_live_resources(endpoint, kinds))


def call_mcp_tool_sync(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Sync alias for :func:`call_tool` — kept for layer-side clarity."""
    return call_tool(endpoint, tool_name, arguments)
