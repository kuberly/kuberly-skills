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


# ---------------------------------------------------------------------------
# /api/v1/meta-overview — graph_layer:* nodes + feeds_into / summarized_by
# ---------------------------------------------------------------------------


async def meta_overview_endpoint(_request: Request) -> JSONResponse:
    """Return the meta layer as a small ``{nodes, links}`` payload.

    Sources every ``type=graph_layer`` node and the ``feeds_into`` /
    ``summarized_by`` edges produced by ``MetaLayer``. Pure GraphStore
    read; no live calls.
    """
    try:
        store = open_store(_persist_dir())
        all_n = store.all_nodes()
        all_e = store.all_edges()
    except Exception as exc:
        return _err(f"meta-overview query failed: {exc}", 500)

    nodes: list[dict] = []
    seen: set[str] = set()
    for n in all_n:
        if not isinstance(n, dict):
            continue
        if n.get("type") != "graph_layer":
            continue
        nid = n.get("id")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        nodes.append(
            {
                "id": nid,
                "name": n.get("name") or n.get("label") or nid,
                "type": "graph_layer",
                "layer_type": n.get("layer_type", "unknown"),
                "refresh_trigger": n.get("refresh_trigger", "manual"),
                "node_count": int(n.get("node_count") or 0),
                "edge_count": int(n.get("edge_count") or 0),
                "last_refresh": n.get("last_refresh", ""),
                "node_types": n.get("node_types") or [],
            }
        )

    node_ids = {n["id"] for n in nodes}
    links: list[dict] = []
    for e in all_e:
        if not isinstance(e, dict):
            continue
        rel = e.get("relation", "")
        if rel not in ("feeds_into", "summarized_by"):
            continue
        s = e.get("source")
        t = e.get("target")
        if s in node_ids and t in node_ids:
            links.append({"source": s, "target": t, "relation": rel})

    return _ok(
        {
            "node_count": len(nodes),
            "edge_count": len(links),
            "nodes": nodes,
            "links": links,
        }
    )


# ---------------------------------------------------------------------------
# /api/v1/aws-services — AWS architecture tile data
# ---------------------------------------------------------------------------

# Map AWS scanner node `type` → (Service display label, Category).
# Categories: Compute, Storage, Database, Network, Security/IAM, Edge/CDN,
# Monitoring, Lambda/Serverless, Other.
_AWS_SERVICE_TABLE: dict[str, tuple[str, str]] = {
    # Compute
    "aws_ec2":            ("EC2 Instance",         "Compute"),
    "aws_eks":            ("EKS Cluster",          "Compute"),
    "aws_eks_nodegroup":  ("EKS Node Group",       "Compute"),
    "aws_fargate_profile":("Fargate Profile",      "Compute"),
    "aws_account":        ("AWS Account",          "Compute"),
    # Storage
    "aws_s3":             ("S3 Bucket",            "Storage"),
    "aws_ebs":            ("EBS Volume",           "Storage"),
    "aws_ecr_repo":       ("ECR Repository",       "Storage"),
    # Database
    "aws_rds_cluster":    ("RDS Cluster",          "Database"),
    "aws_rds_instance":   ("RDS Instance",         "Database"),
    "aws_elasticache":    ("ElastiCache",          "Database"),
    # Network
    "aws_vpc":            ("VPC",                  "Network"),
    "aws_subnet":         ("Subnet",               "Network"),
    "aws_rtb":            ("Route Table",          "Network"),
    "aws_nat":            ("NAT Gateway",          "Network"),
    "aws_igw":            ("Internet Gateway",     "Network"),
    "aws_vpce":           ("VPC Endpoint",         "Network"),
    "aws_lb":             ("Load Balancer",        "Network"),
    # Security/IAM
    "aws_sg":             ("Security Group",       "Security/IAM"),
    "aws_iam_role":       ("IAM Role",             "Security/IAM"),
    "aws_iam_policy":     ("IAM Policy",           "Security/IAM"),
    "aws_iam_instance_profile": ("IAM Instance Profile", "Security/IAM"),
    "aws_acm":            ("ACM Certificate",      "Security/IAM"),
    # Edge/CDN
    "aws_cloudfront":     ("CloudFront",           "Edge/CDN"),
    "aws_r53_zone":       ("Route 53 Zone",        "Edge/CDN"),
    # Monitoring
    "aws_cw_log_group":   ("CloudWatch Log Group", "Monitoring"),
    # Lambda / Serverless
    "aws_lambda":         ("Lambda Function",      "Lambda/Serverless"),
}

# Stable ordering of the categories as they should render in the UI.
_AWS_CATEGORY_ORDER = (
    "Compute",
    "Storage",
    "Database",
    "Network",
    "Security/IAM",
    "Edge/CDN",
    "Monitoring",
    "Lambda/Serverless",
    "Other",
)


async def aws_services_endpoint(_request: Request) -> JSONResponse:
    """Return AWS resources grouped into service categories.

    Walks every node where ``type`` starts with ``aws_`` (or ``id`` starts
    with ``aws:``), maps it to a service+category via ``_AWS_SERVICE_TABLE``
    and returns a category→services structure ready for tile rendering.
    """
    try:
        store = open_store(_persist_dir())
        rows = store.all_nodes()
    except Exception as exc:
        return _err(f"aws-services query failed: {exc}", 500)

    # service-key -> {service, category, node_type, count, sample_id, sample_label}
    by_service: dict[str, dict] = {}
    total = 0
    for n in rows:
        if not isinstance(n, dict):
            continue
        ntype = (n.get("type") or "").strip()
        nid = n.get("id") or ""
        if not (ntype.startswith("aws_") or nid.startswith("aws:")):
            continue
        total += 1
        service, category = _AWS_SERVICE_TABLE.get(
            ntype, (ntype.replace("aws_", "").replace("_", " ").title() or "AWS Resource", "Other")
        )
        key = f"{category}::{ntype or 'aws'}"
        bucket = by_service.get(key)
        if bucket is None:
            by_service[key] = {
                "service": service,
                "category": category,
                "node_type": ntype or "aws",
                "count": 1,
                "sample_id": nid,
                "sample_label": n.get("label") or nid,
            }
        else:
            bucket["count"] += 1

    # Group services by category in stable order.
    by_cat: dict[str, list[dict]] = {c: [] for c in _AWS_CATEGORY_ORDER}
    for bucket in by_service.values():
        cat = bucket["category"] if bucket["category"] in by_cat else "Other"
        by_cat[cat].append(bucket)

    service_categories: list[dict] = []
    for cat in _AWS_CATEGORY_ORDER:
        services = sorted(by_cat[cat], key=lambda b: -b["count"])
        if not services:
            continue
        service_categories.append(
            {
                "category": cat,
                "service_count": len(services),
                "resource_count": sum(s["count"] for s in services),
                "services": services,
            }
        )

    return _ok(
        {
            "total_resources": total,
            "category_count": len(service_categories),
            "service_categories": service_categories,
        }
    )


# ---------------------------------------------------------------------------
# /api/v1/compliance — ComplianceLayer findings
# ---------------------------------------------------------------------------


async def compliance_endpoint(request: Request) -> JSONResponse:
    """Return compliance violation nodes with breakdowns + filters.

    Query params:
      severity  filter to a single severity (HIGH/MEDIUM/LOW)
      rule_id   filter to a single rule id (e.g. R001)
      limit     cap on findings array (default 500)
    """
    severity = (_str_param(request, "severity") or "").upper() or None
    rule_id = _str_param(request, "rule_id")
    limit = max(1, _int_param(request, "limit", 500))

    try:
        store = open_store(_persist_dir())
        rows = store.all_nodes(layer="compliance")
    except Exception as exc:
        return _err(f"compliance query failed: {exc}", 500)

    findings: list[dict] = []
    by_severity: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    total = 0
    for n in rows:
        if not isinstance(n, dict):
            continue
        if n.get("type") != "compliance_violation":
            continue
        total += 1
        sev = str(n.get("severity") or "UNKNOWN").upper()
        rid = str(n.get("rule_id") or "")
        by_severity[sev] = by_severity.get(sev, 0) + 1
        if rid:
            by_rule[rid] = by_rule.get(rid, 0) + 1
        if severity and sev != severity:
            continue
        if rule_id and rid != rule_id:
            continue
        if len(findings) < limit:
            findings.append(
                {
                    "id": n.get("id"),
                    "rule_id": rid,
                    "severity": sev,
                    "resource_type": n.get("resource_type") or "",
                    "target_resource": n.get("resource_id") or "",
                    "env": n.get("env") or "",
                    "address": n.get("address") or "",
                    "namespace": n.get("namespace") or "",
                    "name": n.get("name") or "",
                    "description": n.get("description") or "",
                    "recommendation": n.get("recommendation") or "",
                    "label": n.get("label") or "",
                }
            )

    return _ok(
        {
            "total": total,
            "by_severity": by_severity,
            "by_rule": by_rule,
            "filters": {"severity": severity, "rule_id": rule_id, "limit": limit},
            "findings": findings,
        }
    )


# ---------------------------------------------------------------------------
# /api/v1/communities — modularity-based community detection
# ---------------------------------------------------------------------------


async def communities_endpoint(request: Request) -> JSONResponse:
    """Detect communities over a filtered subgraph.

    Builds a NetworkX undirected graph from ``store.all_nodes(layer=…)`` +
    ``store.all_edges(layer=…)`` (the same filter the ``/graph`` endpoint
    uses) and runs ``nx.community.greedy_modularity_communities``.

    Query params:
      layer      optional layer filter (matches node.layer)
      type       optional type filter (matches node.type)
      algorithm  reserved — currently always ``modularity``
                 (``leidenalg`` would require an extra dep we won't add).
      limit      cap on the candidate node set (default 5000).

    Response shape:
        {
          "node_count": N, "edge_count": E,
          "community_count": M, "modularity": float,
          "algorithm": "greedy_modularity",
          "communities": {"<node_id>": <int>, ...},
          "sizes": [{"community": 0, "size": K}, ...]
        }
    """
    layer = _str_param(request, "layer")
    type_ = _str_param(request, "type")
    algorithm = (_str_param(request, "algorithm") or "modularity").lower()
    limit = max(1, _int_param(request, "limit", 5000))

    try:
        import networkx as nx  # transitive dep via lance/sentence-transformers
        from networkx.algorithms.community import (
            greedy_modularity_communities,
            modularity as nx_modularity,
        )
    except Exception as exc:
        return _err(f"networkx unavailable: {exc}", 500)

    try:
        store = open_store(_persist_dir())
        all_n = store.all_nodes(layer=layer)
        all_e = store.all_edges(layer=layer)
    except Exception as exc:
        return _err(f"communities query failed: {exc}", 500)

    # Same filter logic as /api/v1/graph: cap nodes, include only edges
    # whose endpoints both survived.
    node_ids: list[str] = []
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
        node_ids.append(nid)
        if len(node_ids) >= limit:
            break

    id_set = set(node_ids)
    G = nx.Graph()
    G.add_nodes_from(node_ids)
    for e in all_e:
        if not isinstance(e, dict):
            continue
        s = e.get("source")
        t = e.get("target")
        if s in id_set and t in id_set and s != t:
            G.add_edge(s, t)

    edge_count = G.number_of_edges()
    if G.number_of_nodes() == 0:
        return _ok(
            {
                "node_count": 0,
                "edge_count": 0,
                "community_count": 0,
                "modularity": 0.0,
                "algorithm": "greedy_modularity",
                "filters": {"layer": layer, "type": type_, "limit": limit},
                "communities": {},
                "sizes": [],
            }
        )

    try:
        comms = list(greedy_modularity_communities(G))
    except Exception as exc:
        return _err(f"community detection failed: {exc}", 500)

    # community index → set of nodes; sort by descending size for stable
    # colours (largest community = index 0).
    comms_sorted = sorted(comms, key=lambda c: -len(c))
    node_to_comm: dict[str, int] = {}
    sizes: list[dict] = []
    for idx, members in enumerate(comms_sorted):
        sizes.append({"community": idx, "size": len(members)})
        for m in members:
            node_to_comm[m] = idx

    try:
        mod_score = float(nx_modularity(G, comms_sorted)) if edge_count else 0.0
    except Exception:
        mod_score = 0.0

    return _ok(
        {
            "node_count": G.number_of_nodes(),
            "edge_count": edge_count,
            "community_count": len(comms_sorted),
            "modularity": mod_score,
            "algorithm": "greedy_modularity",
            "requested_algorithm": algorithm,
            "filters": {"layer": layer, "type": type_, "limit": limit},
            "communities": node_to_comm,
            "sizes": sizes,
        }
    )
