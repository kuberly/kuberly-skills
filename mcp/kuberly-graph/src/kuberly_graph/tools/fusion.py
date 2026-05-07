"""Cross-layer fusion + super tools (Phase 5).

Six MCP tools that join across the 11 layers (cold IaC + k8s/argo +
logs/metrics/traces + rendered/state):

- ``service_one_pager``       — unified service profile
- ``find_anomalies``          — sweep ``is_anomaly`` across all layers
- ``cross_layer_search``      — semantic hits enriched with cross-layer neighbours
- ``service_mermaid``         — Mermaid neighbourhood diagram
- ``health_score``            — composite 0..100 score
- ``cross_layer_fuse``        — full cross-layer drift report (capstone)

Tools 1a-1e are pure-read against the GraphStore. ``cross_layer_fuse``
optionally refreshes layers via ``regenerate_layer`` when an MCP endpoint
is provided.

Empty-layer tolerance: every join handles the case where only the cold
layer is populated — missing data → ``null`` / ``[]`` / ``0``, never crash.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..graph.rustworkx_graph import RxGraph
from ..server import SERVER_CONFIG, mcp
from ..store import open_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _index_nodes(nodes: Iterable[dict]) -> dict[str, dict]:
    return {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}


def _build_edge_indexes(
    edges: Iterable[dict],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Return (out_by_source, in_by_target) for fast adjacency lookups."""
    out_by_source: dict[str, list[dict]] = defaultdict(list)
    in_by_target: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        if not isinstance(e, dict):
            continue
        s = e.get("source")
        t = e.get("target")
        if not s or not t:
            continue
        out_by_source[s].append(e)
        in_by_target[t].append(e)
    return out_by_source, in_by_target


def _truthy_anomaly(node: dict) -> bool:
    val = node.get("is_anomaly")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in {"true", "1", "yes"}
    return bool(val)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _resolve_app_id(
    nodes_by_id: dict[str, dict],
    service: str,
    env: str | None,
) -> str | None:
    """Find the cold IaC application node id for ``service`` (& optional env)."""
    if service.startswith("app:"):
        return service if service in nodes_by_id else None
    candidates: list[str] = []
    for nid, node in nodes_by_id.items():
        if node.get("type") != "application":
            continue
        if env and node.get("environment") != env:
            continue
        if (
            node.get("label") == service
            or nid == f"app:{env}/{service}"
            or nid.endswith(f"/{service}")
        ):
            candidates.append(nid)
    if not candidates:
        return None
    if env:
        for c in candidates:
            if c == f"app:{env}/{service}":
                return c
    return candidates[0]


def _service_node_id(service: str) -> str:
    return service if service.startswith("service:") else f"service:{service}"


# ---------------------------------------------------------------------------
# Score computation (shared between service_one_pager and health_score)
# ---------------------------------------------------------------------------


def _compute_score(
    *,
    service_node: dict | None,
    log_template_count: int,
    log_error_template_count: int,
    metric_count: int,
    high_card_count: int,
    rendered_count: int,
    matched_count: int,
) -> dict:
    factors: dict[str, float] = {}
    if service_node:
        rate = _safe_float(service_node.get("error_rate"))
        factors["reliability"] = max(0.0, min(1.0, 1.0 - rate))
    if log_template_count > 0:
        factors["log_health"] = max(
            0.0, 1.0 - (log_error_template_count / log_template_count)
        )
    if metric_count > 0:
        factors["metric_hygiene"] = max(
            0.0, 1.0 - (high_card_count / metric_count)
        )
    if rendered_count > 0:
        factors["live_match"] = max(
            0.0, min(1.0, matched_count / rendered_count)
        )
    if not factors:
        return {"value": None, "factors": {}}
    overall = sum(factors.values()) / len(factors)
    return {
        "value": round(overall * 100.0, 2),
        "factors": {k: round(v, 4) for k, v in factors.items()},
    }


# ---------------------------------------------------------------------------
# 1a. service_one_pager
# ---------------------------------------------------------------------------


def _gather_service_profile(
    nodes_by_id: dict[str, dict],
    edges_out: dict[str, list[dict]],
    edges_in: dict[str, list[dict]],
    service: str,
    env: str | None,
) -> dict:
    """Build the unified one-pager dict (also used by ``health_score``)."""
    app_id = _resolve_app_id(nodes_by_id, service, env)
    app_node = nodes_by_id.get(app_id) if app_id else None
    resolved_env = (
        (app_node.get("environment") if app_node else None) or env or None
    )

    # ---- IaC slice -----------------------------------------------------------
    iac: dict[str, str | None] = {
        "module": None,
        "component": None,
        "application": app_id,
    }
    if app_id and resolved_env:
        # Component candidate: "component:<env>/<service>" if exists.
        comp_id = f"component:{resolved_env}/{service}"
        if comp_id in nodes_by_id:
            iac["component"] = comp_id
        # Walk component → module via "configures_module".
        if iac["component"]:
            for e in edges_out.get(iac["component"], []):
                if e.get("relation") == "configures_module" and e.get(
                    "target"
                ) in nodes_by_id:
                    iac["module"] = e["target"]
                    break

    # ---- k8s slice -----------------------------------------------------------
    k8s_resources: list[dict] = []
    if app_id:
        # app → renders → app_render → renders → rendered_resource
        #              → live_match → k8s_resource
        for e1 in edges_out.get(app_id, []):
            if e1.get("relation") != "renders":
                continue
            render_id = e1.get("target") or ""
            for e2 in edges_out.get(render_id, []):
                if e2.get("relation") != "renders":
                    continue
                rrid = e2.get("target") or ""
                for e3 in edges_out.get(rrid, []):
                    if e3.get("relation") != "live_match":
                        continue
                    k8s_id = e3.get("target") or ""
                    k8s_node = nodes_by_id.get(k8s_id)
                    if not k8s_node:
                        continue
                    k8s_resources.append(
                        {
                            "kind": k8s_node.get("kind"),
                            "namespace": k8s_node.get("namespace"),
                            "name": k8s_node.get("name"),
                            "id": k8s_id,
                        }
                    )

    # ---- argo slice ----------------------------------------------------------
    argo: dict | None = None
    if app_id:
        for e in edges_in.get(app_id, []):
            if e.get("relation") != "tracks":
                continue
            argo_id = e.get("source") or ""
            argo_node = nodes_by_id.get(argo_id)
            if not argo_node:
                continue
            argo = {
                "id": argo_id,
                "sync_status": argo_node.get("sync_status"),
                "health_status": argo_node.get("health_status"),
            }
            break

    # ---- logs slice ----------------------------------------------------------
    log_templates: list[dict] = []
    if app_id:
        for e in edges_out.get(app_id, []):
            if e.get("relation") != "emits":
                continue
            tpl_id = e.get("target") or ""
            tpl = nodes_by_id.get(tpl_id)
            if tpl:
                log_templates.append(tpl)

    log_error_count = sum(1 for n in log_templates if bool(n.get("is_error")))
    top5 = sorted(
        log_templates,
        key=lambda n: _safe_int(n.get("count")),
        reverse=True,
    )[:5]
    logs_block = {
        "template_count": len(log_templates),
        "error_template_count": log_error_count,
        "top_5_templates": [
            {
                "template": n.get("template", "")[:120],
                "count": _safe_int(n.get("count")),
                "is_error": bool(n.get("is_error")),
            }
            for n in top5
        ],
    }

    # ---- metrics slice -------------------------------------------------------
    metric_nodes: list[dict] = []
    if app_id:
        for e in edges_out.get(app_id, []):
            if e.get("relation") != "instrumented_by":
                continue
            mid = e.get("target") or ""
            m = nodes_by_id.get(mid)
            if m:
                metric_nodes.append(m)

    high_card = sum(
        1 for n in metric_nodes if bool(n.get("is_high_cardinality"))
    )
    scrape_targets: set[str] = set()
    for m in metric_nodes:
        for e in edges_in.get(m.get("id", ""), []):
            if e.get("relation") != "produces":
                continue
            stid = e.get("source") or ""
            stn = nodes_by_id.get(stid)
            if not stn:
                continue
            scrape_targets.add(
                f"{stn.get('job', '')}/{stn.get('instance', '')}"
            )
    metrics_block = {
        "metric_count": len(metric_nodes),
        "high_cardinality_count": high_card,
        "scrape_targets": sorted(scrape_targets),
    }

    # ---- traces slice --------------------------------------------------------
    traces_block: dict | None = None
    service_node: dict | None = None
    sid = _service_node_id(service)
    if sid in nodes_by_id and nodes_by_id[sid].get("type") == "service":
        service_node = nodes_by_id[sid]
    elif app_id:
        for e in edges_out.get(app_id, []):
            if e.get("relation") == "traces_as":
                tgt = e.get("target") or ""
                if tgt in nodes_by_id and nodes_by_id[tgt].get("type") == "service":
                    service_node = nodes_by_id[tgt]
                    sid = tgt
                    break

    if service_node:
        callers: list[dict] = []
        callees: list[dict] = []
        for e in edges_in.get(sid, []):
            if e.get("relation") != "calls":
                continue
            callers.append(
                {
                    "from": e.get("source"),
                    "call_count": _safe_int(e.get("call_count")),
                    "error_rate": _safe_float(e.get("error_rate")),
                }
            )
        for e in edges_out.get(sid, []):
            if e.get("relation") != "calls":
                continue
            callees.append(
                {
                    "to": e.get("target"),
                    "call_count": _safe_int(e.get("call_count")),
                    "error_rate": _safe_float(e.get("error_rate")),
                }
            )
        callers.sort(key=lambda r: r["call_count"], reverse=True)
        callees.sort(key=lambda r: r["call_count"], reverse=True)
        traces_block = {
            "service_id": sid,
            "p95_ms": _safe_float(service_node.get("p95_ms")),
            "p99_ms": _safe_float(service_node.get("p99_ms")),
            "error_rate": _safe_float(service_node.get("error_rate")),
            "total_spans": _safe_int(service_node.get("total_spans")),
            "top_callers": callers[:5],
            "top_callees": callees[:5],
        }

    # ---- score ---------------------------------------------------------------
    rendered_count = 0
    matched_count = 0
    if app_id:
        for e1 in edges_out.get(app_id, []):
            if e1.get("relation") != "renders":
                continue
            render_id = e1.get("target") or ""
            for e2 in edges_out.get(render_id, []):
                if e2.get("relation") != "renders":
                    continue
                rrid = e2.get("target") or ""
                rendered_count += 1
                for e3 in edges_out.get(rrid, []):
                    if e3.get("relation") == "live_match":
                        matched_count += 1
                        break

    score = _compute_score(
        service_node=service_node,
        log_template_count=len(log_templates),
        log_error_template_count=log_error_count,
        metric_count=len(metric_nodes),
        high_card_count=high_card,
        rendered_count=rendered_count,
        matched_count=matched_count,
    )

    return {
        "service": service,
        "env": resolved_env,
        "iac": iac,
        "k8s": k8s_resources,
        "argo": argo,
        "logs": logs_block,
        "metrics": metrics_block,
        "traces": traces_block,
        "score": score,
    }


@mcp.tool()
def service_one_pager(
    service: str,
    env: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Unified per-service profile across all 11 graph layers.

    Joins cold IaC (application/component/module) with rendered manifests,
    live k8s, argo state, logs templates, scraped metrics, and trace
    aggregates. Computes a composite ``score`` using the factors that
    happen to be present (no penalty for missing layers).
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = _index_nodes(nodes)
    out_by_src, in_by_tgt = _build_edge_indexes(edges)
    return _gather_service_profile(
        nodes_by_id, out_by_src, in_by_tgt, service, env
    )


# ---------------------------------------------------------------------------
# 1b. find_anomalies
# ---------------------------------------------------------------------------


def _anomaly_score(node: dict) -> tuple[float, str]:
    """Return ``(score, why)`` for an anomaly node.

    Heuristic per layer:
      traces:  error_rate * total_spans / 100
      logs:    count if is_error else count * 0.1
      metrics: log10(series_count) * 10
      k8s:     100 if missing, 50 if drifted, 10 otherwise
      others:  1
    """
    layer = node.get("layer") or ""
    ntype = node.get("type") or ""
    if layer == "traces" or ntype in {"service", "operation"}:
        rate = _safe_float(node.get("error_rate"))
        volume = _safe_int(node.get("total_spans") or node.get("count"))
        return rate * volume / 100.0, (
            f"error_rate={rate:.3f} volume={volume}"
        )
    if layer == "logs" or ntype == "log_template":
        count = _safe_int(node.get("count"))
        is_err = bool(node.get("is_error"))
        return (count if is_err else count * 0.1), (
            f"count={count} is_error={is_err}"
        )
    if layer == "metrics" or ntype == "metric":
        sc = max(1, _safe_int(node.get("series_count")))
        return math.log10(float(sc)) * 10.0, f"series_count={sc}"
    if layer == "k8s" or ntype == "k8s_resource":
        state = (node.get("drift_state") or "").lower()
        if state == "missing":
            return 100.0, "missing live resource"
        if state == "drifted":
            return 50.0, "drifted from rendered"
        return 10.0, "k8s anomaly"
    return 1.0, "anomaly"


@mcp.tool()
def find_anomalies(
    layer: str | None = None,
    limit: int = 50,
    persist_dir: str | None = None,
) -> list[dict]:
    """Sweep ``is_anomaly == true`` across layers and rank by impact.

    Per-layer heuristics:
      - traces:  ``error_rate * total_spans / 100``
      - logs:    ``count`` if ``is_error`` else ``count * 0.1``
      - metrics: ``log10(series_count) * 10``
      - k8s:     ``100`` if missing, ``50`` if drifted, ``10`` otherwise
      - others:  ``1``

    Returns up to ``limit`` rows, descending by score.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    rows: list[tuple[float, dict]] = []
    for n in store.all_nodes(layer=layer):
        if not isinstance(n, dict) or not _truthy_anomaly(n):
            continue
        score, why = _anomaly_score(n)
        rows.append(
            (
                score,
                {
                    "id": n.get("id"),
                    "type": n.get("type"),
                    "layer": n.get("layer"),
                    "label": n.get("label"),
                    "score": round(score, 3),
                    "why": why,
                },
            )
        )
    rows.sort(key=lambda kv: kv[0], reverse=True)
    return [row for _, row in rows[: max(0, int(limit))]]


# ---------------------------------------------------------------------------
# 1c. cross_layer_search
# ---------------------------------------------------------------------------


def _enrich_with_neighbors(
    hit: dict,
    nodes_by_id: dict[str, dict],
    out_by_src: dict[str, list[dict]],
    in_by_tgt: dict[str, list[dict]],
) -> dict:
    """Attach one-hop cross-layer neighbours to a search hit."""
    nid = hit.get("id") or ""
    src_layer = (
        hit.get("layer") or (nodes_by_id.get(nid, {}) or {}).get("layer") or ""
    )
    out_neigh: list[dict] = []
    seen: set[str] = set()
    for e in out_by_src.get(nid, []):
        tgt = e.get("target") or ""
        n = nodes_by_id.get(tgt)
        if not n:
            continue
        if n.get("layer") == src_layer:
            continue
        if tgt in seen:
            continue
        seen.add(tgt)
        out_neigh.append(
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "layer": n.get("layer"),
                "relation": e.get("relation"),
                "label": n.get("label"),
            }
        )
    in_neigh: list[dict] = []
    seen = set()
    for e in in_by_tgt.get(nid, []):
        src = e.get("source") or ""
        n = nodes_by_id.get(src)
        if not n:
            continue
        if n.get("layer") == src_layer:
            continue
        if src in seen:
            continue
        seen.add(src)
        in_neigh.append(
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "layer": n.get("layer"),
                "relation": e.get("relation"),
                "label": n.get("label"),
            }
        )
    enriched = dict(hit)
    enriched["cross_layer_neighbors"] = {
        "out": out_neigh[:5],
        "in": in_neigh[:5],
    }
    return enriched


@mcp.tool()
def cross_layer_search(
    query: str,
    limit: int = 20,
    persist_dir: str | None = None,
) -> list[dict]:
    """Semantic search wrapper that enriches each hit with one-hop
    neighbours from OTHER layers.

    Falls back to a simple substring scan over ``label`` / ``id`` when
    LanceDB is unavailable. De-dups by ``id``; caps total at ``limit``.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    raw_hits = store.semantic_search(
        query=query, layer=None, limit=int(limit)
    )

    hits: list[dict] = []
    if not raw_hits or (
        isinstance(raw_hits[0], dict) and "error" in raw_hits[0]
    ):
        # Lance unavailable — substring fallback.
        q = (query or "").lower()
        for n in store.all_nodes():
            if not isinstance(n, dict):
                continue
            blob = f"{n.get('id', '')} {n.get('label', '')}".lower()
            if q and q in blob:
                hits.append(n)
            if len(hits) >= int(limit):
                break
    else:
        hits = [h for h in raw_hits if isinstance(h, dict)]

    nodes_by_id = _index_nodes(store.all_nodes())
    out_by_src, in_by_tgt = _build_edge_indexes(store.all_edges())

    seen: set[str] = set()
    enriched: list[dict] = []
    for h in hits:
        hid = h.get("id") or ""
        if not hid or hid in seen:
            continue
        seen.add(hid)
        # Make sure we have full node payload.
        full = nodes_by_id.get(hid, h)
        merged = {
            **h,
            "id": hid,
            "type": full.get("type"),
            "layer": full.get("layer"),
            "label": full.get("label"),
        }
        enriched.append(
            _enrich_with_neighbors(merged, nodes_by_id, out_by_src, in_by_tgt)
        )
        if len(enriched) >= int(limit):
            break
    return enriched


# ---------------------------------------------------------------------------
# 1d. service_mermaid
# ---------------------------------------------------------------------------


_MERMAID_CLASSES = {
    "k8s_match": "fill:#c6efce,stroke:#2f7d32",
    "k8s_drift": "fill:#fbd0d0,stroke:#c62828",
    "argo": "fill:#e9d5ff,stroke:#6a1b9a",
    "logs_error": "fill:#fff4b3,stroke:#bf8f00",
    "logs_ok": "fill:#eeeeee,stroke:#616161",
    "metrics_high": "fill:#ffd6a5,stroke:#bf360c",
    "metrics_ok": "fill:#fff0d6,stroke:#bf6f00",
    "traces_anom": "fill:#ffb4ab,stroke:#a4151c",
    "traces_ok": "fill:#c6efce,stroke:#2f7d32",
    "cold": "fill:#cfe2ff,stroke:#0d47a1",
}


def _mermaid_node_class(node: dict) -> str:
    layer = node.get("layer") or ""
    ntype = node.get("type") or ""
    if layer == "k8s" or ntype == "k8s_resource":
        return "k8s_match"
    if layer == "argo" or ntype == "argo_app":
        return "argo"
    if layer == "logs" or ntype == "log_template":
        return "logs_error" if bool(node.get("is_error")) else "logs_ok"
    if layer == "metrics" or ntype == "metric":
        return (
            "metrics_high"
            if bool(node.get("is_high_cardinality"))
            else "metrics_ok"
        )
    if layer == "traces" or ntype in {"service", "operation"}:
        return "traces_anom" if _truthy_anomaly(node) else "traces_ok"
    return "cold"


def _mermaid_safe_id(nid: str) -> str:
    out = []
    for ch in nid:
        out.append(ch if ch.isalnum() else "_")
    return "n_" + "".join(out)[:80]


def _mermaid_label(node: dict) -> str:
    label = node.get("label") or node.get("id") or ""
    label = str(label).replace('"', "'").replace("\n", " ")
    return label[:60]


@mcp.tool()
def service_mermaid(
    service: str,
    layers: list[str] | None = None,
    env: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Mermaid diagram of a service's neighbourhood across the requested
    layers (default: all). Groups nodes by layer using ``subgraph`` blocks
    and color-codes per layer/state. Capped at 80 nodes for readability.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = _index_nodes(nodes)
    out_by_src, in_by_tgt = _build_edge_indexes(edges)

    # Build the rx graph for BFS.
    rx_graph = RxGraph.from_store(nodes, edges)

    seeds: list[str] = []
    app_id = _resolve_app_id(nodes_by_id, service, env)
    if app_id:
        seeds.append(app_id)
    sid = _service_node_id(service)
    if sid in nodes_by_id and nodes_by_id[sid].get("type") == "service":
        seeds.append(sid)
    if not seeds:
        return {
            "mermaid": (
                "graph TD\n"
                f"  empty[\"No nodes found for service '{service}'\"]\n"
            ),
            "node_count": 0,
            "seeds": [],
        }

    selected: set[str] = set(seeds)
    visited_ids: dict[str, int] = {}
    for s in seeds:
        if rx_graph.has_node(s):
            visited_ids.update(rx_graph.bfs(s, direction="both", max_depth=2))
    for nid, _depth in sorted(visited_ids.items(), key=lambda kv: kv[1]):
        if len(selected) >= 80:
            break
        node = nodes_by_id.get(nid)
        if not node:
            continue
        if layers and node.get("layer") not in layers:
            continue
        selected.add(nid)

    # Group by layer.
    layer_groups: dict[str, list[str]] = defaultdict(list)
    for nid in selected:
        node = nodes_by_id.get(nid) or {}
        layer_groups[node.get("layer") or "cold"].append(nid)

    lines: list[str] = ["graph TD"]
    class_lines: list[str] = []
    used_classes: set[str] = set()

    for layer_name in sorted(layer_groups.keys()):
        ids = sorted(layer_groups[layer_name])
        if not ids:
            continue
        lines.append(f'  subgraph layer_{layer_name}["{layer_name}"]')
        for nid in ids:
            node = nodes_by_id.get(nid) or {"id": nid, "label": nid}
            mid = _mermaid_safe_id(nid)
            label = _mermaid_label(node)
            cls = _mermaid_node_class(node)
            used_classes.add(cls)
            lines.append(f'    {mid}["{label}"]:::{cls}')
        lines.append("  end")

    # Edges within the selected subgraph.
    selected_set = selected
    edge_count = 0
    for e in edges:
        s = e.get("source") or ""
        t = e.get("target") or ""
        if s not in selected_set or t not in selected_set:
            continue
        rel = e.get("relation") or ""
        sm = _mermaid_safe_id(s)
        tm = _mermaid_safe_id(t)
        if rel:
            lines.append(f"  {sm} -->|{rel}| {tm}")
        else:
            lines.append(f"  {sm} --> {tm}")
        edge_count += 1
        if edge_count >= 200:
            break

    for cls in sorted(used_classes):
        style = _MERMAID_CLASSES.get(cls, "")
        if style:
            class_lines.append(f"  classDef {cls} {style};")
    lines.extend(class_lines)

    return {
        "mermaid": "\n".join(lines) + "\n",
        "node_count": len(selected),
        "seeds": seeds,
    }


# ---------------------------------------------------------------------------
# 1e. health_score
# ---------------------------------------------------------------------------


@mcp.tool()
def health_score(
    service: str,
    env: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Composite 0..100 health score for ``service`` from the present
    factors (reliability, log_health, metric_hygiene, live_match).

    Returns ``{"value": 0..100|null, "factors": {...}}`` — same block as
    ``service_one_pager().score``. Missing factors are skipped, not
    penalised.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes_by_id = _index_nodes(store.all_nodes())
    out_by_src, in_by_tgt = _build_edge_indexes(store.all_edges())
    profile = _gather_service_profile(
        nodes_by_id, out_by_src, in_by_tgt, service, env
    )
    return profile.get("score") or {"value": None, "factors": {}}


# ---------------------------------------------------------------------------
# 1f. cross_layer_fuse
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join(["---"] * len(headers)) + "|"
    body = ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join([line, sep, *body])


@mcp.tool()
def cross_layer_fuse(
    env: str,
    mcp_url: str | None = None,
    mcp_stdio: str | None = None,
    out_dir: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Capstone — full cross-layer drift report for ``env``.

    Sections:
      1. iac (rendered) vs k8s (live)
      2. argo (declared) vs iac (rendered)
      3. logs (active) vs applications (silent apps)
      4. metrics (scraped) vs applications (unmonitored)
      5. traces (sampled) vs applications (untraced)

    When ``mcp_url`` (or ``mcp_stdio``) is provided, refreshes each live
    layer first; otherwise operates on the cached store. Hard-fails when
    the rendered manifest file ``rendered_apps_<env>.json`` is missing
    (matches the existing ``fuse-live`` convention). Per-tool data
    unavailability soft-degrades — sections without data render as
    "data unavailable".

    Writes ``<out_dir>/cross_drift_<env>.md`` and
    ``<out_dir>/cross_drift_<env>.json``.
    """
    persist_path = Path(_resolve_persist(persist_dir)).resolve()
    out_path = Path(out_dir).resolve() if out_dir else persist_path
    out_path.mkdir(parents=True, exist_ok=True)

    rendered_file = persist_path / f"rendered_apps_{env}.json"
    if not rendered_file.exists():
        return {
            "error": (
                f"rendered_apps_{env}.json not found at {rendered_file} — "
                "produce CUE rendering first (kuberly-stack render-apps)"
            ),
            "env": env,
            "rendered_file": str(rendered_file),
        }

    refreshed: list[str] = []
    refresh_errors: dict[str, str] = {}
    if mcp_url or mcp_stdio:
        from .regenerate import regenerate_layer

        for layer_name in ("k8s", "argo", "logs", "metrics", "traces"):
            try:
                regenerate_layer(
                    layer=layer_name,
                    mcp_url=mcp_url,
                    mcp_stdio=mcp_stdio,
                    persist_dir=str(persist_path),
                )
                refreshed.append(layer_name)
            except Exception as exc:  # soft-degrade per layer
                refresh_errors[layer_name] = str(exc)

    store = open_store(persist_path)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = _index_nodes(nodes)
    out_by_src, in_by_tgt = _build_edge_indexes(edges)

    # ---- collect per-env application set ------------------------------------
    env_apps: list[dict] = [
        n
        for n in nodes
        if n.get("type") == "application" and n.get("environment") == env
    ]
    env_app_ids = {n["id"] for n in env_apps}
    has_apps = len(env_apps) > 0

    # ---- 1. iac vs k8s ------------------------------------------------------
    iac_vs_k8s: dict[str, list[dict]] = {
        "matched": [],
        "missing": [],
        "extra": [],
        "data_available": False,
    }
    rendered_resources = [n for n in nodes if n.get("type") == "rendered_resource" and n.get("env") == env]
    k8s_resources = [n for n in nodes if n.get("type") == "k8s_resource"]
    if rendered_resources or k8s_resources:
        iac_vs_k8s["data_available"] = True
        match_edges = {
            (e.get("source"), e.get("target"))
            for e in edges
            if e.get("relation") == "live_match"
        }
        rendered_ids = {n["id"] for n in rendered_resources}
        for rr in rendered_resources:
            matched_to = [t for s, t in match_edges if s == rr["id"]]
            if matched_to:
                iac_vs_k8s["matched"].append(
                    {"rendered": rr["id"], "live": matched_to}
                )
            else:
                iac_vs_k8s["missing"].append({"rendered": rr["id"]})
        live_ids_seen: set[str] = {t for _, t in match_edges if _ in rendered_ids}
        # "extra" lives — k8s resources nobody rendered.
        for k in k8s_resources:
            if k["id"] not in live_ids_seen:
                iac_vs_k8s["extra"].append({"live": k["id"]})

    # ---- 2. argo vs iac -----------------------------------------------------
    argo_vs_iac: dict[str, list[str]] = {
        "tracked": [],
        "untracked_apps": [],
        "argo_without_app": [],
        "data_available": False,
    }
    argo_apps = [n for n in nodes if n.get("type") == "argo_app"]
    if argo_apps or env_apps:
        argo_vs_iac["data_available"] = bool(argo_apps)
        tracked_app_ids = {
            e.get("target")
            for e in edges
            if e.get("relation") == "tracks"
            and e.get("target") in env_app_ids
        }
        argo_vs_iac["tracked"] = sorted(x for x in tracked_app_ids if x)
        if has_apps and argo_apps:
            argo_vs_iac["untracked_apps"] = sorted(
                env_app_ids - tracked_app_ids
            )
            tracked_argo_ids = {
                e.get("source")
                for e in edges
                if e.get("relation") == "tracks"
            }
            argo_vs_iac["argo_without_app"] = sorted(
                a["id"] for a in argo_apps if a["id"] not in tracked_argo_ids
            )

    # ---- 3. logs vs applications -------------------------------------------
    logs_vs_apps: dict[str, list[str] | bool] = {
        "with_logs": [],
        "silent_apps": [],
        "data_available": False,
    }
    log_templates = [n for n in nodes if n.get("type") == "log_template"]
    if log_templates:
        logs_vs_apps["data_available"] = True
        emit_targets = defaultdict(int)
        for e in edges:
            if e.get("relation") == "emits" and e.get("source") in env_app_ids:
                emit_targets[e["source"]] += 1
        if has_apps:
            logs_vs_apps["with_logs"] = sorted(
                aid for aid, c in emit_targets.items() if c > 0
            )
            logs_vs_apps["silent_apps"] = sorted(
                env_app_ids - set(emit_targets.keys())
            )

    # ---- 4. metrics vs applications ----------------------------------------
    metrics_vs_apps: dict[str, list[str] | bool] = {
        "instrumented": [],
        "unmonitored_apps": [],
        "data_available": False,
    }
    metrics = [n for n in nodes if n.get("type") == "metric"]
    if metrics:
        metrics_vs_apps["data_available"] = True
        inst_sources: dict[str, int] = defaultdict(int)
        for e in edges:
            if (
                e.get("relation") == "instrumented_by"
                and e.get("source") in env_app_ids
            ):
                inst_sources[e["source"]] += 1
        if has_apps:
            metrics_vs_apps["instrumented"] = sorted(
                aid for aid, c in inst_sources.items() if c > 0
            )
            metrics_vs_apps["unmonitored_apps"] = sorted(
                env_app_ids - set(inst_sources.keys())
            )

    # ---- 5. traces vs applications -----------------------------------------
    traces_vs_apps: dict[str, list[str] | bool] = {
        "traced": [],
        "untraced_apps": [],
        "data_available": False,
    }
    services = [n for n in nodes if n.get("type") == "service"]
    if services:
        traces_vs_apps["data_available"] = True
        traced_sources: set[str] = set()
        for e in edges:
            if e.get("relation") == "traces_as" and e.get("source") in env_app_ids:
                traced_sources.add(e["source"])
        if has_apps:
            traces_vs_apps["traced"] = sorted(traced_sources)
            traces_vs_apps["untraced_apps"] = sorted(
                env_app_ids - traced_sources
            )

    summary_rows = [
        ["iac vs k8s",
         "available" if iac_vs_k8s["data_available"] else "data unavailable",
         f"matched={len(iac_vs_k8s['matched'])} missing={len(iac_vs_k8s['missing'])} extra={len(iac_vs_k8s['extra'])}"],
        ["argo vs iac",
         "available" if argo_vs_iac["data_available"] else "data unavailable",
         f"tracked={len(argo_vs_iac['tracked'])} untracked={len(argo_vs_iac['untracked_apps'])}"],
        ["logs vs apps",
         "available" if logs_vs_apps["data_available"] else "data unavailable",
         f"with_logs={len(logs_vs_apps['with_logs'])} silent={len(logs_vs_apps['silent_apps'])}"],
        ["metrics vs apps",
         "available" if metrics_vs_apps["data_available"] else "data unavailable",
         f"instrumented={len(metrics_vs_apps['instrumented'])} unmonitored={len(metrics_vs_apps['unmonitored_apps'])}"],
        ["traces vs apps",
         "available" if traces_vs_apps["data_available"] else "data unavailable",
         f"traced={len(traces_vs_apps['traced'])} untraced={len(traces_vs_apps['untraced_apps'])}"],
    ]

    json_blob = {
        "env": env,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "rendered_file": str(rendered_file),
        "refreshed_layers": refreshed,
        "refresh_errors": refresh_errors,
        "app_count": len(env_apps),
        "iac_vs_k8s": iac_vs_k8s,
        "argo_vs_iac": argo_vs_iac,
        "logs_vs_apps": logs_vs_apps,
        "metrics_vs_apps": metrics_vs_apps,
        "traces_vs_apps": traces_vs_apps,
    }

    md_lines = [
        f"# Cross-layer drift — `{env}`",
        "",
        f"_Generated_: {json_blob['generated_at']}",
        f"_Rendered file_: `{rendered_file}`",
        f"_Refreshed layers_: {refreshed or 'none (cached)'}",
        f"_Applications in env_: **{len(env_apps)}**",
        "",
        "## Summary",
        "",
        _md_table(["Section", "State", "Counts"], summary_rows),
        "",
        "## iac (rendered) vs k8s (live)",
    ]
    if not iac_vs_k8s["data_available"]:
        md_lines.append("_data unavailable_")
    else:
        md_lines.extend(
            [
                f"- matched: **{len(iac_vs_k8s['matched'])}**",
                f"- missing (rendered, no live): **{len(iac_vs_k8s['missing'])}**",
                f"- extra (live, not rendered): **{len(iac_vs_k8s['extra'])}**",
            ]
        )
    md_lines.extend(["", "## argo (declared) vs iac (rendered)"])
    if not argo_vs_iac["data_available"]:
        md_lines.append("_data unavailable_")
    else:
        md_lines.extend(
            [
                f"- tracked apps: **{len(argo_vs_iac['tracked'])}**",
                f"- untracked apps: **{len(argo_vs_iac['untracked_apps'])}**",
                f"- argo apps without IaC counterpart: **{len(argo_vs_iac['argo_without_app'])}**",
            ]
        )
    md_lines.extend(["", "## logs (active) vs applications"])
    if not logs_vs_apps["data_available"]:
        md_lines.append("_data unavailable_")
    else:
        md_lines.extend(
            [
                f"- apps with logs: **{len(logs_vs_apps['with_logs'])}**",
                f"- silent apps (0 templates): **{len(logs_vs_apps['silent_apps'])}**",
            ]
        )
    md_lines.extend(["", "## metrics (scraped) vs applications"])
    if not metrics_vs_apps["data_available"]:
        md_lines.append("_data unavailable_")
    else:
        md_lines.extend(
            [
                f"- instrumented apps: **{len(metrics_vs_apps['instrumented'])}**",
                f"- unmonitored apps: **{len(metrics_vs_apps['unmonitored_apps'])}**",
            ]
        )
    md_lines.extend(["", "## traces (sampled) vs applications"])
    if not traces_vs_apps["data_available"]:
        md_lines.append("_data unavailable_")
    else:
        md_lines.extend(
            [
                f"- traced apps: **{len(traces_vs_apps['traced'])}**",
                f"- untraced apps: **{len(traces_vs_apps['untraced_apps'])}**",
            ]
        )

    md_path = out_path / f"cross_drift_{env}.md"
    json_path = out_path / f"cross_drift_{env}.json"
    md_path.write_text("\n".join(md_lines) + "\n")
    json_path.write_text(json.dumps(json_blob, indent=2, default=str))

    return {
        "env": env,
        "md_path": str(md_path),
        "json_path": str(json_path),
        "summary": json_blob,
    }
