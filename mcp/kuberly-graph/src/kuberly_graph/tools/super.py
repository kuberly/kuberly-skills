"""Phase 8L — LLM-agent-friendly fusion super-tools.

Five high-level tools that join across primitives so the AI agent gets one
structured answer per question instead of having to BFS by hand:

- ``summarize_environment(env=None)`` — per-env counts/breakdowns over all
  populated layers (cold IaC + k8s + AWS + observability + compliance + …).
- ``trace_data_flow(from_id, to_id)`` — shortest path across the full graph
  with semantic hop annotations + Mermaid diagram.
- ``incident_context(symptom, service=None)`` — joins ``semantic_search`` +
  log/metric/trace anomaly probes + (optional) ``service_one_pager`` into a
  single triage report.
- ``service_lineage(service)`` — upstream/downstream callers/callees +
  consumed secrets/configmaps + network exposure + Mermaid.
- ``node_explain(node_id)`` — full metadata, neighbours grouped by relation,
  ancestor derivation chain, Mermaid.

All tools are pure-read against the GraphStore + the cached RxGraph. They
soft-degrade when a layer is empty: missing data → ``[]`` / ``null`` /
``"data unavailable"``, never crash. No live MCP calls.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ..graph.rustworkx_graph import RxGraph
from ..server import SERVER_CONFIG, mcp
from ..store import open_store


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _mermaid_safe_id(nid: str) -> str:
    out: list[str] = []
    for ch in nid or "":
        out.append(ch if ch.isalnum() else "_")
    return "n_" + "".join(out)[:80]


def _mermaid_label(node: dict) -> str:
    label = node.get("label") or node.get("id") or ""
    label = str(label).replace('"', "'").replace("\n", " ")
    return label[:60]


# AWS service-type → category map (subset of dashboard/api.py table — kept
# local so we don't import the dashboard layer from a tool).
_AWS_TYPE_CATEGORIES: dict[str, str] = {
    "aws_ec2": "Compute",
    "aws_eks": "Compute",
    "aws_eks_nodegroup": "Compute",
    "aws_fargate_profile": "Compute",
    "aws_account": "Compute",
    "aws_s3": "Storage",
    "aws_ebs": "Storage",
    "aws_ecr_repo": "Storage",
    "aws_rds_cluster": "Database",
    "aws_rds_instance": "Database",
    "aws_elasticache": "Database",
    "aws_vpc": "Network",
    "aws_subnet": "Network",
    "aws_rtb": "Network",
    "aws_nat": "Network",
    "aws_igw": "Network",
    "aws_vpce": "Network",
    "aws_lb": "Network",
    "aws_sg": "Security/IAM",
    "aws_iam_role": "Security/IAM",
    "aws_iam_policy": "Security/IAM",
    "aws_iam_instance_profile": "Security/IAM",
    "aws_acm": "Security/IAM",
    "aws_cloudfront": "Edge/CDN",
    "aws_r53_zone": "Edge/CDN",
    "aws_cw_log_group": "Monitoring",
    "aws_lambda": "Lambda/Serverless",
}


def _aws_category(ntype: str) -> str:
    return _AWS_TYPE_CATEGORIES.get(ntype, "Other")


# ---------------------------------------------------------------------------
# 1. summarize_environment
# ---------------------------------------------------------------------------


@mcp.tool()
def summarize_environment(
    env: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Structured per-env summary across all populated graph layers.

    If ``env`` is None and exactly one environment is detected (typical for
    a customer fork like ``triagent-dev``), it is auto-selected. Otherwise
    the union of all environments is summarised.

    Returns counts + breakdowns for IaC (modules/components/applications),
    k8s resources by kind, AWS resources by category and type, observability
    (logs/metrics/traces/scrape targets), compliance violations by severity
    and rule, anomaly counts by layer, and graph-level health (totals +
    populated-layer count).
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()

    # Auto-detect env when omitted: pick the single environment present in
    # cold IaC application/component nodes.
    env_set: set[str] = set()
    for n in nodes:
        e = n.get("environment") or n.get("env")
        if e:
            env_set.add(str(e))
    if env is None:
        env = next(iter(env_set)) if len(env_set) == 1 else None
    target_env = env

    def _env_match(node: dict) -> bool:
        if target_env is None:
            return True
        e = node.get("environment") or node.get("env")
        return e == target_env

    # IaC slice ---------------------------------------------------------------
    modules = sum(1 for n in nodes if n.get("type") == "module")
    components = sum(
        1 for n in nodes if n.get("type") == "component" and _env_match(n)
    )
    applications = sum(
        1 for n in nodes if n.get("type") == "application" and _env_match(n)
    )

    # k8s slice ---------------------------------------------------------------
    k8s_by_kind: dict[str, int] = defaultdict(int)
    for n in nodes:
        if n.get("type") != "k8s_resource":
            continue
        # k8s namespace usually scoped per-cluster, not per-env. Best effort:
        # match on "env" metadata when present, else include in cluster count.
        if target_env is not None:
            n_env = n.get("env") or n.get("environment")
            if n_env and n_env != target_env:
                continue
        kind = (n.get("kind") or "Unknown").lower()
        k8s_by_kind[kind] += 1
    k8s_counts: dict[str, int] = {
        "pods": k8s_by_kind.get("pod", 0),
        "deployments": k8s_by_kind.get("deployment", 0),
        "services": k8s_by_kind.get("service", 0),
        "ingresses": k8s_by_kind.get("ingress", 0),
        "configmaps": k8s_by_kind.get("configmap", 0),
        "secrets": k8s_by_kind.get("secret", 0),
        "statefulsets": k8s_by_kind.get("statefulset", 0),
        "daemonsets": k8s_by_kind.get("daemonset", 0),
        "total": sum(k8s_by_kind.values()),
    }

    # AWS slice ---------------------------------------------------------------
    aws_by_category: dict[str, int] = defaultdict(int)
    aws_by_service: dict[str, int] = defaultdict(int)
    for n in nodes:
        ntype = (n.get("type") or "").strip()
        nid = (n.get("id") or "").strip()
        if not (ntype.startswith("aws_") or nid.startswith("aws:")):
            continue
        cat = _aws_category(ntype)
        aws_by_category[cat] += 1
        aws_by_service[ntype or "aws"] += 1

    # Observability slice -----------------------------------------------------
    log_templates = sum(1 for n in nodes if n.get("type") == "log_template")
    metrics = sum(1 for n in nodes if n.get("type") == "metric")
    trace_services = sum(1 for n in nodes if n.get("type") == "service")
    scrape_targets = sum(
        1 for n in nodes if n.get("type") == "scrape_target"
    )

    # Compliance slice --------------------------------------------------------
    comp_total = 0
    comp_severity: dict[str, int] = defaultdict(int)
    comp_rule: dict[str, int] = defaultdict(int)
    for n in nodes:
        if n.get("type") != "compliance_violation":
            continue
        if target_env is not None:
            e = n.get("env") or n.get("environment")
            if e and e != target_env:
                continue
        comp_total += 1
        comp_severity[str(n.get("severity") or "UNKNOWN").upper()] += 1
        rid = str(n.get("rule_id") or "")
        if rid:
            comp_rule[rid] += 1

    # Anomalies slice ---------------------------------------------------------
    anomaly_count = 0
    anomaly_by_layer: dict[str, int] = defaultdict(int)
    for n in nodes:
        flag = n.get("is_anomaly")
        truthy = (
            flag is True
            or (isinstance(flag, str) and flag.strip().lower() in {"true", "1", "yes"})
            or (isinstance(flag, (int, float)) and bool(flag))
        )
        if not truthy:
            continue
        anomaly_count += 1
        anomaly_by_layer[n.get("layer") or "unknown"] += 1

    # Graph health ------------------------------------------------------------
    populated_layers: set[str] = set()
    for n in nodes:
        layer = n.get("layer")
        if layer:
            populated_layers.add(str(layer))

    return {
        "env": target_env,
        "envs_detected": sorted(env_set),
        "iac": {
            "modules": modules,
            "components": components,
            "applications": applications,
        },
        "k8s": k8s_counts,
        "aws": {
            "by_category": dict(aws_by_category),
            "by_service": dict(aws_by_service),
            "total": sum(aws_by_category.values()),
        },
        "observability": {
            "log_templates": log_templates,
            "metrics": metrics,
            "traces_services": trace_services,
            "scrape_targets": scrape_targets,
        },
        "compliance": {
            "violations": comp_total,
            "by_severity": dict(comp_severity),
            "by_rule": dict(comp_rule),
        },
        "anomalies": {
            "is_anomaly_count": anomaly_count,
            "by_layer": dict(anomaly_by_layer),
        },
        "graph_health": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "layer_count": len(populated_layers),
            "populated_layers": sorted(populated_layers),
        },
    }


# ---------------------------------------------------------------------------
# 2. trace_data_flow
# ---------------------------------------------------------------------------


def _hop_relation(rx_graph: RxGraph, src: str, dst: str) -> str | None:
    """Return the relation for any single edge between src and dst.

    Walks both out- and in-edges so it works for paths that traversed an
    edge in either direction (the BFS underlying ``shortest_path`` is
    undirected).
    """
    for e in rx_graph.outgoing_edges(src):
        if e.get("target") == dst:
            return e.get("relation") or None
    for e in rx_graph.incoming_edges(src):
        if e.get("source") == dst:
            return f"<-{e.get('relation') or ''}"
    return None


@mcp.tool()
def trace_data_flow(
    from_id: str,
    to_id: str,
    max_hops: int = 8,
    persist_dir: str | None = None,
) -> dict:
    """Shortest path between two nodes with semantic hop annotations.

    Uses the cached RxGraph (process-wide, invalidated by ``cache_epoch``).
    Each hop is annotated with the relation that connected it to the
    previous node and the layer of the visited node. Capped at ``max_hops``;
    longer paths return ``{"found": false, "reason": "max_hops exceeded"}``.

    Also produces a Mermaid ``graph LR`` diagram suitable for an LLM agent
    or human reviewer to embed.
    """
    persist = Path(_resolve_persist(persist_dir)).resolve()
    store = open_store(persist)
    rx = RxGraph.cached_from_store(store, persist_dir=str(persist))

    if not rx.has_node(from_id):
        return {"found": False, "reason": f"unknown from_id: {from_id}"}
    if not rx.has_node(to_id):
        return {"found": False, "reason": f"unknown to_id: {to_id}"}

    path = rx.shortest_path(from_id, to_id)
    if not path:
        return {"found": False, "reason": "no path"}
    if len(path) - 1 > max_hops:
        return {
            "found": False,
            "reason": f"shortest path is {len(path) - 1} hops, exceeds max_hops={max_hops}",
            "actual_hops": len(path) - 1,
        }

    annotated: list[dict] = []
    prev: str | None = None
    for nid in path:
        node = rx.get_node(nid) or {"id": nid, "type": "unknown", "label": nid}
        rel = None
        if prev is not None:
            rel = _hop_relation(rx, prev, nid)
        annotated.append(
            {
                "node_id": nid,
                "type": node.get("type") or "",
                "layer": node.get("layer") or "",
                "label": node.get("label") or nid,
                "via_relation": rel,
            }
        )
        prev = nid

    # Mermaid LR.
    mermaid_lines = ["graph LR"]
    for hop in annotated:
        mid = _mermaid_safe_id(hop["node_id"])
        layer = hop["layer"] or "?"
        label = _mermaid_label({"label": hop["label"], "id": hop["node_id"]})
        mermaid_lines.append(f'  {mid}["{label}<br/>({layer})"]')
    for i in range(len(annotated) - 1):
        a = _mermaid_safe_id(annotated[i]["node_id"])
        b = _mermaid_safe_id(annotated[i + 1]["node_id"])
        rel = annotated[i + 1].get("via_relation") or ""
        if rel:
            mermaid_lines.append(f"  {a} -->|{rel}| {b}")
        else:
            mermaid_lines.append(f"  {a} --> {b}")

    return {
        "found": True,
        "from": from_id,
        "to": to_id,
        "path": annotated,
        "hop_count": len(path) - 1,
        "mermaid": "\n".join(mermaid_lines) + "\n",
    }


# ---------------------------------------------------------------------------
# 3. incident_context
# ---------------------------------------------------------------------------


@mcp.tool()
def incident_context(
    symptom: str,
    service: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Combine semantic search + observability anomaly probes into a single
    incident-triage report for an LLM agent to digest.

    Joins, in order:
      1. ``semantic_search(symptom, limit=10)`` — top hits across all layers
      2. ``find_log_anomalies(limit=10)`` — log-side anomalies
      3. ``find_high_cardinality_metrics(limit=10)`` — metric hygiene
      4. ``find_error_hotspots(limit=10)`` — trace-side
      5. When ``service`` is provided: ``service_one_pager(service)`` is
         embedded so the LLM gets the full per-service profile.

    All sub-calls soft-degrade — empty layers contribute ``[]`` instead of
    breaking the report.

    Returns a structured envelope with a curated ``recommended_next_steps``
    list seeded by which sub-signals fired.
    """
    persist = _resolve_persist(persist_dir)

    # Lazy imports to avoid the circular semantic ↔ super pull.
    from .semantic import semantic_search as _semantic_search
    from .analytics import (
        find_log_anomalies as _find_log_anomalies,
        find_high_cardinality_metrics as _find_high_cardinality_metrics,
        find_error_hotspots as _find_error_hotspots,
    )
    from .fusion import service_one_pager as _service_one_pager

    def _safe(call, label: str, default: Any) -> Any:
        try:
            return call()
        except Exception as exc:
            return {"_error": f"{label}: {exc}"}

    sem_hits = _safe(
        lambda: _semantic_search(query=symptom, limit=10, persist_dir=persist),
        "semantic_search",
        [],
    )
    log_anoms = _safe(
        lambda: _find_log_anomalies(limit=10, persist_dir=persist),
        "find_log_anomalies",
        {"anomalies": []},
    )
    high_card = _safe(
        lambda: _find_high_cardinality_metrics(limit=10, persist_dir=persist),
        "find_high_cardinality_metrics",
        {"metrics": []},
    )
    hotspots = _safe(
        lambda: _find_error_hotspots(limit=10, persist_dir=persist),
        "find_error_hotspots",
        [],
    )

    one_pager = None
    if service:
        one_pager = _safe(
            lambda: _service_one_pager(service=service, persist_dir=persist),
            "service_one_pager",
            None,
        )

    # Derive recommended next steps from which probes fired.
    next_steps: list[str] = []
    if isinstance(log_anoms, dict) and log_anoms.get("anomalies"):
        next_steps.append(
            "Inspect log_template anomalies (find_log_anomalies output) — "
            "likely error spike."
        )
    if isinstance(high_card, dict) and high_card.get("metrics"):
        next_steps.append(
            "Review high-cardinality metrics — possible cardinality "
            "explosion or scrape mis-config."
        )
    if isinstance(hotspots, list) and hotspots:
        next_steps.append(
            "Open trace hotspots (find_error_hotspots) — focus on the top "
            "service by error_rate * total_spans."
        )
    if isinstance(sem_hits, list) and sem_hits:
        next_steps.append(
            "Cross-reference semantic_search hits — they're ranked by "
            "similarity, not severity, so triage by node type."
        )
    if service and one_pager:
        next_steps.append(
            f"Pull service_one_pager('{service}') for the unified "
            "k8s/argo/logs/metrics/traces profile."
        )
    if not next_steps:
        next_steps.append(
            "No matching anomalies. Consider running regenerate_layer for "
            "logs/metrics/traces if data is stale."
        )

    return {
        "symptom": symptom,
        "service": service,
        "semantic_hits": sem_hits if isinstance(sem_hits, list) else [],
        "log_anomalies": (
            log_anoms.get("anomalies", [])
            if isinstance(log_anoms, dict)
            else []
        ),
        "metric_high_cardinality": (
            high_card.get("metrics", [])
            if isinstance(high_card, dict)
            else []
        ),
        "trace_hotspots": hotspots if isinstance(hotspots, list) else [],
        "service_profile": one_pager,
        "recommended_next_steps": next_steps,
    }


# ---------------------------------------------------------------------------
# 4. service_lineage
# ---------------------------------------------------------------------------


def _normalise_service_id(service: str) -> str:
    return service if service.startswith("service:") else f"service:{service}"


@mcp.tool()
def service_lineage(
    service: str,
    env: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Upstream/downstream lineage for ``service``.

    Joins:
      - Trace edges: ``service:* -calls-> service:<svc>`` (callers) and
        ``service:<svc> -calls-> service:*`` (callees), with call_count and
        error_rate.
      - K8s data deps: every ``Pod`` whose name contains the service name —
        their ``consumes_secret`` / ``consumes_configmap`` outgoing edges.
      - Network exposure: ``Service`` / ``Ingress`` resources that name the
        service; checks for ``via_ingress`` edges and LB host annotations.

    Returns a structured lineage block + a Mermaid ``graph LR`` diagram
    showing callers → service → callees.
    """
    store = open_store(Path(_resolve_persist(persist_dir)).resolve())
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}

    sid = _normalise_service_id(service)
    svc_node = nodes_by_id.get(sid)
    name_lower = service.lower().lstrip("service:")

    callers: list[dict] = []
    callees: list[dict] = []
    if svc_node:
        for e in edges:
            if e.get("relation") != "calls":
                continue
            if e.get("target") == sid:
                src = e.get("source") or ""
                callers.append(
                    {
                        "from": src,
                        "label": (nodes_by_id.get(src) or {}).get("label") or src,
                        "call_count": int(e.get("call_count") or 0),
                        "error_rate": float(e.get("error_rate") or 0.0),
                    }
                )
            elif e.get("source") == sid:
                tgt = e.get("target") or ""
                callees.append(
                    {
                        "to": tgt,
                        "label": (nodes_by_id.get(tgt) or {}).get("label") or tgt,
                        "call_count": int(e.get("call_count") or 0),
                        "error_rate": float(e.get("error_rate") or 0.0),
                    }
                )
        callers.sort(key=lambda r: -r["call_count"])
        callees.sort(key=lambda r: -r["call_count"])

    # Data deps from k8s pods whose name matches the service.
    matched_pods: list[dict] = []
    consumes_secret: list[dict] = []
    consumes_configmap: list[dict] = []
    for n in nodes:
        if n.get("type") != "k8s_resource":
            continue
        if (n.get("kind") or "").lower() != "pod":
            continue
        pname = (n.get("name") or n.get("label") or "").lower()
        if name_lower not in pname:
            continue
        if env:
            n_env = n.get("env") or n.get("environment") or n.get("namespace")
            if n_env and n_env != env:
                continue
        matched_pods.append(
            {"id": n.get("id"), "namespace": n.get("namespace"), "name": n.get("name")}
        )
    pod_ids = {p["id"] for p in matched_pods if p.get("id")}
    for e in edges:
        rel = e.get("relation")
        if e.get("source") not in pod_ids:
            continue
        if rel == "consumes_secret":
            consumes_secret.append(
                {"from": e.get("source"), "to": e.get("target")}
            )
        elif rel == "consumes_configmap":
            consumes_configmap.append(
                {"from": e.get("source"), "to": e.get("target")}
            )

    # Network exposure: matching k8s Service / Ingress.
    exposures: list[dict] = []
    for n in nodes:
        if n.get("type") != "k8s_resource":
            continue
        kind = (n.get("kind") or "").lower()
        if kind not in {"service", "ingress"}:
            continue
        nname = (n.get("name") or n.get("label") or "").lower()
        if name_lower not in nname:
            continue
        if env:
            n_env = n.get("env") or n.get("environment") or n.get("namespace")
            if n_env and n_env != env:
                continue
        exposures.append(
            {
                "id": n.get("id"),
                "kind": n.get("kind"),
                "namespace": n.get("namespace"),
                "name": n.get("name"),
                "service_type": n.get("service_type"),
                "hostname": n.get("hostname") or n.get("host"),
                "is_public": bool(n.get("is_public")),
            }
        )

    # Mermaid: callers -> service -> callees.
    mermaid_lines = ["graph LR"]
    if svc_node:
        center = _mermaid_safe_id(sid)
        mermaid_lines.append(f'  {center}["{_mermaid_label(svc_node)}<br/>(service)"]')
        for c in callers[:8]:
            cid = _mermaid_safe_id(c["from"])
            mermaid_lines.append(f'  {cid}["{c["label"]}"] -->|calls| {center}')
        for c in callees[:8]:
            cid = _mermaid_safe_id(c["to"])
            mermaid_lines.append(f'  {center} -->|calls| {cid}["{c["label"]}"]')
    else:
        mermaid_lines.append(
            f'  empty["No service node for \'{service}\'"]'
        )

    return {
        "service": service,
        "service_node": svc_node,
        "env": env,
        "upstream_callers": callers,
        "downstream_callees": callees,
        "matched_pods": matched_pods,
        "consumes_secret": consumes_secret,
        "consumes_configmap": consumes_configmap,
        "network_exposure": exposures,
        "mermaid": "\n".join(mermaid_lines) + "\n",
    }


# ---------------------------------------------------------------------------
# 5. node_explain
# ---------------------------------------------------------------------------


_DERIVATION_RELATIONS_PRIORITY = (
    "owned_by",
    "in_cluster",
    "in_vpc",
    "declared_in",
    "in_subnet",
    "in_repo",
    "configures_module",
    "renders",
    "tracks",
    "uses",
    "depends_on",
)


@mcp.tool()
def node_explain(
    node_id: str,
    persist_dir: str | None = None,
) -> dict:
    """Self-contained explanation of a single node.

    Returns:
      - ``node`` / ``metadata``: full payload from the store
      - ``neighbors``: in/out neighbours grouped by relation, capped at 50
        nodes total (3-hop BFS via the cached RxGraph)
      - ``derivation``: ancestor chain produced by walking inbound edges
        following a fixed relation priority (``owned_by`` →
        ``in_cluster`` → ``declared_in`` → ``in_repo`` → …); stops at the
        first node with no further relevant inbound edge.
      - ``mermaid``: Mermaid diagram of the derivation chain
    """
    persist = Path(_resolve_persist(persist_dir)).resolve()
    store = open_store(persist)
    rx = RxGraph.cached_from_store(store, persist_dir=str(persist))
    node = rx.get_node(node_id)
    if not node:
        return {"error": f"unknown node: {node_id}"}

    # Neighbours by relation (1-hop).
    neighbors_by_rel: dict[str, list[dict]] = defaultdict(list)
    seen_n: set[str] = set()
    for e in rx.outgoing_edges(node_id):
        rel = f"out:{e.get('relation') or 'unknown'}"
        tgt = e.get("target") or ""
        if not tgt or tgt in seen_n:
            continue
        seen_n.add(tgt)
        tnode = rx.get_node(tgt) or {"id": tgt}
        neighbors_by_rel[rel].append(
            {
                "id": tgt,
                "type": tnode.get("type"),
                "layer": tnode.get("layer"),
                "label": tnode.get("label") or tgt,
            }
        )
        if len(seen_n) >= 50:
            break
    for e in rx.incoming_edges(node_id):
        rel = f"in:{e.get('relation') or 'unknown'}"
        src = e.get("source") or ""
        if not src or src in seen_n:
            continue
        seen_n.add(src)
        snode = rx.get_node(src) or {"id": src}
        neighbors_by_rel[rel].append(
            {
                "id": src,
                "type": snode.get("type"),
                "layer": snode.get("layer"),
                "label": snode.get("label") or src,
            }
        )
        if len(seen_n) >= 50:
            break

    # Derivation: walk inbound edges by relation priority.
    derivation: list[dict] = []
    visited: set[str] = {node_id}
    cursor = node_id
    derivation.append(
        {
            "node_id": node_id,
            "type": (rx.get_node(cursor) or {}).get("type"),
            "layer": (rx.get_node(cursor) or {}).get("layer"),
            "via": None,
        }
    )
    for _ in range(15):  # safety cap
        in_edges = rx.incoming_edges(cursor)
        if not in_edges:
            break
        # Pick the first edge whose relation appears in the priority list.
        chosen: dict | None = None
        for rel in _DERIVATION_RELATIONS_PRIORITY:
            for e in in_edges:
                if e.get("relation") == rel and e.get("source") not in visited:
                    chosen = e
                    break
            if chosen:
                break
        # Fallback: any first inbound edge to a not-yet-visited node.
        if chosen is None:
            for e in in_edges:
                if e.get("source") not in visited:
                    chosen = e
                    break
        if chosen is None:
            break
        src = chosen.get("source") or ""
        if not src or src in visited:
            break
        visited.add(src)
        snode = rx.get_node(src) or {"id": src}
        derivation.append(
            {
                "node_id": src,
                "type": snode.get("type"),
                "layer": snode.get("layer"),
                "via": chosen.get("relation"),
            }
        )
        cursor = src

    # Mermaid: derivation chain.
    mermaid_lines = ["graph TD"]
    for hop in derivation:
        mid = _mermaid_safe_id(hop["node_id"])
        node_pl = rx.get_node(hop["node_id"]) or {"id": hop["node_id"]}
        layer = hop.get("layer") or "?"
        label = _mermaid_label({"label": node_pl.get("label"), "id": hop["node_id"]})
        mermaid_lines.append(f'  {mid}["{label}<br/>({layer})"]')
    # Reverse edges so the diagram reads root → leaf.
    for i in range(len(derivation) - 1):
        leaf = derivation[i]
        root = derivation[i + 1]
        a = _mermaid_safe_id(root["node_id"])
        b = _mermaid_safe_id(leaf["node_id"])
        rel = leaf.get("via") or ""
        if rel:
            mermaid_lines.append(f"  {a} -->|{rel}| {b}")
        else:
            mermaid_lines.append(f"  {a} --> {b}")

    return {
        "node": {
            "id": node_id,
            "type": node.get("type"),
            "layer": node.get("layer"),
            "label": node.get("label"),
        },
        "metadata": dict(node),
        "neighbors": dict(neighbors_by_rel),
        "neighbor_count": len(seen_n),
        "derivation": derivation,
        "mermaid": "\n".join(mermaid_lines) + "\n",
    }
