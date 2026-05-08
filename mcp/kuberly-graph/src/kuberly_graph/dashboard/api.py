"""JSON API handlers for the live web dashboard.

Each handler is a thin wrapper around an existing MCP tool function (or a
direct GraphStore query). We call the underlying Python functions in-
process — there is no MCP roundtrip — so this is cheap and free of any
client/transport coupling.

v0.47.0:
- ``_persist_dir`` reads from ``SERVER_CONFIG`` only — never re-resolves
  against the request-time CWD. ``cli._cmd_serve`` writes the absolute path
  via ``configure(...)`` so downstream calls are deterministic regardless
  of where the user launched the server from.
- New ``graph_endpoint`` returns the full nodes+edges payload for the 3D
  force-directed visualization, with a derived ``category`` field per node.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..server import SERVER_CONFIG
from ..store import open_store


def _persist_dir() -> Path:
    """Return the absolute persist_dir resolved by ``cli._cmd_serve``.

    NB: do NOT call ``.resolve()`` here — that would re-anchor a relative
    string against the current process CWD, which is exactly the bug we
    fixed in v0.47.0. ``cli._resolve_persist_dir`` always writes an
    absolute string into ``SERVER_CONFIG["persist_dir"]``.
    """
    raw = SERVER_CONFIG.get("persist_dir", ".kuberly")
    return Path(raw)


def _int_param(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _str_param(request: Request, name: str) -> str | None:
    val = request.query_params.get(name)
    if val is None or val == "":
        return None
    return val


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _ok(payload: Any) -> JSONResponse:
    return JSONResponse(payload)


# ---------------------------------------------------------------------------
# /api/v1/layers
# ---------------------------------------------------------------------------


async def layers_endpoint(_request: Request) -> JSONResponse:
    """Return the list_layers summary (one row per layer)."""
    from ..orchestrator import list_layers_summary

    try:
        return _ok(list_layers_summary(str(_persist_dir())))
    except Exception as exc:  # pragma: no cover — defensive
        return _err(f"layers query failed: {exc}", 500)


# ---------------------------------------------------------------------------
# /api/v1/stats
# ---------------------------------------------------------------------------


async def stats_endpoint(_request: Request) -> JSONResponse:
    """Return graph_stats (per-layer node/edge counts + last_refresh)."""
    try:
        store = open_store(_persist_dir())
        return _ok(store.stats())
    except Exception as exc:  # pragma: no cover — defensive
        return _err(f"stats query failed: {exc}", 500)


# ---------------------------------------------------------------------------
# Category mapping for the 3D viz colour scheme
# ---------------------------------------------------------------------------
#
# Each node gets a *category* string driving its colour in the front-end.
# Categories (and the colours they map to in app.js):
#   iac_files         #1677ff (blue)   — code, modules, terragrunt files
#   tg_state          #ff9900 (orange) — terraform state / cloud resource
#   k8s_resources     #ff5552 (red)    — k8s_resource, crd, namespace, pod
#   docs              #9da3ad (gray)
#   cue               #a259ff (purple) — schema/cue
#   ci_cd             #3ddc84 (green)  — github actions, workflows, image builds
#   applications      #ff4f9c (pink)   — argocd app / helm release / rendered apps
#   live_observability#f5b800 (yellow) — logs/metrics/traces/alerts/profiles
#   aws               #ff9900          — Phase 8F native AWS scanner output
#   dependency        #c0c4cc          — dep / module-dep nodes
#   meta              #ffffff          — meta-graph self-describing nodes


_LAYER_TO_CATEGORY = {
    "code": "iac_files",
    "static": "iac_files",
    "iac": "iac_files",
    "terragrunt": "iac_files",
    "state": "tg_state",
    "tg_state": "tg_state",
    "tofu_state": "tg_state",
    "k8s": "k8s_resources",
    "kubernetes": "k8s_resources",
    "docs": "docs",
    "doc": "docs",
    "cue": "cue",
    "schema": "cue",
    "cue_schema": "cue",
    "ci_cd": "ci_cd",
    "image_build": "ci_cd",
    "github_actions": "ci_cd",
    "applications": "applications",
    "rendered": "applications",
    "rendered_apps": "applications",
    "logs": "live_observability",
    "metrics": "live_observability",
    "traces": "live_observability",
    "alerts": "live_observability",
    "live": "live_observability",
    "live_observability": "live_observability",
    "profiles": "live_observability",
    "compliance": "live_observability",
    "cost": "live_observability",
    "dns": "live_observability",
    "secrets": "live_observability",
    "aws": "aws",
    "aws_network": "aws",
    "aws_iam": "aws",
    "aws_compute": "aws",
    "aws_storage": "aws",
    "aws_rds": "aws",
    "aws_s3": "aws",
    "dependency": "dependency",
    "deps": "dependency",
    "meta": "meta",
}

_TYPE_TO_CATEGORY = {
    # k8s
    "k8s_resource": "k8s_resources",
    "crd": "k8s_resources",
    "namespace": "k8s_resources",
    "pod": "k8s_resources",
    "node": "k8s_resources",
    "service": "k8s_resources",
    "deployment": "k8s_resources",
    # iac
    "module": "iac_files",
    "component": "iac_files",
    "terragrunt_root": "iac_files",
    "tg_file": "iac_files",
    # state
    "resource": "tg_state",
    "tg_state": "tg_state",
    # apps
    "application": "applications",
    "argo_app": "applications",
    "helm_release": "applications",
    # docs
    "doc": "docs",
    "docs": "docs",
    # observability
    "log_stream": "live_observability",
    "metric": "live_observability",
    "trace": "live_observability",
    "alert": "live_observability",
    # ci/cd
    "image": "ci_cd",
    "image_build": "ci_cd",
    "workflow": "ci_cd",
    # cue
    "cue_def": "cue",
    "schema": "cue",
    # meta
    "meta_layer": "meta",
}


def _categorize(node: dict) -> str:
    """Pick a colour category for a node. Layer dominates; type fills gaps."""
    layer = (node.get("layer") or "").strip()
    if layer in _LAYER_TO_CATEGORY:
        return _LAYER_TO_CATEGORY[layer]
    ntype = (node.get("type") or "").strip()
    if ntype in _TYPE_TO_CATEGORY:
        return _TYPE_TO_CATEGORY[ntype]
    # heuristic fallbacks
    if layer.startswith("aws"):
        return "aws"
    if layer.startswith("k8s"):
        return "k8s_resources"
    if "doc" in layer:
        return "docs"
    return "dependency"


# ---------------------------------------------------------------------------
# /api/v1/graph — full nodes+edges payload for the 3D viz
# ---------------------------------------------------------------------------


async def graph_endpoint(request: Request) -> JSONResponse:
    """Return ``{nodes:[...], edges:[...]}`` for the 3D force-graph viz.

    Query params:
      layer  optional layer filter (matches node.layer)
      type   optional type filter (matches node.type)
      limit  cap on returned nodes (default 5000)
    """
    layer = _str_param(request, "layer")
    type_ = _str_param(request, "type")
    limit = max(1, _int_param(request, "limit", 5000))

    try:
        store = open_store(_persist_dir())
        all_n = store.all_nodes(layer=layer)
        all_e = store.all_edges(layer=layer)
    except Exception as exc:
        return _err(f"graph query failed: {exc}", 500)

    nodes_out: list[dict] = []
    seen: set[str] = set()
    for n in all_n:
        if not isinstance(n, dict):
            continue
        if type_ and n.get("type") != type_:
            continue
        nid = n.get("id")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        nodes_out.append(
            {
                "id": nid,
                "type": n.get("type", ""),
                "layer": n.get("layer", ""),
                "label": n.get("label") or nid,
                "category": _categorize(n),
            }
        )
        if len(nodes_out) >= limit:
            break

    node_ids = {n["id"] for n in nodes_out}
    edges_out: list[dict] = []
    for e in all_e:
        if not isinstance(e, dict):
            continue
        s = e.get("source")
        t = e.get("target")
        if s in node_ids and t in node_ids:
            edges_out.append(
                {"source": s, "target": t, "relation": e.get("relation", "")}
            )

    return _ok(
        {
            "layer": layer,
            "type": type_,
            "limit": limit,
            "node_count": len(nodes_out),
            "edge_count": len(edges_out),
            "nodes": nodes_out,
            "edges": edges_out,
        }
    )


# ---------------------------------------------------------------------------
# /api/v1/nodes
# ---------------------------------------------------------------------------


async def nodes_endpoint(request: Request) -> JSONResponse:
    """List nodes filtered by layer / type / name substring (LanceDB-only).

    Query params: layer, type, name, limit (default 50).
    """
    layer = _str_param(request, "layer")
    type_ = _str_param(request, "type")
    name = _str_param(request, "name")
    limit = max(1, _int_param(request, "limit", 50))

    try:
        store = open_store(_persist_dir())
        rows = store.all_nodes(layer=layer)
    except Exception as exc:
        return _err(f"node query failed: {exc}", 500)

    out: list[dict] = []
    for n in rows:
        if not isinstance(n, dict):
            continue
        if type_ and n.get("type") != type_:
            continue
        if name:
            blob = f"{n.get('id', '')} {n.get('label', '')}".lower()
            if name.lower() not in blob:
                continue
        out.append(n)
        if len(out) >= limit:
            break
    return _ok({"layer": layer, "type": type_, "name": name, "limit": limit, "nodes": out})


async def node_detail_endpoint(request: Request) -> JSONResponse:
    """Return the full node payload for the given id (URL-decoded)."""
    raw = request.path_params.get("node_id", "")
    nid = unquote(raw) if isinstance(raw, str) else ""
    if not nid:
        return _err("node id required", 400)

    try:
        store = open_store(_persist_dir())
        for n in store.all_nodes():
            if isinstance(n, dict) and n.get("id") == nid:
                return _ok({"node": n})
    except Exception as exc:
        return _err(f"node lookup failed: {exc}", 500)
    return _err(f"node not found: {nid}", 404)


async def node_neighbors_endpoint(request: Request) -> JSONResponse:
    """Return one-hop incoming/outgoing edges for the given node id."""
    raw = request.path_params.get("node_id", "")
    nid = unquote(raw) if isinstance(raw, str) else ""
    if not nid:
        return _err("node id required", 400)

    direction = (_str_param(request, "direction") or "both").lower()
    if direction not in ("in", "out", "both"):
        direction = "both"

    try:
        store = open_store(_persist_dir())
        nodes_by_id = {n.get("id"): n for n in store.all_nodes() if isinstance(n, dict)}
        edges = store.all_edges()
    except Exception as exc:
        return _err(f"neighbor query failed: {exc}", 500)

    if nid not in nodes_by_id:
        return _err(f"node not found: {nid}", 404)

    incoming: list[dict] = []
    outgoing: list[dict] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        if direction in ("in", "both") and e.get("target") == nid:
            src = e.get("source") or ""
            n = nodes_by_id.get(src, {"id": src})
            incoming.append({
                "source": src,
                "relation": e.get("relation", ""),
                "label": n.get("label"),
                "type": n.get("type"),
                "layer": n.get("layer"),
            })
        if direction in ("out", "both") and e.get("source") == nid:
            tgt = e.get("target") or ""
            n = nodes_by_id.get(tgt, {"id": tgt})
            outgoing.append({
                "target": tgt,
                "relation": e.get("relation", ""),
                "label": n.get("label"),
                "type": n.get("type"),
                "layer": n.get("layer"),
            })
    return _ok({
        "node": nid,
        "node_info": nodes_by_id.get(nid),
        "incoming": incoming,
        "outgoing": outgoing,
    })


async def node_blast_endpoint(request: Request) -> JSONResponse:
    """Wrap the cold-graph blast_radius tool. Re-builds the cold KuberlyGraph."""
    raw = request.path_params.get("node_id", "")
    nid = unquote(raw) if isinstance(raw, str) else ""
    if not nid:
        return _err("node id required", 400)

    direction = (_str_param(request, "direction") or "both").lower()
    if direction not in ("upstream", "downstream", "both"):
        direction = "both"
    max_depth = max(1, _int_param(request, "max_depth", 10))

    from ..tools.query import blast_radius

    try:
        return _ok(blast_radius(node=nid, direction=direction, max_depth=max_depth))
    except Exception as exc:  # pragma: no cover
        return _err(f"blast_radius failed: {exc}", 500)


# ---------------------------------------------------------------------------
# /api/v1/search
# ---------------------------------------------------------------------------


async def search_endpoint(request: Request) -> JSONResponse:
    """Wrap semantic_search; falls back to substring scan when LanceDB
    isn't available (handled inside the store)."""
    q = _str_param(request, "q") or ""
    if not q:
        return _err("q is required", 400)
    layer = _str_param(request, "layer")
    limit = max(1, _int_param(request, "limit", 10))

    try:
        store = open_store(_persist_dir())
        hits = store.semantic_search(query=q, layer=layer, limit=limit)
    except Exception as exc:
        return _err(f"search failed: {exc}", 500)

    if hits and isinstance(hits[0], dict) and "error" in hits[0]:
        # Fall back to substring scan on label/id so the dashboard always
        # has something to render.
        try:
            store = open_store(_persist_dir())
            substring: list[dict] = []
            ql = q.lower()
            for n in store.all_nodes(layer=layer):
                if not isinstance(n, dict):
                    continue
                blob = f"{n.get('id', '')} {n.get('label', '')}".lower()
                if ql in blob:
                    substring.append(n)
                if len(substring) >= limit:
                    break
            return _ok({
                "query": q,
                "layer": layer,
                "limit": limit,
                "hits": substring,
                "fallback": "substring",
            })
        except Exception as exc:  # pragma: no cover
            return _err(f"search fallback failed: {exc}", 500)
    return _ok({"query": q, "layer": layer, "limit": limit, "hits": hits})


async def cross_search_endpoint(request: Request) -> JSONResponse:
    """Wrap fusion.cross_layer_search."""
    q = _str_param(request, "q") or ""
    if not q:
        return _err("q is required", 400)
    limit = max(1, _int_param(request, "limit", 20))

    from ..tools.fusion import cross_layer_search

    try:
        hits = cross_layer_search(query=q, limit=limit)
        return _ok({"query": q, "limit": limit, "hits": hits})
    except Exception as exc:  # pragma: no cover
        return _err(f"cross_layer_search failed: {exc}", 500)


# ---------------------------------------------------------------------------
# /api/v1/anomalies
# ---------------------------------------------------------------------------


async def anomalies_endpoint(request: Request) -> JSONResponse:
    """Wrap fusion.find_anomalies."""
    layer = _str_param(request, "layer")
    limit = max(1, _int_param(request, "limit", 20))

    from ..tools.fusion import find_anomalies

    try:
        rows = find_anomalies(layer=layer, limit=limit)
        return _ok({"layer": layer, "limit": limit, "anomalies": rows})
    except Exception as exc:  # pragma: no cover
        return _err(f"find_anomalies failed: {exc}", 500)


# ---------------------------------------------------------------------------
# /api/v1/service/<name>
# ---------------------------------------------------------------------------


async def service_one_pager_endpoint(request: Request) -> JSONResponse:
    raw = request.path_params.get("name", "")
    service = unquote(raw) if isinstance(raw, str) else ""
    if not service:
        return _err("service name required", 400)
    env = _str_param(request, "env")

    from ..tools.fusion import service_one_pager

    try:
        return _ok(service_one_pager(service=service, env=env))
    except Exception as exc:  # pragma: no cover
        return _err(f"service_one_pager failed: {exc}", 500)


async def service_mermaid_endpoint(request: Request) -> JSONResponse:
    raw = request.path_params.get("name", "")
    service = unquote(raw) if isinstance(raw, str) else ""
    if not service:
        return _err("service name required", 400)
    env = _str_param(request, "env")
    layers_csv = _str_param(request, "layers")
    layers_arg: list[str] | None = None
    if layers_csv:
        layers_arg = [s.strip() for s in layers_csv.split(",") if s.strip()]

    from ..tools.fusion import service_mermaid

    try:
        return _ok(service_mermaid(service=service, layers=layers_arg, env=env))
    except Exception as exc:  # pragma: no cover
        return _err(f"service_mermaid failed: {exc}", 500)
