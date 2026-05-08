"""MetricsLayer — Prometheus metric metadata + scrape topology.

v0.51.0: kubectl port-forward fallback is now the **default** path when the
upstream MCP wrapper has no Prom URL wired (Phase 8H landed it as opt-in
which produced 0 nodes on the live cluster). Order of operations:

  1. If ``mcp_endpoint`` is set, try ``query_metrics`` + ``prom_get_targets``.
  2. If those return ``isError`` / ``None`` / 0 series AND
     ``metrics_use_kubectl_pf`` is not explicitly false, spin up a
     ``kubectl port-forward`` to ``svc/prometheus-kube-prometheus-prometheus``
     and scrape ``/api/v1/label/__name__/values`` + ``/api/v1/metadata`` +
     ``/api/v1/targets`` directly.
  3. Subprocess cleanup is owned by ``layers/_pf.py`` (try/finally + drain).

Soft-degrade: if kubectl is missing OR no current-context OR PF fails to
bind, log a single warning and return the existing-MCP result (which may be
empty).
"""

from __future__ import annotations

import datetime as _dt
import sys
from collections import defaultdict

from .base import Layer
from ._pf import (
    _have_current_context,
    _have_kubectl,
    http_get_json_qs,
    kubectl_port_forward,
    warn,
)


def _extract_metric_names(payload) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if payload is None:
        return out
    data = payload
    if isinstance(payload, dict):
        data = payload.get("data", payload)
    results = []
    if isinstance(data, dict):
        results = data.get("result") or data.get("results") or []
    elif isinstance(data, list):
        results = data
    for entry in results or []:
        if not isinstance(entry, dict):
            continue
        labels = entry.get("metric") or entry.get("labels") or {}
        if not isinstance(labels, dict):
            continue
        name = labels.get("__name__") or labels.get("name") or ""
        if not name:
            continue
        count_val = 0
        val = entry.get("value")
        if isinstance(val, list) and len(val) >= 2:
            try:
                count_val = int(float(val[1]))
            except Exception:
                count_val = 0
        if not count_val:
            for key in ("count", "series", "cardinality"):
                v = entry.get(key)
                if isinstance(v, (int, float)):
                    count_val = int(v)
                    break
        out.append((str(name), int(count_val) or 1))
    return out


def _extract_metric_metadata(payload) -> dict:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                first = v[0]
                if "type" in first or "help" in first:
                    return {
                        "metric_type": str(first.get("type") or "unknown"),
                        "help": str(first.get("help") or "")[:200],
                    }
        if "type" in data or "help" in data:
            return {
                "metric_type": str(data.get("type") or "unknown"),
                "help": str(data.get("help") or "")[:200],
            }
    return {}


def _extract_targets(payload) -> list[dict]:
    out: list[dict] = []
    if payload is None:
        return out
    data = payload
    if isinstance(payload, dict):
        data = payload.get("data", payload)
    targets = []
    if isinstance(data, dict):
        targets = (
            data.get("activeTargets")
            or data.get("active_targets")
            or data.get("targets")
            or []
        )
    elif isinstance(data, list):
        targets = data
    for t in targets or []:
        if not isinstance(t, dict):
            continue
        labels = t.get("labels") or t.get("discoveredLabels") or {}
        if not isinstance(labels, dict):
            labels = {}
        job = labels.get("job") or t.get("job") or ""
        instance = labels.get("instance") or t.get("instance") or ""
        if not job or not instance:
            continue
        ns = labels.get("namespace") or labels.get("kubernetes_namespace") or ""
        pod = labels.get("pod") or labels.get("kubernetes_pod_name") or ""
        out.append(
            {
                "job": str(job),
                "instance": str(instance),
                "namespace": str(ns),
                "pod": str(pod),
                "health": str(t.get("health") or "unknown"),
                "last_scrape": str(
                    t.get("lastScrape") or t.get("last_scrape") or ""
                ),
                "labels": {
                    k: str(v)
                    for k, v in labels.items()
                    if isinstance(v, (str, int, float, bool))
                },
            }
        )
    return out


def _match_module_for_job(job: str, existing_module_ids: set[str]) -> str:
    if not job or not existing_module_ids:
        return ""
    job_lower = job.lower()
    candidates: list[str] = []
    for mid in existing_module_ids:
        try:
            _, body = mid.split(":", 1)
            _provider, name = body.split("/", 1)
        except ValueError:
            continue
        if not name:
            continue
        nl = name.lower()
        if (
            nl == job_lower
            or job_lower.startswith(nl + "-")
            or (f"-{nl}-" in job_lower or job_lower.endswith(f"-{nl}"))
        ):
            candidates.append(mid)
    return candidates[0] if len(candidates) == 1 else ""


def _scrape_prom_via_pf(
    *,
    namespace: str,
    service: str,
    port: int,
    top_n: int,
    verbose: bool,
) -> tuple[list[tuple[str, int]], dict[str, dict], list[dict]]:
    """Spin up kubectl-pf to Prometheus and pull names + metadata + targets.

    Returns ``(metric_rows, meta_lookup, targets)``. Empty triple on failure.
    All HTTP calls happen inside the contextmanager so the subprocess dies
    on any exception path.
    """
    metric_rows: list[tuple[str, int]] = []
    meta_lookup: dict[str, dict] = {}
    targets: list[dict] = []
    try:
        with kubectl_port_forward(namespace, service, port) as local_port:
            base = f"http://127.0.0.1:{local_port}"
            # 1) every metric name
            names_payload = http_get_json_qs(
                base, "/api/v1/label/__name__/values"
            )
            names: list[str] = []
            if isinstance(names_payload, dict):
                data = names_payload.get("data") or []
                if isinstance(data, list):
                    names = [str(x) for x in data if isinstance(x, str)]
            if verbose:
                print(
                    f"  [MetricsLayer] pf: /api/v1/label/__name__/values → "
                    f"{len(names)} metric names"
                )
            # Heuristic series-count: rank by series cardinality via the
            # cheap query ``count by (__name__) ({__name__!=""})``. If the
            # query is heavy on this Prom we fall back to an even-weight
            # ranking (1 per name).
            cardinality: dict[str, int] = {}
            count_payload = http_get_json_qs(
                base,
                "/api/v1/query",
                {"query": 'count by (__name__) ({__name__!=""})'},
            )
            for nm, cnt in _extract_metric_names(count_payload):
                cardinality[nm] = max(int(cnt), cardinality.get(nm, 0))
            for nm in names:
                if nm not in cardinality:
                    cardinality[nm] = 1
            metric_rows = sorted(
                cardinality.items(), key=lambda kv: (-kv[1], kv[0])
            )[: max(0, top_n)]

            # 2) /metadata — Prom returns the full table in one shot. Cap to
            # the top_n metric names so we don't bloat memory on very wide
            # clusters.
            meta_payload = http_get_json_qs(base, "/api/v1/metadata")
            if isinstance(meta_payload, dict):
                meta_data = meta_payload.get("data") or {}
                if isinstance(meta_data, dict):
                    target_names = {nm for nm, _ in metric_rows}
                    for mn, entries in meta_data.items():
                        if mn not in target_names:
                            continue
                        if isinstance(entries, list) and entries:
                            first = entries[0] if isinstance(entries[0], dict) else {}
                            meta_lookup[mn] = {
                                "metric_type": str(first.get("type") or "unknown"),
                                "help": str(first.get("help") or "")[:200],
                            }

            # 3) /targets — active scrape jobs.
            tgt_payload = http_get_json_qs(
                base, "/api/v1/targets", {"state": "active"}
            )
            targets = _extract_targets(tgt_payload)
            if verbose:
                print(
                    f"  [MetricsLayer] pf: /api/v1/metadata → "
                    f"{len(meta_lookup)} entries; /api/v1/targets → "
                    f"{len(targets)} targets"
                )
    except Exception as exc:
        warn(f"  [MetricsLayer] kubectl-pf path failed: {exc} — soft-degrade")
        return [], {}, []
    return metric_rows, meta_lookup, targets


class MetricsLayer(Layer):
    name = "metrics"
    refresh_trigger = "interval:10m"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        # v0.51.0: default to ON. Operator can disable explicitly with
        # ``metrics_use_kubectl_pf=false``.
        use_pf_flag = ctx.get("metrics_use_kubectl_pf")
        use_pf = (use_pf_flag is None) or bool(use_pf_flag)

        top_n = int(ctx.get("metrics_top_n") or 1000)
        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))
        existing_module_ids: set[str] = set(
            ctx.get("_existing_module_ids", set())
        )

        from ..client import call_tool as _call_tool_sync

        promql = 'count by (__name__) ({__name__!=""})'
        metric_rows: list[tuple[str, int]] = []
        meta_lookup: dict[str, dict] = {}
        targets: list[dict] = []
        mcp_payload = None

        # 1) MCP-first (Phase 8H code path).
        if endpoint is not None:
            try:
                mcp_payload = _call_tool_sync(
                    endpoint,
                    "query_metrics",
                    {"promql": promql, "query": promql},
                )
            except ConnectionError:
                raise
            except Exception as exc:
                if verbose:
                    print(
                        f"  [MetricsLayer] query_metrics failed: {exc} — soft-degrade"
                    )
                mcp_payload = {"error": str(exc)}

            if isinstance(mcp_payload, dict) and mcp_payload.get("error"):
                if verbose:
                    print(
                        f"  [MetricsLayer] MCP query_metrics error: "
                        f"{mcp_payload['error']}"
                    )
            elif mcp_payload is not None:
                metric_rows = _extract_metric_names(mcp_payload)

            if endpoint is not None:
                try:
                    t_payload = _call_tool_sync(
                        endpoint, "prom_get_targets", {"state": "active"}
                    )
                except ConnectionError:
                    raise
                except Exception as exc:
                    if verbose:
                        print(
                            f"  [MetricsLayer] prom_get_targets unavailable: "
                            f"{exc} — soft-degrade"
                        )
                    t_payload = None
                if isinstance(t_payload, dict) and t_payload.get("error"):
                    if verbose:
                        print(
                            f"  [MetricsLayer] prom_get_targets error: "
                            f"{t_payload['error']}"
                        )
                elif t_payload is not None:
                    targets = _extract_targets(t_payload)

        mcp_yielded_metrics = bool(metric_rows)
        mcp_yielded_targets = bool(targets)

        # 2) kubectl-pf fallback when MCP came back empty.
        if use_pf and not (mcp_yielded_metrics and mcp_yielded_targets):
            if not _have_kubectl():
                warn(
                    "  [MetricsLayer] kubectl-pf fallback skipped — kubectl "
                    "not on PATH"
                )
            elif not _have_current_context():
                warn(
                    "  [MetricsLayer] kubectl-pf fallback skipped — no "
                    "current-context"
                )
            else:
                ns = ctx.get("metrics_pf_namespace") or "monitoring"
                svc = (
                    ctx.get("metrics_pf_service")
                    or "prometheus-kube-prometheus-prometheus"
                )
                port = int(ctx.get("metrics_pf_port") or 9090)
                if verbose:
                    print(
                        f"  [MetricsLayer] kubectl-pf fallback to "
                        f"svc/{ns}/{svc}:{port}"
                    )
                pf_metrics, pf_meta, pf_targets = _scrape_prom_via_pf(
                    namespace=ns,
                    service=svc,
                    port=port,
                    top_n=top_n,
                    verbose=verbose,
                )
                if pf_metrics:
                    metric_rows = pf_metrics
                if pf_meta:
                    meta_lookup.update(pf_meta)
                if pf_targets:
                    targets = pf_targets

        metric_rows.sort(key=lambda kv: (-kv[1], kv[0]))
        metric_rows = metric_rows[: max(0, top_n)]

        if verbose:
            print(
                f"  [MetricsLayer] enumerated {len(metric_rows)} metric names "
                f"(top_n={top_n})"
            )
            print(
                f"  [MetricsLayer] discovered {len(targets)} scrape targets"
            )

        # If MCP gave us metric names but no metadata, optionally probe the
        # legacy /metadata-via-MCP path. Only when we don't already have
        # entries from the kubectl-pf scan.
        if endpoint is not None and metric_rows and not meta_lookup:
            for mname, _ in metric_rows[:50]:
                try:
                    m_payload = _call_tool_sync(
                        endpoint,
                        "query_metrics",
                        {"name": mname, "mode": "metadata"},
                    )
                except ConnectionError:
                    raise
                except Exception:
                    continue
                if isinstance(m_payload, dict) and m_payload.get("error"):
                    continue
                md = _extract_metric_metadata(m_payload)
                if md:
                    meta_lookup[mname] = md

        target_jobs: dict[str, list[dict]] = defaultdict(list)
        for tg in targets:
            target_jobs[tg["job"]].append(tg)

        nodes: list[dict] = []
        edges: list[dict] = []
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

        target_node_ids: dict[tuple[str, str], str] = {}
        for tg in targets:
            tid = f"scrape_target:{tg['job']}/{tg['instance']}"
            if (tg["job"], tg["instance"]) in target_node_ids:
                continue
            target_node_ids[(tg["job"], tg["instance"])] = tid
            nodes.append(
                {
                    "id": tid,
                    "type": "scrape_target",
                    "label": f"{tg['job']}/{tg['instance']}",
                    "job": tg["job"],
                    "instance": tg["instance"],
                    "namespace": tg["namespace"],
                    "pod": tg["pod"],
                    "health": tg["health"],
                    "last_scrape": tg["last_scrape"],
                }
            )
            mod_id = _match_module_for_job(tg["job"], existing_module_ids)
            if mod_id:
                edges.append(
                    {
                        "source": mod_id,
                        "target": tid,
                        "relation": "scrapes_for",
                    }
                )

        for mname, series_count in metric_rows:
            md = meta_lookup.get(mname, {})
            scrape_jobs = sorted(
                {
                    tg["job"]
                    for tg in targets
                    if tg["pod"] and tg["pod"].startswith(mname)
                }
            )
            mid = f"metric:{mname}"
            is_high_card = bool(series_count > 1000)
            is_anomaly = bool(series_count > 10000)
            nodes.append(
                {
                    "id": mid,
                    "type": "metric",
                    "label": mname,
                    "name": mname,
                    "metric_type": md.get("metric_type", "unknown"),
                    "help": md.get("help", ""),
                    "series_count": int(series_count),
                    "scrape_jobs": scrape_jobs,
                    "last_seen": now_iso,
                    "is_high_cardinality": is_high_card,
                    "is_anomaly": is_anomaly,
                }
            )
            attribution_jobs = scrape_jobs or list(target_jobs.keys())
            if not scrape_jobs and len(attribution_jobs) != 1:
                attribution_jobs = []
            for j in attribution_jobs:
                for tg in target_jobs.get(j, []):
                    src = target_node_ids.get((tg["job"], tg["instance"]))
                    if not src:
                        continue
                    edges.append(
                        {"source": src, "target": mid, "relation": "produces"}
                    )

            for cold_app_id in existing_app_ids:
                try:
                    _, body = cold_app_id.split(":", 1)
                    _env, app_name = body.split("/", 1)
                except ValueError:
                    continue
                pod_match = any(
                    tg["pod"] and tg["pod"].startswith(app_name + "-")
                    for tg in targets
                )
                if pod_match:
                    edges.append(
                        {
                            "source": cold_app_id,
                            "target": mid,
                            "relation": "instrumented_by",
                        }
                    )

        if verbose:
            print(
                f"  [MetricsLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges
