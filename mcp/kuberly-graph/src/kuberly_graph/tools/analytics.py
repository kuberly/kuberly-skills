"""Analytics tools — pure GraphStore queries (no live MCP calls)."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


@mcp.tool()
def find_log_anomalies(
    env: str | None = None,
    limit: int = 20,
    persist_dir: str | None = None,
) -> dict:
    """Top-N `log_template` nodes flagged is_anomaly=true (count > 5 AND
    >50% of occurrences at ERROR/FATAL/CRITICAL level)."""
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    out: list[dict] = []
    for n in store.all_nodes(layer="logs"):
        if n.get("type") != "log_template":
            continue
        if not n.get("is_anomaly"):
            continue
        if env and n.get("env") != env:
            continue
        out.append(n)
    out.sort(key=lambda n: int(n.get("count", 0) or 0), reverse=True)
    return {
        "env": env,
        "limit": int(limit),
        "anomalies": out[: int(limit)],
        "total_matches": len(out),
    }


@mcp.tool()
def find_high_cardinality_metrics(
    env: str | None = None,
    threshold: int = 10000,
    limit: int = 20,
    persist_dir: str | None = None,
) -> dict:
    """Top-N `metric` nodes whose `series_count` exceeds `threshold`."""
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    matches: list[dict] = []
    env_metric_ids: set[str] | None = None
    if env:
        env_metric_ids = set()
        for e in store.all_edges():
            if e.get("relation") != "instrumented_by":
                continue
            src = e.get("source") or ""
            if not src.startswith("app:"):
                continue
            try:
                _, body = src.split(":", 1)
                e_env, _app = body.split("/", 1)
            except ValueError:
                continue
            if e_env == env:
                env_metric_ids.add(e.get("target") or "")
    for n in store.all_nodes(layer="metrics"):
        if n.get("type") != "metric":
            continue
        sc = int(n.get("series_count", 0) or 0)
        if sc <= int(threshold):
            continue
        if env_metric_ids is not None and n.get("id") not in env_metric_ids:
            continue
        matches.append(n)
    matches.sort(
        key=lambda n: int(n.get("series_count", 0) or 0), reverse=True
    )
    return {
        "env": env,
        "threshold": int(threshold),
        "limit": int(limit),
        "metrics": matches[: int(limit)],
        "total_matches": len(matches),
    }


@mcp.tool()
def find_metric_owners(
    metric_name: str,
    persist_dir: str | None = None,
) -> dict:
    """Given a metric name, return every incoming edge — i.e. which
    scrape_targets / applications / modules produce or instrument it."""
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    metric_id = (
        metric_name
        if metric_name.startswith("metric:")
        else f"metric:{metric_name}"
    )
    nodes_by_id = {n.get("id"): n for n in store.all_nodes()}
    metric_node = nodes_by_id.get(metric_id)
    incoming: list[dict] = []
    for e in store.all_edges():
        if e.get("target") != metric_id:
            continue
        src_id = e.get("source") or ""
        src_node = nodes_by_id.get(src_id, {})
        incoming.append(
            {
                "source": src_id,
                "source_type": src_node.get("type", "unknown"),
                "relation": e.get("relation", ""),
                "source_label": src_node.get("label", ""),
            }
        )
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in incoming:
        grouped[row["source_type"]].append(row)
    return {
        "metric_name": metric_name,
        "metric_id": metric_id,
        "found": metric_node is not None,
        "metric": metric_node,
        "owners": dict(grouped),
        "total_owners": len(incoming),
    }


@mcp.tool()
def find_slow_operations(
    service: str | None = None,
    percentile: str = "p95",
    limit: int = 20,
    persist_dir: str | None = None,
) -> dict:
    """Top-N `operation` nodes sorted by `p50/p95/p99_ms` descending."""
    pkey = (percentile or "p95").lower()
    if pkey not in {"p50", "p95", "p99"}:
        pkey = "p95"
    field = f"{pkey}_ms"
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    matches: list[dict] = []
    for n in store.all_nodes(layer="traces"):
        if n.get("type") != "operation":
            continue
        if service and n.get("service") != service:
            continue
        matches.append(n)
    matches.sort(key=lambda n: float(n.get(field, 0) or 0), reverse=True)
    return {
        "service": service,
        "percentile": pkey,
        "limit": int(limit),
        "operations": matches[: int(limit)],
        "total_matches": len(matches),
    }


@mcp.tool()
def find_error_hotspots(
    min_error_rate: float = 0.05,
    limit: int = 20,
    persist_dir: str | None = None,
) -> dict:
    """`service` and `operation` nodes flagged is_anomaly=true (or whose
    `error_rate` exceeds `min_error_rate`), sorted by error_rate * volume desc.
    """
    threshold = float(min_error_rate)
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    rows: list[tuple[float, dict]] = []
    for n in store.all_nodes(layer="traces"):
        ntype = n.get("type")
        if ntype not in {"service", "operation"}:
            continue
        rate = float(n.get("error_rate", 0) or 0)
        if not n.get("is_anomaly") and rate < threshold:
            continue
        if ntype == "service":
            volume = int(n.get("total_spans", 0) or 0)
        else:
            volume = int(n.get("count", 0) or 0)
        rows.append((rate * float(volume), n))
    rows.sort(key=lambda kv: kv[0], reverse=True)
    return {
        "min_error_rate": threshold,
        "limit": int(limit),
        "hotspots": [n for _, n in rows[: int(limit)]],
        "total_matches": len(rows),
    }


@mcp.tool()
def service_call_graph(
    service: str,
    depth: int = 2,
    persist_dir: str | None = None,
) -> dict:
    """BFS subgraph rooted at `service:<service>` along outgoing `calls`
    edges (service-to-service), up to `depth` hops.
    """
    if not service:
        return {
            "root": None,
            "depth": int(depth),
            "nodes": [],
            "edges": [],
            "error": "service is required",
        }
    root_id = service if service.startswith("service:") else f"service:{service}"
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes_by_id = {n.get("id"): n for n in store.all_nodes(layer="traces")}
    if root_id not in nodes_by_id:
        return {
            "root": root_id,
            "depth": int(depth),
            "nodes": [],
            "edges": [],
            "found": False,
        }
    adjacency: dict[str, list[dict]] = defaultdict(list)
    for e in store.all_edges():
        if e.get("relation") != "calls":
            continue
        src = e.get("source") or ""
        tgt = e.get("target") or ""
        if not src.startswith("service:") or not tgt.startswith("service:"):
            continue
        adjacency[src].append(e)

    visited: set[str] = {root_id}
    out_edges: list[dict] = []
    frontier: list[str] = [root_id]
    for _ in range(max(0, int(depth))):
        next_frontier: list[str] = []
        for sid in frontier:
            for e in adjacency.get(sid, []):
                tgt = e.get("target") or ""
                out_edges.append(e)
                if tgt not in visited:
                    visited.add(tgt)
                    next_frontier.append(tgt)
        frontier = next_frontier
        if not frontier:
            break
    return {
        "root": root_id,
        "depth": int(depth),
        "found": True,
        "nodes": [nodes_by_id[i] for i in visited if i in nodes_by_id],
        "edges": out_edges,
    }
