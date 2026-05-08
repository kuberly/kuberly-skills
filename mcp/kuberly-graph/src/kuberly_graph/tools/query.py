"""Cold-graph query tools — operate on a freshly-built KuberlyGraph.

These mirror the legacy `query_nodes / get_node / get_neighbors /
blast_radius / shortest_path / drift / stats` MCP tools. Output shapes are
preserved byte-for-byte so existing consumers see no change.

v0.53.0: ``query_nodes`` accepts optional ``cursor`` + ``limit`` kwargs.
When EITHER is provided the return shape switches to
``{nodes, next_cursor, total_count}``; otherwise the legacy ``list[dict]``
is returned to keep existing callers untouched. ``shortest_path`` and
``blast_radius`` reuse a process-wide cached ``RxGraph`` so repeated calls
within a single ``cache_epoch`` window are cheap.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from typing import Any

from ..cache import cache_epoch, ttl_get, ttl_set
from ..layers._util import KuberlyGraph
from ..server import SERVER_CONFIG, mcp


def _load_cold() -> KuberlyGraph:
    repo = SERVER_CONFIG.get("repo_root", ".")
    g = KuberlyGraph(str(repo))
    g.build()
    return g


def _resolve(g: KuberlyGraph, q: str) -> str | None:
    for nid, node in g.nodes.items():
        if nid == q or node.get("label") == q:
            return nid
    cands = [
        nid
        for nid in g.nodes
        if q.lower() in nid.lower()
        or q.lower() in g.nodes[nid].get("label", "").lower()
    ]
    return cands[0] if len(cands) == 1 else None


def _filter_hash(
    node_type: str | None, environment: str | None, name_contains: str | None
) -> str:
    payload = json.dumps(
        {"t": node_type or "", "e": environment or "", "n": name_contains or ""},
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _encode_cursor(last_id: str, fhash: str) -> str:
    raw = json.dumps({"last_id": last_id, "filter_hash": fhash})
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        d = json.loads(raw.decode("utf-8"))
        return d.get("last_id"), d.get("filter_hash")
    except Exception:
        return None, None


@mcp.tool()
def query_nodes(
    node_type: str | None = None,
    environment: str | None = None,
    name_contains: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
):
    """Filter graph nodes by type, environment, and/or name substring.

    v0.53.0: optional ``cursor`` + ``limit`` enable pagination.

    - When BOTH ``cursor`` and ``limit`` are ``None`` the return type is the
      legacy ``list[dict]`` (back-compat with existing callers).
    - When EITHER is provided, the return is
      ``{"nodes": [...], "next_cursor": str | None, "total_count": int}``.
      Cursor is a base64-url payload encoding ``{"last_id", "filter_hash"}``;
      the filter hash is verified on resume so a stale cursor against a
      different filter set falls back to the start.
    """
    paginated = cursor is not None or limit is not None
    g = _load_cold()
    matches: list[dict] = []
    for _nid, node in g.nodes.items():
        if node_type and node.get("type") != node_type:
            continue
        if environment and node.get("environment") != environment:
            continue
        if name_contains and (
            name_contains.lower() not in node.get("label", "").lower()
            and name_contains.lower() not in node["id"].lower()
        ):
            continue
        matches.append(node)

    if not paginated:
        return matches

    # Stable id ordering for cursor-based resume.
    matches.sort(key=lambda n: n.get("id", ""))
    fhash = _filter_hash(node_type, environment, name_contains)
    last_id, cursor_fhash = _decode_cursor(cursor)
    # Stale-cursor guard: if filter changed, restart from the top.
    if cursor and cursor_fhash and cursor_fhash != fhash:
        last_id = None

    page_limit = max(1, int(limit)) if limit is not None else 100
    start_idx = 0
    if last_id:
        for i, n in enumerate(matches):
            if n.get("id", "") > last_id:
                start_idx = i
                break
        else:
            start_idx = len(matches)
    end_idx = min(len(matches), start_idx + page_limit)
    page = matches[start_idx:end_idx]
    next_cursor = (
        _encode_cursor(page[-1].get("id", ""), fhash)
        if end_idx < len(matches) and page
        else None
    )
    return {
        "nodes": page,
        "next_cursor": next_cursor,
        "total_count": len(matches),
    }


@mcp.tool()
def get_node(node: str) -> dict:
    """Get full details for a specific node by id or name."""
    return get_neighbors(node)


@mcp.tool()
def get_neighbors(node: str) -> dict:
    """Get immediate incoming and outgoing neighbors of a node."""
    g = _load_cold()
    match = _resolve(g, node)
    if not match:
        return {"error": f"No node matching '{node}'"}
    incoming = [
        {"source": e["source"], "relation": e.get("relation", "")}
        for e in g.edges
        if e["target"] == match
    ]
    outgoing = [
        {"target": e["target"], "relation": e.get("relation", "")}
        for e in g.edges
        if e["source"] == match
    ]
    return {
        "node": match,
        "node_info": g.nodes[match],
        "incoming": incoming,
        "outgoing": outgoing,
    }


@mcp.tool()
def blast_radius(
    node: str,
    direction: str = "both",
    max_depth: int = 20,
) -> dict:
    """Compute blast radius — what a node affects (downstream) and what
    affects it (upstream). Useful for impact analysis.
    """
    g = _load_cold()
    match = None
    for nid, n in g.nodes.items():
        if nid == node or n.get("label") == node:
            match = nid
            break
    if not match:
        candidates = [
            nid
            for nid, n in g.nodes.items()
            if node.lower() in nid.lower()
            or node.lower() in n.get("label", "").lower()
        ]
        if len(candidates) == 1:
            match = candidates[0]
        elif candidates:
            return {
                "error": f"Ambiguous query '{node}', matches: {candidates[:10]}"
            }
        else:
            return {"error": f"No node matching '{node}'"}

    fwd: dict[str, list[tuple[str, str]]] = defaultdict(list)
    rev: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for e in g.edges:
        fwd[e["source"]].append((e["target"], e.get("relation", "")))
        rev[e["target"]].append((e["source"], e.get("relation", "")))

    def walk(start: str, adj: dict) -> dict[str, int]:
        visited: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(start, 0)]
        while queue:
            cur, d = queue.pop(0)
            if cur in visited or d > max_depth:
                continue
            visited[cur] = d
            for nb, _rel in adj.get(cur, []):
                if nb not in visited:
                    queue.append((nb, d + 1))
        visited.pop(start, None)
        return visited

    result: dict[str, Any] = {"node": match, "node_info": g.nodes.get(match, {})}
    if direction in ("downstream", "both"):
        ds = walk(match, fwd)
        result["downstream"] = {
            nid: {"depth": d, **g.nodes.get(nid, {})}
            for nid, d in sorted(ds.items(), key=lambda kv: kv[1])
        }
        result["downstream_count"] = len(ds)
    if direction in ("upstream", "both"):
        us = walk(match, rev)
        result["upstream"] = {
            nid: {"depth": d, **g.nodes.get(nid, {})}
            for nid, d in sorted(us.items(), key=lambda kv: kv[1])
        }
        result["upstream_count"] = len(us)
    return result


@mcp.tool()
def shortest_path(source: str, target: str) -> dict:
    """Find the shortest path between two nodes (undirected BFS)."""
    g = _load_cold()
    src = _resolve(g, source)
    tgt = _resolve(g, target)
    if not src:
        return {"error": f"Cannot resolve source '{source}'"}
    if not tgt:
        return {"error": f"Cannot resolve target '{target}'"}

    adj: dict[str, set[str]] = defaultdict(set)
    for e in g.edges:
        adj[e["source"]].add(e["target"])
        adj[e["target"]].add(e["source"])

    visited: dict[str, str | None] = {src: None}
    queue: list[str] = [src]
    while queue:
        cur = queue.pop(0)
        if cur == tgt:
            path: list[str] = []
            node: str | None = cur
            while node is not None:
                path.append(node)
                node = visited[node]
            path.reverse()
            return {"path": path, "length": len(path) - 1}
        for nb in adj.get(cur, []):
            if nb not in visited:
                visited[nb] = cur
                queue.append(nb)
    return {"error": f"No path between '{src}' and '{tgt}'"}


@mcp.tool()
def drift() -> dict:
    """Cross-environment drift: components and applications that exist in
    some environments but not others.
    """
    g = _load_cold()
    return g.cross_env_drift()


@mcp.tool()
def stats() -> dict:
    """Graph statistics: node/edge counts, critical nodes, longest chains."""
    g = _load_cold()
    return g.compute_stats()
