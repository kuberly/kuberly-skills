"""Heuristic troubleshooting entrypoint for the consolidated Kuberly MCP.

This tool deliberately starts with the local graph: resolve the likely subject,
summarize blast radius, and decide whether live cluster data is needed. Only
runtime-shaped incidents call the ai-agent-tool MCP.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..client import call_tool
from ..layers._util import KuberlyGraph
from ..orchestrator import build_mcp_endpoint
from ..server import SERVER_CONFIG, mcp
from ..store import open_store
from ..store._mcp_discovery import discover_live_mcp
from .platform import _layer_summary, _relation_hints, _route, _score_nodes


_RUNTIME_KEYWORDS = {
    "crash": ("crash", "crashloop", "restart", "oom", "killed", "pod"),
    "latency": ("slow", "latency", "timeout", "p95", "p99", "duration"),
    "errors": ("error", "exception", "5xx", "500", "failing", "failed"),
    "saturation": ("cpu", "memory", "saturation", "throttle", "disk", "capacity"),
    "logs": ("log", "logs", "loki"),
    "traces": ("trace", "traces", "tempo"),
    "metrics": ("metric", "metrics", "prometheus", "promql"),
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "service",
    "the",
    "to",
    "with",
}


def _load_graph(repo_root: str | None) -> KuberlyGraph:
    repo = repo_root or SERVER_CONFIG.get("repo_root", ".")
    graph = KuberlyGraph(str(repo))
    graph.build()
    return graph


def _tokens(text: str) -> list[str]:
    return [
        t
        for t in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", text.lower())
        if len(t) > 2 and t not in _STOPWORDS
    ]


def _classify(subject: str) -> dict[str, Any]:
    lower = subject.lower()
    scores: dict[str, int] = {}
    for kind, words in _RUNTIME_KEYWORDS.items():
        scores[kind] = sum(1 for word in words if word in lower)
    incident_kind = max(scores, key=scores.get) if any(scores.values()) else "graph"
    return {
        "incident_kind": incident_kind,
        "needs_live": incident_kind != "graph",
        "signals": {k: v for k, v in scores.items() if v},
    }


def _find_graph_matches(
    graph: KuberlyGraph,
    subject: str,
    environment: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    terms = _tokens(subject)
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for node_id, node in graph.nodes.items():
        if environment and node.get("environment") not in (None, "", environment):
            continue
        haystack = " ".join(
            str(v).lower()
            for v in (
                node_id,
                node.get("label", ""),
                node.get("name", ""),
                node.get("type", ""),
                node.get("module", ""),
                node.get("application", ""),
                node.get("component", ""),
            )
        )
        score = sum(
            3 if term in node_id.lower() else 1
            for term in terms
            if term in haystack
        )
        if score:
            scored.append((score, node_id, node))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"score": score, "id": node_id, "node": node}
        for score, node_id, node in scored[: max(1, limit)]
    ]


def _blast_summary(
    graph: KuberlyGraph,
    node_id: str,
    max_depth: int,
) -> dict[str, Any]:
    forward: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if not src or not tgt:
            continue
        forward[src].append(tgt)
        reverse[tgt].append(src)

    def walk(adj: dict[str, list[str]]) -> list[dict[str, Any]]:
        seen: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(node_id, 0)]
        while queue:
            current, depth = queue.pop(0)
            if current in seen or depth > max_depth:
                continue
            seen[current] = depth
            for neighbor in adj.get(current, []):
                if neighbor not in seen:
                    queue.append((neighbor, depth + 1))
        seen.pop(node_id, None)
        return [
            {
                "id": nid,
                "depth": depth,
                "type": graph.nodes.get(nid, {}).get("type"),
                "label": graph.nodes.get(nid, {}).get("label"),
            }
            for nid, depth in sorted(
                seen.items(),
                key=lambda item: (item[1], item[0]),
            )[:25]
        ]

    upstream = walk(reverse)
    downstream = walk(forward)
    return {
        "node": node_id,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "upstream_sample": upstream,
        "downstream_sample": downstream,
    }


def _resolve_endpoint(
    repo_root: str,
    mcp_url: str | None,
    mcp_stdio: str | None,
) -> dict | None:
    if mcp_url or mcp_stdio:
        return build_mcp_endpoint(mcp_url, mcp_stdio)
    return discover_live_mcp(repo_root)


def _persisted_context(
    query: str,
    environment: str | None,
    limit: int,
    persist_dir: str | None,
) -> dict[str, Any]:
    store_path = persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")
    store = open_store(Path(store_path).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()
    matches = _score_nodes(nodes, query, environment, limit)
    match_ids = {str(match.get("id")) for match in matches if match.get("id")}
    return {
        "summary": _layer_summary(nodes, edges, store.stats()),
        "routes": _route(query),
        "matches": matches,
        "relation_hints": _relation_hints(edges, match_ids),
    }


def _safe_live_call(endpoint: dict, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = call_tool(endpoint, tool, args)
    except Exception as exc:  # noqa: BLE001 - MCP transport errors are data here.
        return {"tool": tool, "args": args, "error": str(exc)}
    return {"tool": tool, "args": args, "result": payload}


def _log_query(subject: str, namespace: str | None, limit: int) -> dict[str, Any]:
    selector = subject.replace('"', "'")
    log_selector = f'{{namespace="{namespace}"}}' if namespace else '{namespace=~".+"}'
    query = f'{log_selector} |= "{selector}"'
    return {"query": query, "limit": limit}


def _metric_query(kind: str, namespace: str | None) -> str:
    ns_filter = f'{{namespace="{namespace}"}}' if namespace else ""
    if kind == "saturation":
        return (
            "topk(10, sum by (pod) "
            f"(rate(container_cpu_usage_seconds_total{ns_filter}[5m])))"
        )
    if kind == "latency":
        return (
            "histogram_quantile(0.95, sum by (le, service) "
            "(rate(http_request_duration_seconds_bucket[5m])))"
        )
    return "sum by (service) (rate(http_requests_total{status=~\"5..\"}[5m]))"


@mcp.tool()
def troubleshoot(
    subject: str,
    environment: str | None = None,
    namespace: str | None = None,
    resource_kind: str | None = None,
    resource_name: str | None = None,
    use_live: bool = True,
    mcp_url: str | None = None,
    mcp_stdio: str | None = None,
    repo_root: str | None = None,
    persist_dir: str | None = None,
    graph_match_limit: int = 8,
    live_limit: int = 50,
) -> dict[str, Any]:
    """Troubleshoot a Kuberly issue from graph context first, live cluster second.

    The tool classifies the subject with lightweight heuristics, resolves likely
    graph nodes, summarizes graph blast radius, and only calls the discovered
    ai-agent-tool MCP when the issue looks runtime-shaped (crash, latency,
    errors, saturation, logs, metrics, traces) and ``use_live`` is true.
    """
    repo = repo_root or SERVER_CONFIG.get("repo_root", ".")
    index = _persisted_context(subject, environment, graph_match_limit, persist_dir)
    graph = _load_graph(repo)
    heuristic = _classify(subject)
    cold_matches = _find_graph_matches(graph, subject, environment, graph_match_limit)
    persisted_matches = index["matches"]
    primary = (
        persisted_matches[0]["id"]
        if persisted_matches
        else cold_matches[0]["id"]
        if cold_matches
        else None
    )
    blast = _blast_summary(graph, primary, 3) if primary else None

    result: dict[str, Any] = {
        "subject": subject,
        "environment": environment,
        "namespace": namespace,
        "heuristic": heuristic,
        "platform_index": {
            "summary": index["summary"],
            "routes": index["routes"],
            "relation_hints": index["relation_hints"],
        },
        "graph": {
            "matches": persisted_matches or cold_matches,
            "persisted_matches": persisted_matches,
            "cold_matches": cold_matches,
            "primary": primary,
            "blast_radius": blast,
        },
        "live": {
            "called": False,
            "reason": "not needed by heuristic" if not heuristic["needs_live"] else None,
            "calls": [],
        },
        "next_steps": [],
    }

    if not persisted_matches and not cold_matches:
        result["next_steps"].append(
            "Regenerate graph layers or provide a more specific component/application name."
        )

    if not use_live:
        result["live"]["reason"] = "disabled by use_live=false"
        return result
    if not heuristic["needs_live"]:
        result["next_steps"].append("Use graph tools for dependency, ownership, and blast-radius follow-up.")
        return result

    endpoint = _resolve_endpoint(str(repo), mcp_url, mcp_stdio)
    if not endpoint:
        result["live"]["reason"] = "no ai-agent-tool MCP endpoint discovered"
        result["next_steps"].append("Add ai-agent-tool to .mcp.json or pass mcp_url/mcp_stdio.")
        return result

    result["live"]["called"] = True
    result["live"]["endpoint"] = "url" if endpoint.get("url") else "stdio"
    calls: list[dict[str, Any]] = result["live"]["calls"]
    calls.append(_safe_live_call(endpoint, "observability_status", {}))

    kind = heuristic["incident_kind"]
    if resource_kind and resource_name:
        args: dict[str, Any] = {"kind": resource_kind, "name": resource_name}
        if namespace:
            args["namespace"] = namespace
        calls.append(_safe_live_call(endpoint, "describe_resource", args))
    elif namespace is None:
        calls.append(_safe_live_call(endpoint, "list_namespaces", {}))

    if kind in {"crash", "errors", "logs"}:
        calls.append(
            _safe_live_call(
                endpoint,
                "query_logs",
                _log_query(subject, namespace, live_limit),
            )
        )
    if kind in {"latency", "saturation", "metrics", "errors"}:
        calls.append(
            _safe_live_call(
                endpoint,
                "query_metrics",
                {"query": _metric_query(kind, namespace)},
            )
        )
    if kind in {"latency", "traces"}:
        calls.append(
            _safe_live_call(
                endpoint,
                "query_traces",
                {"query": subject, "limit": min(live_limit, 20)},
            )
        )

    result["next_steps"].append("Correlate live findings with the primary graph node and blast-radius samples.")
    return result
