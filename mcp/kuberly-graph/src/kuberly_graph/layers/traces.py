"""TracesLayer — Tempo service/operation aggregation."""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict

from .base import Layer


def _percentiles_ms(durations_ms: list[float]) -> tuple[float, float, float]:
    if not durations_ms:
        return 0.0, 0.0, 0.0
    sorted_d = sorted(float(x) for x in durations_ms)
    n = len(sorted_d)
    if n == 1:
        v = sorted_d[0]
        return v, v, v
    try:
        import statistics as _stats

        if n >= 2:
            qs = _stats.quantiles(sorted_d, n=100, method="inclusive")
            return float(qs[49]), float(qs[94]), float(qs[98])
    except Exception:
        pass

    def _pick(p: float) -> float:
        idx = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return float(sorted_d[idx])

    return _pick(50), _pick(95), _pick(99)


def _is_error_status(status_code) -> bool:
    if status_code is None:
        return False
    if isinstance(status_code, bool):
        return bool(status_code)
    if isinstance(status_code, (int, float)):
        return int(status_code) == 2
    if isinstance(status_code, str):
        s = status_code.strip().upper()
        return s in {"ERROR", "STATUS_CODE_ERROR", "2"}
    return False


def _otlp_attrs_to_dict(attrs) -> dict:
    out: dict = {}
    if not isinstance(attrs, list):
        return out
    for a in attrs:
        if not isinstance(a, dict):
            continue
        k = a.get("key")
        if not k:
            continue
        v = a.get("value")
        if isinstance(v, dict):
            for vk in (
                "stringValue",
                "intValue",
                "doubleValue",
                "boolValue",
                "value",
            ):
                if vk in v:
                    out[str(k)] = v[vk]
                    break
        else:
            out[str(k)] = v
    return out


def _iter_otlp_resource_spans(rs_list, default_trace_id: str):
    if not isinstance(rs_list, list):
        return
    for rs in rs_list:
        if not isinstance(rs, dict):
            continue
        resource = rs.get("resource") or {}
        attrs = _otlp_attrs_to_dict(resource.get("attributes") or [])
        scope_spans = (
            rs.get("scopeSpans") or rs.get("instrumentationLibrarySpans") or []
        )
        for ss in scope_spans:
            if not isinstance(ss, dict):
                continue
            for sp in ss.get("spans") or []:
                if not isinstance(sp, dict):
                    continue
                sp = dict(sp)
                sp["_resource_attrs"] = attrs
                sp["_trace_id"] = sp.get("traceId") or default_trace_id or ""
                yield sp


def _iter_trace_spans(payload):
    if not payload:
        return
    if isinstance(payload, dict):
        for key in ("traces", "data", "result", "results"):
            traces = payload.get(key)
            if not isinstance(traces, list):
                continue
            for tr in traces:
                if not isinstance(tr, dict):
                    continue
                trace_id = (
                    tr.get("traceID")
                    or tr.get("trace_id")
                    or tr.get("id")
                    or ""
                )
                if isinstance(tr.get("spans"), list):
                    for sp in tr["spans"]:
                        if isinstance(sp, dict):
                            sp = dict(sp)
                            sp["_trace_id"] = trace_id
                            yield sp
                rs_list = tr.get("resourceSpans") or tr.get("batches") or []
                yield from _iter_otlp_resource_spans(rs_list, trace_id)
            return
        rs_list = payload.get("resourceSpans") or payload.get("batches") or []
        if rs_list:
            yield from _iter_otlp_resource_spans(rs_list, "")
            return
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield from _iter_trace_spans(item)


def _span_service_name(span: dict) -> str:
    res = span.get("_resource_attrs") or {}
    for key in ("service.name", "service", "k8s.deployment.name"):
        v = res.get(key)
        if v:
            return str(v)
    for key in ("serviceName", "service", "service_name"):
        v = span.get(key)
        if v:
            return str(v)
    proc = span.get("process") or {}
    if isinstance(proc, dict):
        for key in ("serviceName", "service.name"):
            v = proc.get(key)
            if v:
                return str(v)
    return "unknown"


def _span_operation_name(span: dict) -> str:
    for key in ("name", "operationName", "operation_name"):
        v = span.get(key)
        if v:
            return str(v)
    return "unknown"


def _span_duration_ms(span: dict) -> float:
    start_ns = span.get("startTimeUnixNano") or span.get("start_time_unix_nano")
    end_ns = span.get("endTimeUnixNano") or span.get("end_time_unix_nano")
    try:
        if start_ns and end_ns:
            return max(0.0, (float(end_ns) - float(start_ns)) / 1_000_000.0)
    except Exception:
        pass
    for key in ("durationMs", "duration_ms"):
        v = span.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    for key in ("durationNanos", "duration_nanos"):
        v = span.get(key)
        if isinstance(v, (int, float)):
            return float(v) / 1_000_000.0
    v = span.get("duration")
    if isinstance(v, (int, float)):
        return float(v) / 1000.0 if float(v) > 1e6 else float(v)
    if isinstance(v, str):
        s = v.strip().lower()
        for suffix, mult in (
            ("ms", 1.0),
            ("us", 0.001),
            ("ns", 1e-6),
            ("s", 1000.0),
        ):
            if s.endswith(suffix):
                try:
                    return float(s[: -len(suffix)]) * mult
                except Exception:
                    return 0.0
    return 0.0


def _span_status(span: dict):
    st = span.get("status")
    if isinstance(st, dict):
        return st.get("code") or st.get("status_code") or st.get("message")
    return st or span.get("status_code") or span.get("statusCode")


def _span_ids(span: dict) -> tuple[str, str]:
    sid = span.get("spanId") or span.get("span_id") or span.get("spanID") or ""
    pid = (
        span.get("parentSpanId")
        or span.get("parent_span_id")
        or span.get("parentSpanID")
        or ""
    )
    return str(sid), str(pid)


def _traceql_for_env(env: str) -> str:
    if not env:
        return "{}"
    return '{ resource.k8s.namespace.name="' + env + '" }'


def _aggregate_traces(spans: list[dict]):
    span_lookup: dict[str, tuple[str, str]] = {}
    for sp in spans:
        sid, _ = _span_ids(sp)
        if sid:
            span_lookup[sid] = (
                _span_service_name(sp),
                _span_operation_name(sp),
            )

    service_stats: dict[str, dict] = defaultdict(
        lambda: {"durations": [], "errors": 0, "total": 0}
    )
    op_stats: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"durations": [], "errors": 0, "total": 0}
    )
    service_calls: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"durations": [], "errors": 0, "total": 0}
    )
    op_calls: dict[tuple[tuple[str, str], tuple[str, str]], dict] = defaultdict(
        lambda: {"durations": [], "errors": 0, "total": 0}
    )
    traces_seen: set = set()

    for sp in spans:
        svc = _span_service_name(sp)
        op = _span_operation_name(sp)
        dur = _span_duration_ms(sp)
        is_err = _is_error_status(_span_status(sp))
        tid = sp.get("_trace_id") or ""
        if tid:
            traces_seen.add(tid)

        s = service_stats[svc]
        s["durations"].append(dur)
        s["total"] += 1
        if is_err:
            s["errors"] += 1

        o = op_stats[(svc, op)]
        o["durations"].append(dur)
        o["total"] += 1
        if is_err:
            o["errors"] += 1

        _, parent_id = _span_ids(sp)
        if parent_id and parent_id in span_lookup:
            p_svc, p_op = span_lookup[parent_id]
            if p_svc != svc:
                key = (p_svc, svc)
                e = service_calls[key]
                e["durations"].append(dur)
                e["total"] += 1
                if is_err:
                    e["errors"] += 1
            ok = ((p_svc, p_op), (svc, op))
            oe = op_calls[ok]
            oe["durations"].append(dur)
            oe["total"] += 1
            if is_err:
                oe["errors"] += 1

    return service_stats, op_stats, service_calls, op_calls, traces_seen


class TracesLayer(Layer):
    name = "traces"
    refresh_trigger = "interval:10m"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        if not endpoint:
            if verbose:
                print("  [TracesLayer] skip — no mcp_endpoint in ctx")
            return [], []

        window = str(ctx.get("traces_window") or "1h")
        limit = int(ctx.get("traces_limit") or 500)
        envs: list[str] = list(ctx.get("envs") or [])
        existing_app_ids: set[str] = set(ctx.get("_existing_app_ids", set()))
        existing_module_ids: set[str] = set(
            ctx.get("_existing_module_ids", set())
        )

        from ..client import call_tool as _call_tool_sync

        all_spans: list[dict] = []
        traces_seen_total: set = set()
        # Two query shapes coexist in the wild:
        #   - legacy TraceQL via {"query": "{}"}
        #   - ai-agent-tool wrapper which requires {"service": ...} or
        #     {"trace_id": ...} and returns isError otherwise.
        # We try the TraceQL form first; if it errors with a "service is
        # required"-style message we fall back to scanning per-service
        # using existing_app_ids as the seed list.
        queries: list[tuple[str, dict]] = []
        if envs:
            for env in envs:
                queries.append(
                    (
                        env,
                        {
                            "query": _traceql_for_env(env),
                            "limit": max(1, int(limit)),
                            "start": f"-{window}",
                            "since": window,
                        },
                    )
                )
        else:
            queries.append(
                ("", {"query": "{}", "limit": max(1, int(limit)), "since": window})
            )

        # Per-service fallback seed.
        seed_services: list[str] = []
        for app_id in existing_app_ids:
            try:
                _, body = app_id.split(":", 1)
                _env, name = body.split("/", 1)
                if name and name not in seed_services:
                    seed_services.append(name)
            except ValueError:
                continue

        for env, args in queries:
            try:
                payload = _call_tool_sync(endpoint, "query_traces", args)
            except ConnectionError:
                raise
            except Exception as exc:
                if verbose:
                    print(
                        f"  [TracesLayer] query_traces(env={env!r}) failed: {exc} — soft-degrade"
                    )
                continue
            if isinstance(payload, dict) and payload.get("error"):
                err_text = str(payload.get("error") or "").lower()
                if (
                    "service is required" in err_text
                    or "trace_id" in err_text
                    or "service" in err_text
                ):
                    if verbose:
                        print(
                            f"  [TracesLayer] env={env!r} wrapper requires service — "
                            f"falling back to per-service queries (seed n={len(seed_services)})"
                        )
                    for svc in seed_services[:50]:
                        try:
                            sub = _call_tool_sync(
                                endpoint,
                                "query_traces",
                                {
                                    "service": svc,
                                    "since": window,
                                    "limit": max(1, int(limit)),
                                },
                            )
                        except ConnectionError:
                            raise
                        except Exception:
                            continue
                        if isinstance(sub, dict) and sub.get("error"):
                            continue
                        for sp in _iter_trace_spans(sub):
                            all_spans.append(sp)
                            tid = sp.get("_trace_id") or ""
                            if tid:
                                traces_seen_total.add(tid)
                    continue
                if verbose:
                    print(
                        f"  [TracesLayer] env={env!r} error: {payload['error']} — soft-degrade"
                    )
                continue
            spans_added = 0
            for sp in _iter_trace_spans(payload):
                all_spans.append(sp)
                spans_added += 1
                tid = sp.get("_trace_id") or ""
                if tid:
                    traces_seen_total.add(tid)
            if verbose:
                print(
                    f"  [TracesLayer] env={env!r} ingested {spans_added} spans"
                )

        if not all_spans:
            if verbose:
                print("  [TracesLayer] no spans ingested — soft-degrade")
            return [], []

        if verbose:
            print(
                f"  [TracesLayer] aggregating {len(all_spans)} spans across "
                f"{len(traces_seen_total)} traces"
            )

        (
            service_stats,
            op_stats,
            service_calls,
            op_calls,
            _seen,
        ) = _aggregate_traces(all_spans)

        nodes: list[dict] = []
        edges: list[dict] = []
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()

        for svc, st in service_stats.items():
            total = int(st["total"])
            errs = int(st["errors"])
            err_rate = (errs / total) if total else 0.0
            p50, p95, p99 = _percentiles_ms(st["durations"])
            nodes.append(
                {
                    "id": f"service:{svc}",
                    "type": "service",
                    "label": svc,
                    "service": svc,
                    "total_spans": total,
                    "error_spans": errs,
                    "error_rate": round(err_rate, 4),
                    "p50_ms": round(p50, 3),
                    "p95_ms": round(p95, 3),
                    "p99_ms": round(p99, 3),
                    "is_anomaly": bool(err_rate > 0.05 and total > 20),
                    "last_seen": now_iso,
                }
            )

        for (svc, op), st in op_stats.items():
            total = int(st["total"])
            errs = int(st["errors"])
            err_rate = (errs / total) if total else 0.0
            p50, p95, p99 = _percentiles_ms(st["durations"])
            nodes.append(
                {
                    "id": f"operation:{svc}/{op}",
                    "type": "operation",
                    "label": f"{svc}/{op}",
                    "service": svc,
                    "operation": op,
                    "count": total,
                    "error_count": errs,
                    "error_rate": round(err_rate, 4),
                    "p50_ms": round(p50, 3),
                    "p95_ms": round(p95, 3),
                    "p99_ms": round(p99, 3),
                    "is_anomaly": bool(err_rate > 0.05 and total > 10),
                    "last_seen": now_iso,
                }
            )

        svc_call_rows = list(service_calls.items())
        svc_call_rows.sort(key=lambda kv: -int(kv[1]["total"]))
        for (a, b), st in svc_call_rows[:50]:
            total = int(st["total"])
            errs = int(st["errors"])
            err_rate = (errs / total) if total else 0.0
            p50, p95, p99 = _percentiles_ms(st["durations"])
            edges.append(
                {
                    "source": f"service:{a}",
                    "target": f"service:{b}",
                    "relation": "calls",
                    "call_count": total,
                    "error_count": errs,
                    "error_rate": round(err_rate, 4),
                    "p50_ms": round(p50, 3),
                    "p95_ms": round(p95, 3),
                    "p99_ms": round(p99, 3),
                }
            )

        op_call_rows = list(op_calls.items())
        op_call_rows.sort(key=lambda kv: -int(kv[1]["total"]))
        for ((sa, oa), (sb, ob)), st in op_call_rows[:100]:
            total = int(st["total"])
            errs = int(st["errors"])
            err_rate = (errs / total) if total else 0.0
            p50, p95, p99 = _percentiles_ms(st["durations"])
            edges.append(
                {
                    "source": f"operation:{sa}/{oa}",
                    "target": f"operation:{sb}/{ob}",
                    "relation": "calls",
                    "call_count": total,
                    "error_count": errs,
                    "error_rate": round(err_rate, 4),
                    "p50_ms": round(p50, 3),
                    "p95_ms": round(p95, 3),
                    "p99_ms": round(p99, 3),
                }
            )

        service_names = set(service_stats.keys())
        for cold_app_id in existing_app_ids:
            try:
                _, body = cold_app_id.split(":", 1)
                _env, app_name = body.split("/", 1)
            except ValueError:
                continue
            if app_name in service_names:
                edges.append(
                    {
                        "source": cold_app_id,
                        "target": f"service:{app_name}",
                        "relation": "traces_as",
                    }
                )

        for mid in existing_module_ids:
            try:
                _, body = mid.split(":", 1)
                _provider, mname = body.split("/", 1)
            except ValueError:
                continue
            if not mname or len(mname) < 4:
                continue
            mname_lower = mname.lower()
            for svc in service_names:
                sl = svc.lower()
                if (
                    mname_lower == sl
                    or sl.startswith(mname_lower + "-")
                    or f"-{mname_lower}-" in sl
                    or sl.endswith(f"-{mname_lower}")
                ):
                    edges.append(
                        {
                            "source": mid,
                            "target": f"service:{svc}",
                            "relation": "instruments",
                        }
                    )

        if verbose:
            print(f"  [TracesLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
