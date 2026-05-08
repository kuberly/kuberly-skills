"""LogsLayer — Loki log-template clustering.

v0.51.0: discover labels first (namespaces / apps / services) and iterate
per-namespace LogQL. When the upstream MCP returns 0 lines / isError across
the whole chain, fall back to ``kubectl port-forward svc/loki-gateway -n
monitoring 80`` and hit the Loki HTTP API directly. Per-namespace cap of
``logs_per_namespace_limit`` (default 1000 lines).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from .base import Layer
from ._pf import (
    _have_current_context,
    _have_kubectl,
    http_get_json_qs,
    kubectl_port_forward,
    warn,
)


_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:?\d{2})?"
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_IPV6_RE = re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_QSTR_RE = re.compile(r'"[^"]*"')
_NUM_RE = re.compile(r"\b\d+\b")
_LEVEL_RE = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b")
_TRACE_ID_RE = re.compile(
    r"\btrace[_-]?id[\"']?\s*[:=]\s*[\"']?([0-9a-f]{16,32})", re.I
)


def _parse_iso8601(value) -> str:
    if value is None:
        return ""
    try:
        if isinstance(value, (int, float)):
            ns = int(value)
        elif isinstance(value, str):
            v = value.strip()
            if v.isdigit():
                ns = int(v)
            else:
                return v[:32]
        else:
            return ""
        secs = ns / 1_000_000_000.0
        return _dt.datetime.fromtimestamp(secs, tz=_dt.timezone.utc).isoformat()
    except Exception:
        return ""


def _normalize_log_template(line: str) -> str:
    s = line
    s = _UUID_RE.sub("<UUID>", s)
    s = _TS_RE.sub("<TS>", s)
    s = _IPV4_RE.sub("<IP>", s)
    s = _IPV6_RE.sub("<IP>", s)
    s = _EMAIL_RE.sub("<EMAIL>", s)
    s = _QSTR_RE.sub('"<STR>"', s)
    s = _NUM_RE.sub("<N>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def _extract_service(stream_labels: dict, raw_line: str) -> str:
    for key in (
        "app_kubernetes_io_name",
        "app",
        "service_name",
        "service",
        "container",
        "container_name",
    ):
        v = stream_labels.get(key)
        if v:
            return str(v)
    pod = stream_labels.get("pod") or stream_labels.get("pod_name") or ""
    if pod:
        return re.sub(r"-[a-z0-9]{5,}.*$", "", str(pod)) or str(pod)
    return "unknown"


def _extract_level(raw_line: str, parsed: dict | None) -> str:
    if isinstance(parsed, dict):
        for key in ("level", "severity", "lvl", "log.level"):
            v = parsed.get(key)
            if isinstance(v, str) and v:
                return v.upper()
    m = _LEVEL_RE.search(raw_line)
    return m.group(1).upper() if m else ""


def _extract_trace_id(raw_line: str, parsed: dict | None) -> str:
    if isinstance(parsed, dict):
        for key in ("trace_id", "traceId", "traceID", "trace.id"):
            v = parsed.get(key)
            if isinstance(v, str) and v:
                return v
    m = _TRACE_ID_RE.search(raw_line)
    return m.group(1) if m else ""


def _try_parse_json_line(raw_line: str) -> dict | None:
    line = raw_line.strip()
    if not (line.startswith("{") and line.endswith("}")):
        return None
    try:
        obj = json.loads(line)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _logql_for_env(env: str) -> str:
    return (
        f'{{namespace=~".+", kuberly_io_env="{env}"}} or {{namespace="{env}"}}'
    )


def _logql_fallbacks(env: str, namespace_seed: list[str]) -> list[str]:
    """Loki upstreams in the wild are flaky about the exact label set. Try a
    chain — narrow first, broad last — and return the first response that
    actually has lines.
    """
    out: list[str] = [_logql_for_env(env)]
    out.append(f'{{namespace="{env}"}}')
    for ns in namespace_seed:
        out.append(f'{{namespace="{ns}"}}')
    out.append('{job=~".+"}')
    out.append('{app=~".+"}')
    return out


def _parse_logcli_text(text: str):
    """ai-agent-tool wraps Loki responses with a tag line ``[Loki via logcli
    (no upstream MCP)]\\n<one line per log entry>``. Each line is roughly
    ``<RFC3339Z> {labels=...} <raw>``. Yield ``(stream_dict, ts, raw)``
    tuples mirroring the JSON shape callers expect.
    """
    if not isinstance(text, str):
        return
    body = text.strip()
    if not body:
        return
    if body.startswith("[") and "\n" in body:
        body = body[body.find("\n") + 1 :]
    label_re = re.compile(r"\{([^}]*)\}")
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)")
    pair_re = re.compile(r'(\w[\w.-]*)="([^"]*)"|(\w[\w.-]*)=([^,\s}]+)')
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        ts_m = ts_re.match(line)
        if not ts_m:
            continue
        ts = ts_m.group(1)
        rest = line[ts_m.end() :].lstrip()
        labels: dict[str, str] = {}
        if rest.startswith("{"):
            lab_m = label_re.match(rest)
            if lab_m:
                lab_body = lab_m.group(1)
                rest = rest[lab_m.end() :].lstrip()
                for m in pair_re.finditer(lab_body):
                    k = m.group(1) or m.group(3)
                    v = m.group(2) or m.group(4) or ""
                    if k:
                        labels[k] = v
        yield labels, ts, rest


def _iter_log_streams(payload):
    if payload is None:
        return
    if isinstance(payload, str):
        yield from _parse_logcli_text(payload)
        return
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("result") or payload
        if isinstance(data, dict):
            results = data.get("result") or data.get("streams") or []
        else:
            results = data
    elif isinstance(payload, list):
        results = payload
    else:
        return
    for entry in results or []:
        if not isinstance(entry, dict):
            continue
        stream = entry.get("stream") or entry.get("labels") or {}
        if not isinstance(stream, dict):
            stream = {}
        values = entry.get("values") or entry.get("entries") or []
        if not isinstance(values, list):
            continue
        for pair in values:
            if isinstance(pair, list) and len(pair) >= 2:
                ts, line = pair[0], pair[1]
            elif isinstance(pair, dict):
                ts = pair.get("ts") or pair.get("timestamp")
                line = pair.get("line") or pair.get("message") or ""
            else:
                continue
            if not isinstance(line, str):
                continue
            yield stream, ts, line


def _window_to_seconds(window: str) -> int:
    """Turn ``1h`` / ``30m`` / ``5d`` into seconds. Default 3600."""
    if not isinstance(window, str) or len(window) < 2:
        return 3600
    try:
        unit = window[-1].lower()
        val = int(window[:-1])
        return val * {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 3600)
    except Exception:
        return 3600


def _walk_for_org_slug(node) -> str:
    """Find ``org_slug`` anywhere inside the JSON tree. The kuberly
    ``shared-infra.json`` structure has gone through a few revisions
    (top-level vs nested under ``shared-infra``); a recursive walk
    handles both.
    """
    if isinstance(node, dict):
        slug = node.get("org_slug")
        if isinstance(slug, str) and slug:
            return slug
        for v in node.values():
            r = _walk_for_org_slug(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _walk_for_org_slug(v)
            if r:
                return r
    return ""


def _discover_loki_tenant(repo_root: Path, envs: list[str]) -> str:
    """Read ``components/<env>/shared-infra.json`` for ``org_slug``.

    Loki on kuberly clusters runs multi-tenant; the X-Scope-OrgID header
    is the cluster's ``org_slug`` (also referred to as ``tenant_id`` in
    the alloy values). When multiple envs disagree we pick the first
    populated one — the assumption is one cluster per workspace.
    """
    comp_dir = repo_root / "components"
    if not comp_dir.exists():
        return ""
    candidates: list[str] = []
    if envs:
        candidates = list(envs)
    else:
        candidates = sorted(
            p.name
            for p in comp_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
    for env in candidates:
        si_path = comp_dir / env / "shared-infra.json"
        if not si_path.exists():
            continue
        try:
            data = json.loads(si_path.read_text())
        except Exception:
            continue
        slug = _walk_for_org_slug(data)
        if slug:
            return slug
    return ""


def _loki_pf_fetch_lines(
    *,
    namespace_seed: list[str],
    window: str,
    per_ns_limit: int,
    pf_namespace: str,
    pf_service: str,
    pf_port: int,
    tenant: str,
    verbose: bool,
):
    """Generator yielding ``(env_or_ns, namespace_label, stream_labels, ts,
    raw)``. Pulls labels via ``/loki/api/v1/labels`` then iterates per
    namespace via ``/loki/api/v1/query_range``. All HTTP happens inside the
    PF context so the subprocess dies on any path. Sends X-Scope-OrgID to
    satisfy multi-tenant Loki gateways."""
    out: list[tuple[str, str, dict, str, str]] = []
    headers = {"X-Scope-OrgID": tenant} if tenant else None
    try:
        with kubectl_port_forward(pf_namespace, pf_service, pf_port) as port:
            base = f"http://127.0.0.1:{port}"
            now_s = int(time.time())
            start_s = now_s - _window_to_seconds(window)
            start_ns = start_s * 1_000_000_000
            end_ns = now_s * 1_000_000_000

            # Discover namespace label values.
            ns_payload = http_get_json_qs(
                base,
                "/loki/api/v1/label/namespace/values",
                headers=headers,
            )
            namespaces: list[str] = []
            if isinstance(ns_payload, dict):
                d = ns_payload.get("data") or []
                if isinstance(d, list):
                    namespaces = [str(x) for x in d if isinstance(x, str)]
            for ns in namespace_seed:
                if ns not in namespaces:
                    namespaces.append(ns)
            if verbose:
                print(
                    f"  [LogsLayer] pf: discovered {len(namespaces)} "
                    f"namespace label values (tenant={tenant or '<none>'})"
                )

            # Per-namespace fetch.
            for ns in namespaces:
                logql = f'{{namespace="{ns}"}}'
                payload = http_get_json_qs(
                    base,
                    "/loki/api/v1/query_range",
                    {
                        "query": logql,
                        "start": str(start_ns),
                        "end": str(end_ns),
                        "limit": str(per_ns_limit),
                        "direction": "backward",
                    },
                    headers=headers,
                )
                if not isinstance(payload, dict):
                    continue
                ingested = 0
                for stream, ts, raw in _iter_log_streams(payload):
                    out.append((ns, ns, stream, ts, raw))
                    ingested += 1
                if verbose and ingested:
                    print(
                        f"  [LogsLayer] pf: ns={ns} ingested {ingested} lines"
                    )
    except Exception as exc:
        warn(f"  [LogsLayer] kubectl-pf path failed: {exc} — soft-degrade")
        return []
    return out


class LogsLayer(Layer):
    name = "logs"
    refresh_trigger = "interval:5m"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))

        window = ctx.get("logs_window") or "1h"
        limit = int(ctx.get("logs_limit") or 5000)
        per_ns_limit = int(ctx.get("logs_per_namespace_limit") or 1000)
        envs: list[str] = list(ctx.get("envs") or [])
        if not envs:
            comp_dir = Path(ctx.get("repo_root", ".")) / "components"
            if comp_dir.exists():
                envs = sorted(
                    p.name
                    for p in comp_dir.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                )

        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))

        # Collect namespace seeds from the live store (k8s_resource).
        namespace_seed: list[str] = []
        seen_ns: set[str] = set()
        store = ctx.get("graph_store")
        if store is not None:
            try:
                for n in store.all_nodes():
                    if n.get("type") != "k8s_resource":
                        continue
                    ns = str(n.get("namespace") or "").strip()
                    if not ns or ns in seen_ns or ns in {"cluster", "kube-system"}:
                        continue
                    seen_ns.add(ns)
                    namespace_seed.append(ns)
            except Exception:
                pass

        from ..client import call_tool as _call_tool_sync

        buckets: dict[tuple[str, str, str], dict] = {}
        trace_index: dict[str, list[tuple[tuple[str, str, str], str]]] = defaultdict(
            list
        )

        # 1) MCP-first per-env.
        mcp_total_lines = 0
        if endpoint is not None:
            for env in envs:
                forms = _logql_fallbacks(env, namespace_seed)
                payload = None
                chosen_form = ""
                last_err = ""
                for logql in forms:
                    args = {
                        "logql": logql,
                        "since": window,
                        "limit": limit,
                        "query": logql,
                        "start": f"-{window}",
                    }
                    try:
                        payload = _call_tool_sync(endpoint, "query_logs", args)
                    except ConnectionError:
                        raise
                    except Exception as exc:
                        last_err = str(exc)
                        payload = None
                        continue
                    if isinstance(payload, dict) and payload.get("error"):
                        last_err = str(payload.get("error"))
                        payload = None
                        continue
                    has_lines = False
                    for _stream, _ts, _raw in _iter_log_streams(payload):
                        has_lines = True
                        break
                    if has_lines:
                        chosen_form = logql
                        break
                    payload = None
                if payload is None:
                    if verbose:
                        print(
                            f"  [LogsLayer] env={env} all LogQL forms returned "
                            f"0 lines / errors (last_err={last_err[:200]})"
                        )
                    continue
                if verbose:
                    print(
                        f"  [LogsLayer] env={env} chose LogQL form: {chosen_form}"
                    )
                line_count = 0
                for stream, ts, raw in _iter_log_streams(payload):
                    self._ingest_line(
                        env, stream, ts, raw, buckets, trace_index
                    )
                    line_count += 1
                mcp_total_lines += line_count
                if verbose:
                    print(
                        f"  [LogsLayer] env={env} ingested {line_count} lines "
                        f"via MCP"
                    )

        # 2) kubectl-pf fallback when MCP gave us nothing.
        used_pf = False
        if not buckets:
            if not _have_kubectl():
                warn("  [LogsLayer] kubectl-pf skipped — kubectl not on PATH")
            elif not _have_current_context():
                warn(
                    "  [LogsLayer] kubectl-pf skipped — no current-context"
                )
            else:
                pf_namespace = ctx.get("logs_pf_namespace") or "monitoring"
                pf_service = ctx.get("logs_pf_service") or "loki-gateway"
                pf_port = int(ctx.get("logs_pf_port") or 80)
                # Loki multi-tenant: send X-Scope-OrgID. Auto-discovered
                # from components/<env>/shared-infra.json (org_slug).
                tenant = (
                    ctx.get("loki_tenant")
                    or ctx.get("logs_tenant")
                    or _discover_loki_tenant(
                        Path(ctx.get("repo_root", ".")), envs
                    )
                )
                if verbose:
                    print(
                        f"  [LogsLayer] kubectl-pf fallback to "
                        f"svc/{pf_namespace}/{pf_service}:{pf_port} "
                        f"(tenant={tenant or '<none>'})"
                    )
                lines = _loki_pf_fetch_lines(
                    namespace_seed=namespace_seed
                    or [e for e in envs if e],
                    window=window,
                    per_ns_limit=per_ns_limit,
                    pf_namespace=pf_namespace,
                    pf_service=pf_service,
                    pf_port=pf_port,
                    tenant=tenant,
                    verbose=verbose,
                )
                used_pf = True
                # We treat each namespace label value as both env and service
                # bucket key — env defaults to the namespace name when there
                # is no explicit env mapping.
                for env_label, _ns_label, stream, ts, raw in lines:
                    self._ingest_line(
                        env_label, stream, ts, raw, buckets, trace_index
                    )
                if verbose:
                    print(
                        f"  [LogsLayer] pf: ingested {len(lines)} lines / "
                        f"{len(buckets)} templates"
                    )

        nodes: list[dict] = []
        edges: list[dict] = []
        for (env, service, hash8), bucket in buckets.items():
            levels: dict = dict(bucket["levels"])
            err_count = (
                levels.get("ERROR", 0)
                + levels.get("FATAL", 0)
                + levels.get("CRITICAL", 0)
            )
            is_error = bool(bucket["count"]) and err_count > (bucket["count"] // 2)
            is_anomaly = bool(bucket["count"] > 5 and is_error)
            tpl_node_id = f"log_template:{env}/{service}/{hash8}"
            nodes.append(
                {
                    "id": tpl_node_id,
                    "type": "log_template",
                    "label": f"{service}: {bucket['template'][:80]}",
                    "env": env,
                    "service": service,
                    "template_hash": bucket["template_hash"],
                    "template": bucket["template"],
                    "count": bucket["count"],
                    "levels": levels,
                    "is_error": is_error,
                    "is_anomaly": is_anomaly,
                    "sample_line": bucket["sample_line"],
                    "first_seen": bucket["first_seen"],
                    "last_seen": bucket["last_seen"],
                    "source": "kubectl-pf" if used_pf else "mcp",
                }
            )
            cold_app_id = f"app:{env}/{service}"
            if cold_app_id in existing_app_ids:
                edges.append(
                    {
                        "source": cold_app_id,
                        "target": tpl_node_id,
                        "relation": "emits",
                    }
                )

        candidate_pairs: dict[tuple[str, str], int] = {}
        for trace_id, hits in trace_index.items():
            if len(hits) < 2:
                continue
            for i in range(len(hits)):
                key_a, ts_a = hits[i]
                for j in range(i + 1, len(hits)):
                    key_b, ts_b = hits[j]
                    if key_a == key_b:
                        continue
                    if ts_a and ts_b:
                        try:
                            da = _dt.datetime.fromisoformat(
                                ts_a.replace("Z", "+00:00")
                            )
                            db = _dt.datetime.fromisoformat(
                                ts_b.replace("Z", "+00:00")
                            )
                            if abs((da - db).total_seconds()) > 30:
                                continue
                        except Exception:
                            pass
                    pair = tuple(
                        sorted(
                            (
                                f"log_template:{key_a[0]}/{key_a[1]}/{key_a[2]}",
                                f"log_template:{key_b[0]}/{key_b[1]}/{key_b[2]}",
                            )
                        )
                    )
                    candidate_pairs[pair] = candidate_pairs.get(pair, 0) + 1

        ranked = sorted(
            candidate_pairs.items(), key=lambda kv: kv[1], reverse=True
        )[:100]
        for (a, b), shared in ranked:
            edges.append(
                {
                    "source": a,
                    "target": b,
                    "relation": "co_occurs_with",
                    "shared_traces": shared,
                }
            )

        if verbose:
            print(
                f"  [LogsLayer] emitted {len(nodes)} nodes / {len(edges)} edges"
            )
        return nodes, edges

    @staticmethod
    def _ingest_line(
        env: str,
        stream: dict,
        ts,
        raw: str,
        buckets: dict,
        trace_index: dict,
    ) -> None:
        service = _extract_service(stream, raw)
        parsed = _try_parse_json_line(raw)
        level = _extract_level(raw, parsed)
        trace_id = _extract_trace_id(raw, parsed)
        template = _normalize_log_template(raw)
        if not template:
            return
        tpl_hash = hashlib.sha1(
            template.encode("utf-8", errors="replace")
        ).hexdigest()
        key = (env, service, tpl_hash[:8])
        iso_ts = _parse_iso8601(ts)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "env": env,
                "service": service,
                "template_hash": tpl_hash,
                "template": template,
                "count": 0,
                "levels": defaultdict(int),
                "first_seen": iso_ts,
                "last_seen": iso_ts,
                "sample_line": raw[:300],
            }
            buckets[key] = bucket
        bucket["count"] += 1
        if level:
            bucket["levels"][level] += 1
        if iso_ts:
            if not bucket["first_seen"] or iso_ts < bucket["first_seen"]:
                bucket["first_seen"] = iso_ts
            if not bucket["last_seen"] or iso_ts > bucket["last_seen"]:
                bucket["last_seen"] = iso_ts
        if trace_id:
            trace_index[trace_id].append((key, iso_ts))
