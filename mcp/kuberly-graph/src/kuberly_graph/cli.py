"""Stdlib argparse CLI: serve / call / version."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from . import __version__


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import configure, mcp

    configure(repo_root=args.repo, persist_dir=args.persist_dir)

    transport = args.transport
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        # FastMCP exposes host/port via settings.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # Live web dashboard rides on the same Starlette app via FastMCP's
        # custom_route mechanism — no extra port, no second uvicorn.
        from .dashboard import register_dashboard

        register_dashboard(mcp)
        mcp_path = getattr(mcp.settings, "streamable_http_path", "/mcp")
        print(
            f"kuberly-graph MCP listening on http://{args.host}:{args.port}{mcp_path}",
            file=sys.stderr,
        )
        print(
            f"dashboard at  http://{args.host}:{args.port}/dashboard",
            file=sys.stderr,
        )
        mcp.run(transport="streamable-http")
    else:
        print(f"unknown transport {transport}", file=sys.stderr)
        return 2
    return 0


def _cmd_call(args: argparse.Namespace) -> int:
    """Spawn an in-process FastMCP run, call one tool, print the result.

    We use `mcp.client.stdio` to talk to a freshly-spawned subprocess so the
    behaviour matches what an external MCP host (Claude Code, etc.) sees.
    """
    arguments: dict[str, Any] = {}
    if args.args:
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"error: --args is not valid JSON: {exc}", file=sys.stderr)
            return 2
    if not isinstance(arguments, dict):
        print("error: --args must decode to a JSON object", file=sys.stderr)
        return 2

    from .client import call_tool

    # Spawn this same package in stdio mode and dispatch the call. The repo /
    # persist_dir flow over env vars so the subprocess picks them up before
    # any tool runs.
    cmd_parts: list[str] = [sys.executable, "-m", "kuberly_graph", "serve", "--transport", "stdio"]
    env = dict(os.environ)
    if args.repo:
        env["KUBERLY_REPO"] = args.repo
    if args.persist_dir:
        env["KUBERLY_PERSIST_DIR"] = args.persist_dir

    endpoint = {"stdio_cmd": cmd_parts, "env": env}
    try:
        result = call_tool(endpoint, args.tool, arguments)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kuberly-graph")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the FastMCP server")
    p_serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--repo", default=os.environ.get("KUBERLY_REPO", "."))
    p_serve.add_argument(
        "--persist-dir",
        default=os.environ.get("KUBERLY_PERSIST_DIR", ".kuberly"),
        help="GraphStore persist dir (default: .kuberly)",
    )
    p_serve.set_defaults(fn=_cmd_serve)

    p_call = sub.add_parser(
        "call",
        help="Spawn an embedded server and call one tool",
        epilog=(
            "Quick refresh after `aws sso login` + `kubectl` + ai-agent-tool MCP wiring:\n"
            "  kuberly-graph call regenerate_all\n"
            "Single layer: kuberly-graph call regenerate_layer --args '{\"layer\":\"k8s\"}'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_call.add_argument("tool", help="Tool name (e.g. regenerate_all, regenerate_layer)")
    p_call.add_argument(
        "--args",
        default=None,
        help="JSON object of arguments (default: {} — many tools take no args)",
    )
    p_call.add_argument("--repo", default=os.environ.get("KUBERLY_REPO", "."))
    p_call.add_argument(
        "--persist-dir",
        default=os.environ.get("KUBERLY_PERSIST_DIR", ".kuberly"),
    )
    p_call.set_defaults(fn=_cmd_call)

    p_version = sub.add_parser("version", help="Print the package version")
    p_version.set_defaults(fn=_cmd_version)

    args = parser.parse_args(argv)
    rc = args.fn(args)
    sys.exit(rc if isinstance(rc, int) else 0)


if __name__ == "__main__":
    main()
