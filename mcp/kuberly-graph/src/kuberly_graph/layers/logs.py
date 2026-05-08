"""LogsLayer — Loki log-template clustering."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from .base import Layer


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


def _iter_log_streams(payload):
    if payload is None:
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


class LogsLayer(Layer):
    name = "logs"
    refresh_trigger = "interval:5m"

    def scan(self, ctx: dict) -> tuple[list[dict], list[dict]]:
        endpoint = ctx.get("mcp_endpoint")
        verbose = bool(ctx.get("verbose"))
        if not endpoint:
            if verbose:
                print("  [LogsLayer] skip — no mcp_endpoint in ctx")
            return [], []

        window = ctx.get("logs_window") or "1h"
        limit = int(ctx.get("logs_limit") or 5000)
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

        from ..client import call_tool as _call_tool_sync

        buckets: dict[tuple[str, str, str], dict] = {}
        trace_index: dict[str, list[tuple[tuple[str, str, str], str]]] = defaultdict(
            list
        )

        for env in envs:
            args = {
                "query": _logql_for_env(env),
                "start": f"-{window}",
                "limit": limit,
            }
            try:
                payload = _call_tool_sync(endpoint, "query_logs", args)
            except ConnectionError:
                raise
            except Exception as exc:
                raise ConnectionError(
                    f"LogsLayer query_logs(env={env}) failed: {exc}"
                ) from exc
            if isinstance(payload, dict) and payload.get("error"):
                if verbose:
                    print(
                        f"  [LogsLayer] env={env} query_logs error: {payload['error']}"
                    )
                continue

            line_count = 0
            for stream, ts, raw in _iter_log_streams(payload):
                line_count += 1
                service = _extract_service(stream, raw)
                parsed = _try_parse_json_line(raw)
                level = _extract_level(raw, parsed)
                trace_id = _extract_trace_id(raw, parsed)
                template = _normalize_log_template(raw)
                if not template:
                    continue
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

            if verbose:
                print(
                    f"  [LogsLayer] env={env} ingested {line_count} lines, "
                    f"{sum(1 for k in buckets if k[0] == env)} templates"
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
            print(f"  [LogsLayer] emitted {len(nodes)} nodes / {len(edges)} edges")
        return nodes, edges
