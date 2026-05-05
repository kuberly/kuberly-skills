#!/usr/bin/env python3
"""kuberly-state: live cluster observability MCP (stub).

v0.15.0 — companion to kuberly-platform. Where kuberly-platform is the
STATIC graph (modules, components, applications as declared in HCL/JSON),
kuberly-state answers RUNTIME questions: what's actually deployed, what
errors are happening, what metrics look like.

This file is a stub — every tool returns a structured "not yet
implemented" response with hooks for the consumer's observability stack
(Loki / Tempo / Prometheus / Grafana / CloudWatch / kubectl). Replace
the stubs with real backends as they become available.

Why a stub ships now:
- Personas (especially `agent-sre`) can declare it in their `tools:`
  list today, then take advantage automatically when implementations land.
- The contract is fixed in source — customer dev fills in the body.
- One MCP server per concern (graph vs runtime) keeps each focused;
  we don't bloat kuberly-platform with kubectl/Prom integration.

Stdlib only, Python 3.8+. Run as MCP stdio server:

    python3 kuberly_state.py mcp [--repo .]

Wire into the consumer's .mcp.json (manual for now; not auto-registered
via apm.yml — opt-in until the implementations are real):

    {
      "mcpServers": {
        "kuberly-state": {
          "command": "python3",
          "args": ["apm_modules/kuberly/kuberly-skills/mcp/kuberly-state/kuberly_state.py", "mcp"]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Tool definitions — the contract. Implementations are stubs; replace them
# with real backends as observability infrastructure lands.
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "pod_status",
        "description": "Live status of a Kubernetes pod. Returns phase, restarts, image, age. Stub: integrate kubectl or k8s API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "name":      {"type": "string", "description": "Pod name (exact). Use a label selector if you don't know the name."},
                "selector":  {"type": "string", "description": "Optional label selector instead of name."},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "service_status",
        "description": "Endpoints + ready replicas for a Kubernetes service or ECS service. Stub: integrate kubectl / ECS DescribeServices.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["k8s", "ecs"], "default": "k8s"},
                "namespace": {"type": "string", "description": "K8s namespace (k8s) or ECS cluster (ecs)."},
                "name":      {"type": "string"},
            },
            "required": ["namespace", "name"],
        },
    },
    {
        "name": "recent_logs",
        "description": "Recent log lines from Loki / CloudWatch. Returns matched-line count + a small sample. Stub: integrate logcli or aws logs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target":     {"type": "string", "description": "Component / service / app id to scope logs to."},
                "query":      {"type": "string", "description": "LogQL or CloudWatch insight query. Defaults to error-level filter."},
                "since":      {"type": "string", "description": "Lookback window (e.g. '15m', '1h'). Default '15m'."},
                "max_lines":  {"type": "integer", "default": 50},
            },
            "required": ["target"],
        },
    },
    {
        "name": "recent_metrics",
        "description": "Recent metrics from Prometheus / CloudWatch Metrics. Returns headline numbers (e.g. error rate, p95 latency). Stub: integrate Prom or CloudWatch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "metric": {"type": "string", "description": "Metric name or PromQL fragment."},
                "since":  {"type": "string", "default": "30m"},
            },
            "required": ["target", "metric"],
        },
    },
    {
        "name": "trace_search",
        "description": "Recent slow / failing traces from Tempo. Stub: integrate Tempo HTTP API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service":  {"type": "string"},
                "min_duration_ms": {"type": "integer", "default": 1000},
                "since":    {"type": "string", "default": "30m"},
            },
            "required": ["service"],
        },
    },
]


def _stub(tool: str, args: dict) -> dict:
    return {
        "tool": tool,
        "status": "not_implemented",
        "args": args,
        "hint": (
            "kuberly-state ships as a stub in v0.15.0. Replace this file's "
            "_dispatch() handlers with calls to your observability backend "
            "(kubectl, Loki/logcli, Prometheus HTTP API, CloudWatch, Tempo). "
            "The tool surface (TOOLS[]) is the stable contract — clients can "
            "rely on it now and get real data once handlers are filled in."
        ),
    }


def _dispatch(name: str, args: dict) -> dict:
    if name not in {t["name"] for t in TOOLS}:
        return {"error": f"unknown tool: {name}"}
    return _stub(name, args)


# ---------------------------------------------------------------------------
# MCP stdio server (minimal — same JSON-RPC shape as kuberly-platform)
# ---------------------------------------------------------------------------

def run_mcp_server() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}
        resp = _handle(method, params, rid)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def _handle(method: str, params: dict, rid: Any):
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "kuberly-state", "version": "0.1.0-stub"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        result = _dispatch(tool_name, tool_args)
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": "error" in result,
            },
        }
    return {
        "jsonrpc": "2.0", "id": rid,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="kuberly-state — live cluster observability MCP (stub)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("mcp", help="Run as MCP stdio server")
    args = parser.parse_args()
    if args.command == "mcp":
        run_mcp_server()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
