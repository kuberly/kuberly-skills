"""Platform index tools.

These are the intended first calls for agents. They summarize what graph data is
available, resolve likely nodes across all layers, and route the question to the
right graph tool before any live MCP handoff is considered.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..layers import leaf_layer_names
from ..server import SERVER_CONFIG, mcp
from ..store import open_store


_STOPWORDS = {
    "about",
    "after",
    "and",
    "are",
    "because",
    "for",
    "from",
    "has",
    "have",
    "into",
    "issue",
    "need",
    "our",
    "please",
    "service",
    "show",
    "the",
    "this",
    "that",
    "what",
    "when",
    "where",
    "with",
}

_INTENT_RULES: list[tuple[str, tuple[str, ...], list[str], list[str]]] = [
    (
        "runtime_troubleshooting",
        ("crash", "crashloop", "error", "failing", "latency", "oom", "pod", "slow", "timeout"),
        ["troubleshoot", "incident_context", "service_lineage"],
        ["k8s", "argo", "logs", "metrics", "traces"],
    ),
    (
        "impact_analysis",
        ("affect", "blast", "break", "depends", "dependency", "impact", "upstream", "downstream"),
        ["blast_radius", "trace_data_flow", "node_explain"],
        ["cold", "components", "applications", "dependency"],
    ),
    (
        "security_review",
        ("iam", "irsa", "permission", "public", "role", "secret", "security", "sg", "vulnerable"),
        ["find_open_security_groups", "iam_role_assumers", "node_explain"],
        ["iam", "network", "secrets", "compliance", "k8s"],
    ),
    (
        "cost_capacity",
        ("capacity", "cost", "cpu", "memory", "rightsize", "saturation", "waste"),
        ["summarize_environment", "find_high_cardinality_metrics", "troubleshoot"],
        ["cost", "metrics", "k8s", "components"],
    ),
    (
        "deployment_state",
        ("argo", "deploy", "drift", "environment", "rendered", "state", "sync"),
        ["drift", "summarize_environment", "query_nodes"],
        ["state", "rendered", "argo", "applications", "components"],
    ),
    (
        "docs_code_lookup",
        ("code", "doc", "docs", "explain", "file", "implementation", "skill"),
        ["semantic_search", "node_explain", "query_nodes"],
        ["code", "docs", "meta"],
    ),
]


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def _node_text(node: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "id",
        "type",
        "label",
        "name",
        "env",
        "environment",
        "namespace",
        "kind",
        "module",
        "application",
        "component",
        "address",
        "path",
    ):
        value = node.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _env_match(node: dict[str, Any], env: str | None) -> bool:
    if not env:
        return True
    return node.get("environment") == env or node.get("env") == env


def _score_nodes(
    nodes: list[dict[str, Any]],
    query: str,
    env: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    terms = _tokens(query)
    if not terms:
        return []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for node in nodes:
        if not _env_match(node, env):
            continue
        node_id = str(node.get("id") or "")
        haystack = _node_text(node)
        score = 0
        for term in terms:
            if term == node_id.lower() or term == str(node.get("label") or "").lower():
                score += 8
            elif term in node_id.lower():
                score += 4
            elif term in haystack:
                score += 1
        if score:
            scored.append((score, node_id, node))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "score": score,
            "id": node_id,
            "type": node.get("type"),
            "layer": node.get("layer"),
            "label": node.get("label"),
            "env": node.get("environment") or node.get("env"),
            "namespace": node.get("namespace"),
            "node": node,
        }
        for score, node_id, node in scored[: max(1, limit)]
    ]


def _route(query: str) -> list[dict[str, Any]]:
    lower = query.lower()
    routes: list[dict[str, Any]] = []
    for intent, keywords, tools, layers in _INTENT_RULES:
        hits = [keyword for keyword in keywords if keyword in lower]
        if not hits:
            continue
        routes.append(
            {
                "intent": intent,
                "score": len(hits),
                "matched_keywords": hits,
                "recommended_tools": tools,
                "relevant_layers": layers,
                "may_need_live": intent == "runtime_troubleshooting",
            }
        )
    routes.sort(key=lambda item: (-item["score"], item["intent"]))
    if routes:
        return routes
    return [
        {
            "intent": "graph_lookup",
            "score": 0,
            "matched_keywords": [],
            "recommended_tools": ["semantic_search", "query_nodes", "node_explain"],
            "relevant_layers": leaf_layer_names(),
            "may_need_live": False,
        }
    ]


def _layer_summary(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], stats: dict) -> dict[str, Any]:
    node_layers = Counter(str(n.get("layer") or "unknown") for n in nodes)
    edge_layers = Counter(str(e.get("layer") or "unknown") for e in edges)
    node_types_by_layer: dict[str, Counter[str]] = defaultdict(Counter)
    envs: set[str] = set()
    namespaces: set[str] = set()
    for node in nodes:
        layer = str(node.get("layer") or "unknown")
        node_types_by_layer[layer][str(node.get("type") or "unknown")] += 1
        env = node.get("environment") or node.get("env")
        if env:
            envs.add(str(env))
        namespace = node.get("namespace")
        if namespace:
            namespaces.add(str(namespace))
    layers: dict[str, Any] = {}
    for layer in sorted(set(node_layers) | set(edge_layers) | set(stats.get("per_layer", {}))):
        layer_stats = stats.get("per_layer", {}).get(layer, {})
        layers[layer] = {
            "nodes": node_layers.get(layer, layer_stats.get("nodes", 0)),
            "edges": edge_layers.get(layer, layer_stats.get("edges", 0)),
            "last_refresh": layer_stats.get("last_refresh"),
            "top_node_types": dict(node_types_by_layer[layer].most_common(8)),
        }
    return {
        "mode": stats.get("mode"),
        "persist_dir": stats.get("persist_dir"),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "populated_layers": [layer for layer, data in layers.items() if data["nodes"] or data["edges"]],
        "layers": layers,
        "environments": sorted(envs),
        "namespaces_sample": sorted(namespaces)[:30],
    }


def _relation_hints(edges: list[dict[str, Any]], match_ids: set[str]) -> list[dict[str, Any]]:
    if not match_ids:
        return []
    hints: list[dict[str, Any]] = []
    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in match_ids and target not in match_ids:
            continue
        hints.append(
            {
                "source": source,
                "target": target,
                "relation": edge.get("relation"),
                "layer": edge.get("layer"),
            }
        )
        if len(hints) >= 40:
            break
    return hints


@mcp.tool()
def platform_index(
    query: str | None = None,
    environment: str | None = None,
    limit: int = 12,
    persist_dir: str | None = None,
) -> dict[str, Any]:
    """Index and route a user question across every Kuberly graph layer.

    Call this first. It reports which graph layers are populated, identifies
    likely nodes across the persisted graph store, recommends the next graph
    tools to call, and says whether live ai-agent-tool handoff may be needed.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()
    stats = store.stats()
    summary = _layer_summary(nodes, edges, stats)
    routes = _route(query or "")
    matches = _score_nodes(nodes, query or "", environment, limit) if query else []
    match_ids = {str(match.get("id")) for match in matches if match.get("id")}
    semantic: dict[str, Any] | None = None
    if query:
        hits = store.semantic_search(query=query, limit=min(max(1, limit), 20))
        if hits and isinstance(hits[0], dict) and "error" in hits[0]:
            semantic = {"available": False, "error": hits[0]["error"]}
        else:
            semantic = {"available": True, "hits": hits[:limit]}

    primary_route = routes[0] if routes else None
    return {
        "query": query,
        "environment": environment,
        "summary": summary,
        "routing": {
            "primary_intent": primary_route.get("intent") if primary_route else None,
            "routes": routes,
            "entrypoint": "platform_index",
            "live_handoff_policy": "Only call ai-agent-tool after graph context indicates runtime signal is needed.",
        },
        "matches": matches,
        "semantic": semantic,
        "relation_hints": _relation_hints(edges, match_ids),
        "next_steps": _next_steps(routes, matches, summary),
    }


def _next_steps(
    routes: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[str]:
    steps: list[str] = []
    if summary["total_nodes"] == 0:
        return ["Run regenerate_all so platform_index can route against populated graph layers."]
    primary = routes[0] if routes else None
    if primary:
        tools = ", ".join(primary.get("recommended_tools", [])[:3])
        steps.append(f"Use {tools} for the {primary.get('intent')} path.")
    if matches:
        steps.append(f"Start with node_explain on {matches[0]['id']} for full context.")
    else:
        steps.append("Use semantic_search or a more specific service/module name to resolve a graph node.")
    if primary and primary.get("may_need_live"):
        steps.append("Call troubleshoot next; it will forward to ai-agent-tool only if live signal is needed.")
    return steps
