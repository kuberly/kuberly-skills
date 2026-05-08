"""MetricsLayer — Prometheus metric metadata + scrape topology."""

from __future__ import annotations

import contextlib
import datetime as _dt
import shutil
import subprocess
import sys
import time
from collections import defaultdict

from .base import Layer


@contextlib.contextmanager
def _kubectl_port_forward(
    namespace: str,
    service: str,
    remote_port: int,
    *,
    kubectl: str | None = None,
    timeout_s: float = 8.0,
):
    """Spin up `kubectl port-forward -n <ns> svc/<svc> 0:<remote_port>` in a
    subprocess and yield the local port the kernel allocated.

    Captures stderr (kubectl writes "Forwarding from 127.0.0.1:<port> ->
    <remote_port>" there) to discover the random local port. Kills the
    process on context exit.
    """
    bin_path = kubectl or shutil.which("kubectl")
    if not bin_path:
        raise RuntimeError("kubectl not found on PATH")
    cmd = [
        bin_path,
        "port-forward",
        "-n",
        namespace,
        f"svc/{service}",
        f"0:{int(remote_port)}",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    local_port = 0
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = (proc.stderr.read() if proc.stderr else "") or ""
                raise RuntimeError(f"kubectl port-forward exited early: {err[:300]}")
            line = ""
            if proc.stdout is not None:
                # Both newer and older kubectl print to stdout; some versions
                # use stderr. Probe both with a short non-blocking peek.
                try:
                    import select

                    r, _, _ = select.select([proc.stdout, proc.stderr], [], [], 0.2)
                    for stream in r:
                        line = stream.readline() or ""
                        if line:
                            break
                except Exception:
                    line = proc.stdout.readline() or ""
            if not line:
                continue
            # Forwarding from 127.0.0.1:54321 -> 9090
            idx = line.find("127.0.0.1:")
            if idx >= 0:
                tail = line[idx + len("127.0.0.1:") :]
                port_str = ""
                for ch in tail:
                    if ch.isdigit():
                        port_str += ch
                    else:
                        break
                if port_str.isdigit():
                    local_port = int(port_str)
                    break
        if not local_port:
            raise RuntimeError("kubectl port-forward did not announce a local port")
        yield local_port
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        except Exception:
            pass


def _prom_kubectl_pf_query(
    promql: str,
    *,
    namespace: str,
    service: str,
    remote_port: int,
    timeout_s: float = 10.0,
) -> dict | None:
    """Run a single PromQL instant query through a kubectl-port-forwarded
    Prometheus. Returns the decoded ``{"status": "success", "data": {...}}``
    or ``None`` on failure. Kills the port-forward on exit.
    """
    try:
        import urllib.parse
        import urllib.request
        import json as _json
    except Exception:
        return None
    try:
        with _kubectl_port_forward(
            namespace, service, remote_port
        ) as local_port:
            qs = urllib.parse.urlencode({"query": promql})
            url = f"http://127.0.0.1:{local_port}/api/v1/query?{qs}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                return _json.loads(body)
            except Exception:
                return None
    except Exception as exc:
        print(
            f"  [MetricsLayer] kubectl-pf query failed: {exc}", file=sys.stderr
        )
        return None


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


class MetricsLayer(Layer):
    name = "metrics"
    refresh_trigger = "interval:10m"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        # Phase 8H: kubectl-pf fallback is gated by an opt-in flag so the
        # default path stays pure-MCP. When the operator passes
        # ``metrics_use_kubectl_pf=true`` and no usable endpoint is around,
        # we shell out to kubectl-port-forward and hit Prom directly.
        use_pf = bool(ctx.get("metrics_use_kubectl_pf"))
        if not endpoint and not use_pf:
            if verbose:
                print("  [MetricsLayer] skip — no mcp_endpoint in ctx")
            return [], []

        top_n = int(ctx.get("metrics_top_n") or 200)
        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))
        existing_module_ids: set[str] = set(
            ctx.get("_existing_module_ids", set())
        )

        from ..client import call_tool as _call_tool_sync

        promql = 'count by (__name__) ({__name__!=""})'
        metric_rows: list[tuple[str, int]] = []
        payload = None
        if endpoint is not None:
            try:
                payload = _call_tool_sync(
                    endpoint,
                    "query_metrics",
                    # Send both the v0.45.1 wrapper key (``promql``) and the
                    # legacy key (``query``) so we match either flavour.
                    {"promql": promql, "query": promql},
                )
            except ConnectionError:
                raise
            except Exception as exc:
                if verbose:
                    print(
                        f"  [MetricsLayer] query_metrics failed: {exc} — soft-degrade"
                    )
                payload = {"error": str(exc)}

        # When the MCP-side Prom upstream is blank (Tempo/Prom/Loki MCP
        # wrapper unwired), opt-in kubectl-pf path probes the in-cluster
        # Prom directly.
        mcp_failed = (
            payload is None
            or (isinstance(payload, dict) and payload.get("error"))
        )
        if use_pf and mcp_failed:
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
            pf_payload = _prom_kubectl_pf_query(
                promql,
                namespace=ns,
                service=svc,
                remote_port=port,
            )
            if pf_payload is not None:
                payload = pf_payload

        if isinstance(payload, dict) and payload.get("error"):
            if verbose:
                print(f"  [MetricsLayer] query_metrics error: {payload['error']}")
        elif payload is not None:
            metric_rows = _extract_metric_names(payload)

        metric_rows.sort(key=lambda kv: (-kv[1], kv[0]))
        metric_rows = metric_rows[: max(0, top_n)]

        if verbose:
            print(
                f"  [MetricsLayer] enumerated {len(metric_rows)} metric names (top_n={top_n})"
            )

        targets: list[dict] = []
        try:
            t_payload = _call_tool_sync(
                endpoint, "prom_get_targets", {"state": "active"}
            )
        except ConnectionError:
            raise
        except Exception as exc:
            if verbose:
                print(
                    f"  [MetricsLayer] prom_get_targets unavailable: {exc} — soft-degrade"
                )
            t_payload = None

        if isinstance(t_payload, dict) and t_payload.get("error"):
            if verbose:
                print(
                    f"  [MetricsLayer] prom_get_targets error: {t_payload['error']}"
                )
        elif t_payload is not None:
            targets = _extract_targets(t_payload)

        if verbose:
            print(f"  [MetricsLayer] discovered {len(targets)} scrape targets")

        # Per-metric metadata (mode=metadata) is unsupported by the
        # ai-agent-tool wrapper — it'd just emit isError per call. Skip it
        # when we have no metric rows; otherwise still try (some wrappers do
        # support it) but never raise.
        meta_lookup: dict[str, dict] = {}
        if metric_rows:
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
