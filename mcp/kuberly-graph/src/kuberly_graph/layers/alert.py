"""AlertLayer — extract Prometheus alert rules + ServiceMonitor scrape edges.

Runs AFTER ``k8s`` and ``metrics``, BEFORE ``dependency``. Pure structural
extractor over k8s_resource nodes (PrometheusRule + ServiceMonitor CRDs)
already in the GraphStore. Empty-store tolerant.

Nodes:
  * ``alert_rule:<ns>/<rule-name>/<group>/<alert>``

Edges:
  * alert_rule → metric:<name>     (``uses_metric``)
                                       — extracted from PromQL ``expr``
  * k8s_resource(ServiceMonitor) → k8s_resource(Service) (``scrapes``)
  * alert_rule → application:<env>/<app>  (``monitors``)
                                       — best-effort label match
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import Layer


# A reasonable PromQL identifier regex — drops keywords/aggregations.
_METRIC_NAME_RE = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]*)\b")
_PROMQL_KEYWORDS = {
    "by",
    "without",
    "on",
    "ignoring",
    "group_left",
    "group_right",
    "and",
    "or",
    "unless",
    "bool",
    "offset",
    "if",
    "else",
    "vector",
    "scalar",
    "time",
    "rate",
    "irate",
    "increase",
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "stddev",
    "stdvar",
    "topk",
    "bottomk",
    "quantile",
    "histogram_quantile",
    "label_replace",
    "absent",
    "absent_over_time",
    "predict_linear",
    "deriv",
    "delta",
    "idelta",
    "changes",
    "resets",
    "abs",
    "ceil",
    "floor",
    "round",
    "exp",
    "ln",
    "log2",
    "log10",
    "sqrt",
    "clamp",
    "clamp_max",
    "clamp_min",
    "pi",
    "year",
    "month",
    "day_of_week",
    "day_of_month",
    "days_in_month",
    "hour",
    "minute",
    "timestamp",
    "le",
    "ge",
    "eq",
    "ne",
    "gt",
    "lt",
    "Inf",
    "NaN",
    "nan",
    "true",
    "false",
}


def _extract_metric_names(expr: str) -> list[str]:
    """Return distinct metric-like identifiers from a PromQL expression."""
    if not expr or not isinstance(expr, str):
        return []
    seen: set[str] = set()
    out: list[str] = []
    # Strip string literals so we don't pull names out of label-match values.
    cleaned = re.sub(r'"[^"]*"', "", expr)
    cleaned = re.sub(r"'[^']*'", "", cleaned)
    for tok in _METRIC_NAME_RE.findall(cleaned):
        if tok in _PROMQL_KEYWORDS:
            continue
        if tok.isdigit():
            continue
        # PromQL number suffixes etc — keep simple identifier patterns.
        if tok in seen:
            continue
        # Skip pure label keys like "severity", "summary", "for".
        seen.add(tok)
        out.append(tok)
    return out


def _flatten_prom_rule_groups(spec: dict) -> list[tuple[str, dict]]:
    """Yield (group_name, rule_dict) from a PrometheusRule.spec."""
    out: list[tuple[str, dict]] = []
    if not isinstance(spec, dict):
        return out
    groups = spec.get("groups")
    if not isinstance(groups, list):
        return out
    for g in groups:
        if not isinstance(g, dict):
            continue
        gname = str(g.get("name") or "")
        rules = g.get("rules")
        if not isinstance(rules, list):
            continue
        for r in rules:
            if isinstance(r, dict):
                out.append((gname, r))
    return out


class AlertLayer(Layer):
    name = "alert"
    refresh_trigger = "manual"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        verbose = bool(ctx.get("verbose"))
        store = ctx.get("graph_store")
        if store is None:
            from ..store import open_store

            persist_dir = ctx.get("persist_dir") or str(
                Path(ctx.get("repo_root", ".")) / ".kuberly"
            )
            store = open_store(Path(persist_dir))

        try:
            k8s_nodes = store.all_nodes(layer="k8s")
        except Exception:
            k8s_nodes = []
        try:
            all_nodes = store.all_nodes()
        except Exception:
            all_nodes = []

        if not k8s_nodes and not all_nodes:
            if verbose:
                print("  [AlertLayer] empty store — emitting 0 nodes")
            return [], []

        prom_rules = [n for n in k8s_nodes if n.get("kind") == "PrometheusRule"]
        service_monitors = [n for n in k8s_nodes if n.get("kind") == "ServiceMonitor"]
        services = [n for n in k8s_nodes if n.get("kind") == "Service"]
        services_by_label: dict[tuple[str, str, str], list[dict]] = {}
        for svc in services:
            ns = str(svc.get("namespace") or "")
            labels = svc.get("labels") if isinstance(svc.get("labels"), dict) else {}
            for k, v in (labels or {}).items():
                services_by_label.setdefault((ns, str(k), str(v)), []).append(svc)

        metric_ids: set[str] = set()
        applications_by_name: dict[str, list[dict]] = {}
        for n in all_nodes:
            t = n.get("type")
            if t == "metric":
                nid = str(n.get("id") or "")
                if nid:
                    metric_ids.add(nid)
            elif t == "application":
                # application:<env>/<app>
                nid = str(n.get("id") or "")
                try:
                    _, body = nid.split(":", 1)
                    _env, app_name = body.split("/", 1)
                except ValueError:
                    continue
                applications_by_name.setdefault(app_name, []).append(n)

        nodes: list[dict] = []
        edges: list[dict] = []
        emitted_ids: set[str] = set()

        def _emit_node(node: dict) -> None:
            if node["id"] in emitted_ids:
                return
            emitted_ids.add(node["id"])
            nodes.append(node)

        def _emit_edge(source: str, target: str, relation: str, **extra) -> None:
            if not source or not target:
                return
            edge = {"source": source, "target": target, "relation": relation}
            edge.update(extra)
            edges.append(edge)

        # ---- alert_rule nodes + uses_metric / monitors edges ------------------
        for pr in prom_rules:
            ns = str(pr.get("namespace") or "")
            spec = pr.get("spec") if isinstance(pr.get("spec"), dict) else {}
            for gname, rule in _flatten_prom_rule_groups(spec):
                alert_name = str(rule.get("alert") or "")
                if not alert_name:
                    continue
                expr = str(rule.get("expr") or "")
                for_dur = str(rule.get("for") or "")
                labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
                annotations = (
                    rule.get("annotations") if isinstance(rule.get("annotations"), dict) else {}
                )
                rule_id = f"alert_rule:{ns}/{pr.get('name', '')}/{gname}/{alert_name}"
                _emit_node(
                    {
                        "id": rule_id,
                        "type": "alert_rule",
                        "label": alert_name,
                        "namespace": ns,
                        "group": gname,
                        "rule_name": str(pr.get("name") or ""),
                        "alert": alert_name,
                        "expr": expr,
                        "for": for_dur,
                        "severity": str(labels.get("severity") or ""),
                        "summary": str(annotations.get("summary") or ""),
                        "description": str(annotations.get("description") or ""),
                        "runbook_url": str(annotations.get("runbook_url") or ""),
                    }
                )
                # uses_metric edges via PromQL parse.
                metric_names = _extract_metric_names(expr)
                for m in metric_names:
                    target = f"metric:{m}"
                    if target in metric_ids:
                        _emit_edge(rule_id, target, "uses_metric")
                # monitors application by label match.
                app_label = str(labels.get("app") or labels.get("service") or "")
                if app_label:
                    for app in applications_by_name.get(app_label, []):
                        _emit_edge(rule_id, app["id"], "monitors")

        # ---- ServiceMonitor → Service via spec.selector.matchLabels -----------
        for sm in service_monitors:
            ns = str(sm.get("namespace") or "")
            spec = sm.get("spec") if isinstance(sm.get("spec"), dict) else {}
            sel = spec.get("selector")
            if not isinstance(sel, dict):
                continue
            match = sel.get("matchLabels")
            if not isinstance(match, dict) or not match:
                continue
            # AND of all labels — start with first and intersect.
            candidate_set: set[str] | None = None
            for k, v in match.items():
                bucket = services_by_label.get((ns, str(k), str(v)), [])
                ids = {svc["id"] for svc in bucket}
                if candidate_set is None:
                    candidate_set = ids
                else:
                    candidate_set &= ids
                if not candidate_set:
                    break
            for svc_id in candidate_set or set():
                _emit_edge(sm["id"], svc_id, "scrapes")

        if verbose:
            print(
                f"  [AlertLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges
