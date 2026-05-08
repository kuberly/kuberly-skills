"""Phase 7D infra tools — pure GraphStore queries over DNS / Secrets / Cost /
Alert / Compliance layers. No live API calls.

10 new MCP tools. Each is empty-store tolerant: an empty graph yields ``[]``
or ``{}`` rather than crashing.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..server import SERVER_CONFIG, mcp
from ..store import open_store


def _resolve_persist(persist_dir: str | None) -> str:
    return persist_dir or SERVER_CONFIG.get("persist_dir", ".kuberly")


def _open(persist_dir: str | None):
    return open_store(Path(_resolve_persist(persist_dir)).resolve())


# ---- DNS --------------------------------------------------------------------


@mcp.tool()
def find_dns_dangling_records(persist_dir: str | None = None) -> list[dict]:
    """``dns_record`` nodes with no outgoing ``points_to`` edge.

    Useful for finding stale Route53 records whose alias targets no longer
    map onto an Ingress / Service in the live cluster.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    pointing: set[str] = set()
    for e in edges:
        if e.get("relation") == "points_to":
            src = str(e.get("source") or "")
            if src.startswith("dns_record:"):
                pointing.add(src)
    out: list[dict] = []
    for n in nodes:
        if n.get("type") != "dns_record":
            continue
        nid = str(n.get("id") or "")
        if nid in pointing:
            continue
        out.append(
            {
                "id": nid,
                "env": n.get("env", ""),
                "name": n.get("name", ""),
                "record_type": n.get("record_type", ""),
                "alias_target": n.get("alias_target", ""),
                "ttl": n.get("ttl", 0),
            }
        )
    return out


@mcp.tool()
def service_dns_chain(
    service_id: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """Walk Pod → Service → Ingress → DNS record path for ``service_id``.

    ``service_id`` is the full ``k8s_resource:<ns>/Service/<name>`` id. Returns
    a list of edge segments [{from, to, relation, label}] in walk order.
    """
    if not service_id:
        return []
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    if service_id not in nodes_by_id:
        return []
    out: list[dict] = []
    # Pods that "traced_as" the service.
    for e in edges:
        if e.get("relation") == "traced_as" and str(e.get("target") or "") == service_id:
            src = str(e.get("source") or "")
            out.append(
                {
                    "from": src,
                    "to": service_id,
                    "relation": "traced_as",
                    "label": (nodes_by_id.get(src, {}) or {}).get("label", src),
                }
            )
    # Ingresses that scrape the service via a ServiceMonitor (rare) — but more
    # importantly Ingresses whose backend points at this service. We use the
    # alert-side `scrapes` edge as a fallback.
    ingress_ids: set[str] = set()
    for n in nodes_by_id.values():
        if n.get("kind") != "Ingress":
            continue
        spec = n.get("spec") if isinstance(n.get("spec"), dict) else {}
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            http = r.get("http") if isinstance(r.get("http"), dict) else {}
            paths = http.get("paths") if isinstance(http.get("paths"), list) else []
            for p in paths or []:
                if not isinstance(p, dict):
                    continue
                back = p.get("backend") if isinstance(p.get("backend"), dict) else {}
                svc = back.get("service") if isinstance(back.get("service"), dict) else {}
                if isinstance(svc, dict) and svc.get("name") == nodes_by_id[service_id].get("name"):
                    ingress_ids.add(str(n.get("id") or ""))
                    out.append(
                        {
                            "from": service_id,
                            "to": str(n.get("id") or ""),
                            "relation": "exposed_by",
                            "label": n.get("label", ""),
                        }
                    )
    # dns_record → ingress (points_to)
    for e in edges:
        if e.get("relation") != "points_to":
            continue
        if str(e.get("target") or "") in ingress_ids:
            out.append(
                {
                    "from": str(e.get("source") or ""),
                    "to": str(e.get("target") or ""),
                    "relation": "points_to",
                    "label": (nodes_by_id.get(str(e.get("source") or ""), {}) or {}).get(
                        "label", ""
                    ),
                }
            )
    return out


# ---- Secrets ----------------------------------------------------------------


@mcp.tool()
def find_secret_consumers(
    secret_id: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """Pods / workloads that consume the given k8s Secret via envFrom / volumes.

    ``secret_id`` is the full ``k8s_resource:<ns>/Secret/<name>`` id.
    """
    if not secret_id:
        return []
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    out: list[dict] = []
    seen: set[str] = set()
    for e in edges:
        if e.get("relation") != "consumes_secret":
            continue
        if str(e.get("target") or "") != secret_id:
            continue
        src = str(e.get("source") or "")
        if src in seen:
            continue
        seen.add(src)
        n = nodes_by_id.get(src, {"id": src})
        out.append(
            {
                "id": src,
                "kind": n.get("kind", ""),
                "namespace": n.get("namespace", ""),
                "name": n.get("name", ""),
            }
        )
    return out


@mcp.tool()
def find_unused_secrets(persist_dir: str | None = None) -> list[dict]:
    """k8s Secrets with no incoming ``consumes_secret`` or ``creates`` edge.

    A Secret without consumers is potentially stale. We treat ``creates`` as a
    "still owned by an ExternalSecret" signal so freshly-rotated targets aren't
    flagged.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    targets: set[str] = set()
    for e in edges:
        rel = e.get("relation")
        if rel in {"consumes_secret", "creates"}:
            tgt = str(e.get("target") or "")
            if tgt:
                targets.add(tgt)
    out: list[dict] = []
    for n in nodes:
        if n.get("kind") != "Secret":
            continue
        nid = str(n.get("id") or "")
        if nid in targets:
            continue
        out.append(
            {
                "id": nid,
                "namespace": n.get("namespace", ""),
                "name": n.get("name", ""),
            }
        )
    return out


@mcp.tool()
def external_secret_chain(
    external_secret_id: str,
    persist_dir: str | None = None,
) -> dict:
    """ExternalSecret → SecretStore → AWS secret/SSM chain.

    ``external_secret_id`` is the full
    ``k8s_resource:<ns>/ExternalSecret/<name>`` node id.
    """
    if not external_secret_id:
        return {"found": False, "reason": "external_secret_id required"}
    store = _open(persist_dir)
    nodes_by_id = {n.get("id"): n for n in store.all_nodes() if n.get("id")}
    edges = store.all_edges()
    es_node = nodes_by_id.get(external_secret_id)
    if es_node is None:
        return {"found": False, "reason": "ExternalSecret not in graph"}
    secret_id = ""
    store_id = ""
    for e in edges:
        s = str(e.get("source") or "")
        if s != external_secret_id:
            continue
        rel = str(e.get("relation") or "")
        if rel == "creates":
            secret_id = str(e.get("target") or "")
        elif rel == "uses_store":
            store_id = str(e.get("target") or "")
    aws_targets: list[dict] = []
    if store_id:
        for e in edges:
            if e.get("relation") != "pulls_from":
                continue
            if str(e.get("source") or "") != store_id:
                continue
            tgt = str(e.get("target") or "")
            n = nodes_by_id.get(tgt, {"id": tgt})
            aws_targets.append(
                {
                    "id": tgt,
                    "type": n.get("type", ""),
                    "name": n.get("name", ""),
                    "key": e.get("key", ""),
                }
            )
    return {
        "found": True,
        "external_secret": {
            "id": external_secret_id,
            "namespace": es_node.get("namespace", ""),
            "name": es_node.get("name", ""),
        },
        "secret": (
            {
                "id": secret_id,
                "namespace": (nodes_by_id.get(secret_id) or {}).get("namespace", ""),
                "name": (nodes_by_id.get(secret_id) or {}).get("name", ""),
            }
            if secret_id
            else None
        ),
        "store": (
            {
                "id": store_id,
                "kind": (nodes_by_id.get(store_id) or {}).get("kind", ""),
                "name": (nodes_by_id.get(store_id) or {}).get("name", ""),
            }
            if store_id
            else None
        ),
        "aws_targets": aws_targets,
    }


# ---- Cost -------------------------------------------------------------------


@mcp.tool()
def cost_summary(
    account_id: str | None = None,
    months: int = 3,
    persist_dir: str | None = None,
) -> dict:
    """Roll up ``cost_period`` / ``cost_service`` nodes into a summary.

    Pure GraphStore query — no live AWS call. Returns ``{}`` when the cost
    layer hasn't been populated (boto3 missing or creds absent).
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    periods = [n for n in nodes if n.get("type") == "cost_period"]
    services = [n for n in nodes if n.get("type") == "cost_service"]
    if account_id:
        periods = [n for n in periods if n.get("account_id") == account_id]
        services = [n for n in services if n.get("account_id") == account_id]
    if months and months > 0:
        # Keep the most recent `months` distinct months.
        sorted_months = sorted({str(n.get("month") or "") for n in periods}, reverse=True)
        keep = set(sorted_months[: int(months)])
        periods = [n for n in periods if str(n.get("month") or "") in keep]
        services = [n for n in services if str(n.get("month") or "") in keep]

    by_month: dict[str, dict] = {}
    for p in periods:
        m = str(p.get("month") or "")
        by_month.setdefault(m, {"month": m, "total_usd": 0.0, "services": []})
        by_month[m]["total_usd"] = float(p.get("total_usd") or 0.0)
        by_month[m]["currency"] = p.get("currency", "USD")
        by_month[m]["account_id"] = p.get("account_id", "")
    for s in services:
        m = str(s.get("month") or "")
        by_month.setdefault(m, {"month": m, "total_usd": 0.0, "services": []})
        by_month[m]["services"].append(
            {"service": s.get("service", ""), "usd": float(s.get("usd") or 0.0)}
        )
    months_sorted = sorted(by_month.values(), key=lambda x: x.get("month", ""))
    return {
        "account_id": account_id or (months_sorted[-1]["account_id"] if months_sorted else ""),
        "months": months_sorted,
        "available_months": sorted({str(p.get("month") or "") for p in periods}),
    }


# ---- Alert ------------------------------------------------------------------


@mcp.tool()
def find_orphan_alerts(persist_dir: str | None = None) -> list[dict]:
    """Alert rules whose ``uses_metric`` targets aren't in the graph.

    Likely causes: renamed / dropped metrics, alerts that haven't been re-
    aligned with the current scrape config. An alert with zero ``uses_metric``
    edges is also surfaced — we can't tell whether the expr genuinely doesn't
    reference any metric or whether the parser missed it; the caller decides.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    metric_ids = {str(n.get("id") or "") for n in nodes if n.get("type") == "metric"}
    out: list[dict] = []
    by_alert: dict[str, list[str]] = defaultdict(list)
    has_metric_edge: set[str] = set()
    for e in edges:
        if e.get("relation") != "uses_metric":
            continue
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        has_metric_edge.add(src)
        if tgt not in metric_ids:
            by_alert[src].append(tgt)
    for n in nodes:
        if n.get("type") != "alert_rule":
            continue
        nid = str(n.get("id") or "")
        missing = by_alert.get(nid, [])
        if missing or nid not in has_metric_edge:
            out.append(
                {
                    "id": nid,
                    "alert": n.get("alert", ""),
                    "namespace": n.get("namespace", ""),
                    "expr": n.get("expr", ""),
                    "missing_metrics": missing,
                    "no_uses_metric_edge": nid not in has_metric_edge,
                    "severity": n.get("severity", ""),
                }
            )
    return out


@mcp.tool()
def service_alert_summary(
    service: str,
    env: str | None = None,
    persist_dir: str | None = None,
) -> list[dict]:
    """Alerts that monitor the given application/service name.

    Match precedence:
      1. ``alert_rule → application:<env>/<service>`` (``monitors`` edge).
      2. ``alert_rule.expr`` mentions a metric whose name contains ``service``.
    """
    if not service:
        return []
    store = _open(persist_dir)
    nodes = store.all_nodes()
    edges = store.all_edges()
    nodes_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    targets: set[str] = set()
    for e in edges:
        if e.get("relation") != "monitors":
            continue
        tgt = str(e.get("target") or "")
        if not tgt.startswith("application:"):
            continue
        try:
            _, body = tgt.split(":", 1)
            t_env, t_app = body.split("/", 1)
        except ValueError:
            continue
        if env and t_env != env:
            continue
        if t_app == service:
            targets.add(str(e.get("source") or ""))
    # heuristic: alert rules whose expr mentions the service name.
    sub = service.lower()
    for n in nodes:
        if n.get("type") != "alert_rule":
            continue
        expr = str(n.get("expr") or "").lower()
        if sub in expr:
            targets.add(str(n.get("id") or ""))
    out: list[dict] = []
    for tid in sorted(targets):
        n = nodes_by_id.get(tid, {"id": tid})
        out.append(
            {
                "id": tid,
                "alert": n.get("alert", ""),
                "namespace": n.get("namespace", ""),
                "severity": n.get("severity", ""),
                "expr": n.get("expr", ""),
            }
        )
    return out


# ---- Compliance --------------------------------------------------------------


@mcp.tool()
def compliance_report(
    severity: str | None = None,
    rule_id: str | None = None,
    persist_dir: str | None = None,
) -> dict:
    """Summary of compliance violations: total, per-rule, per-resource.

    Optional filters:
      * ``severity`` — HIGH / MEDIUM / LOW (case-insensitive).
      * ``rule_id`` — exact match against R001..R007.
    """
    store = _open(persist_dir)
    nodes = store.all_nodes()
    sev_filter = severity.upper() if severity else None
    rules: dict[str, list[dict]] = defaultdict(list)
    per_resource: dict[str, list[dict]] = defaultdict(list)
    total = 0
    for n in nodes:
        if n.get("type") != "compliance_violation":
            continue
        rid = str(n.get("rule_id") or "")
        if rule_id and rid != rule_id:
            continue
        sev = str(n.get("severity") or "")
        if sev_filter and sev != sev_filter:
            continue
        total += 1
        rules[rid].append(
            {
                "id": str(n.get("id") or ""),
                "resource_id": str(n.get("resource_id") or ""),
                "severity": sev,
                "summary": n.get("description", ""),
            }
        )
        per_resource[str(n.get("resource_id") or "")].append(
            {"id": str(n.get("id") or ""), "rule_id": rid, "severity": sev}
        )
    return {
        "total": total,
        "filter": {"severity": severity, "rule_id": rule_id},
        "by_rule": {
            rid: {"count": len(rows), "items": rows} for rid, rows in sorted(rules.items())
        },
        "by_resource": {
            res_id: {"count": len(rows), "violations": rows}
            for res_id, rows in sorted(per_resource.items())
        },
    }


@mcp.tool()
def find_violations_for_resource(
    resource_id: str,
    persist_dir: str | None = None,
) -> list[dict]:
    """All ``compliance_violation`` nodes targeting the given resource id."""
    if not resource_id:
        return []
    store = _open(persist_dir)
    nodes = store.all_nodes()
    out: list[dict] = []
    for n in nodes:
        if n.get("type") != "compliance_violation":
            continue
        if str(n.get("resource_id") or "") != resource_id:
            continue
        out.append(
            {
                "id": str(n.get("id") or ""),
                "rule_id": n.get("rule_id", ""),
                "severity": n.get("severity", ""),
                "description": n.get("description", ""),
                "recommendation": n.get("recommendation", ""),
            }
        )
    return out
