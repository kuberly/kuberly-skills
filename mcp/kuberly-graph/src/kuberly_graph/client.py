"""MCP client helpers — port of the legacy `scripts/kuberly_graph_client.py`.

Two connection modes:
  - endpoint = {"url": "http://host:port/mcp"}  -> streamable HTTP transport
  - endpoint = {"stdio_cmd": "<cmd>" | [argv]}  -> spawn subprocess, stdio
                + optional `env` dict

Hard-fails on any connection error (re-raised as ConnectionError) so callers
can sys.exit(1) with a clean message.

v0.45.1: live MCP servers in the wild (notably the ai-agent-tool ->
kubernetes-mcp-server upstream) return tool results as kubectl plaintext
*tables*, not JSON. The previous parser only knew JSON, so every kubectl-style
list came back as 0 rows. This module now:

  - logs ``isError=true`` instead of swallowing it as empty,
  - parses kubectl-table TextContent into [{apiVersion, kind, metadata: {...}}]
    items so K8sLayer / ArgoLayer can populate from the live cluster, and
  - falls back through tool aliases (``resources_list`` ->
    ``pods_list``/``pods_list_in_namespace``/``namespaces_list``) when the
    primary tool returns an error like "tool not found" or RBAC-forbidden.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import shlex
import sys
from typing import Any, Awaitable, Callable, TypeVar


T = TypeVar("T")


def _run_coro_sync(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run an async coroutine from sync code, robust to a running event loop.

    If there is no running loop in the current thread, use ``asyncio.run``
    directly (the CLI path). If a loop is already running (FastMCP server
    transport — sync tool dispatch is invoked from inside the loop), we
    cannot call ``asyncio.run`` here; instead, hand the coroutine off to a
    short-lived worker thread that owns its own fresh event loop.

    ``coro_factory`` is a zero-arg callable that returns a fresh coroutine
    object — we may need to construct the coroutine in the worker thread so
    its event-loop bindings resolve there, not in the caller's thread.
    """

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if not in_loop:
        return asyncio.run(coro_factory())

    def _worker() -> T:
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_worker).result()


def _endpoint_str(endpoint: dict) -> str:
    if endpoint.get("url"):
        return f"url={endpoint['url']}"
    if endpoint.get("stdio_cmd"):
        cmd = endpoint["stdio_cmd"]
        if isinstance(cmd, list):
            cmd = " ".join(cmd)
        return f"stdio_cmd={cmd}"
    return repr(endpoint)


def _validate_endpoint(endpoint: dict) -> None:
    has_url = bool(endpoint.get("url"))
    has_stdio = bool(endpoint.get("stdio_cmd"))
    if has_url == has_stdio:
        raise ValueError(
            "endpoint must set exactly one of {'url', 'stdio_cmd'}; got "
            f"{sorted(k for k, v in endpoint.items() if v)}"
        )


def _join_text_content(content: Any) -> str:
    """Concatenate the text of every TextContent block in `content`."""
    if not content:
        return ""
    parts: list[str] = []
    for part in content:
        text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _extract_json_from_call_result(result: Any) -> Any:
    """Best-effort JSON extraction.

    Order of preference:
      1. ``structuredContent`` (FastMCP-native dict).
      2. JSON-decoded text content (single block or list of blocks).
      3. Raw concatenated text (fallback for plain-text responses).

    NOTE: this helper does NOT distinguish ``isError=true`` from a normal
    text response. Callers that need that distinction must use
    :func:`_normalize_call_result` instead.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        if "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured
    content = getattr(result, "content", None)
    if not content:
        return None
    if len(content) > 1:
        items: list[Any] = []
        ok = True
        for part in content:
            text = getattr(part, "text", None)
            if text is None:
                ok = False
                break
            try:
                items.append(json.loads(text))
            except (json.JSONDecodeError, TypeError):
                ok = False
                break
        if ok:
            return items
    for part in content:
        text = getattr(part, "text", None)
        if text is None:
            continue
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text
    return None


def _normalize_call_result(result: Any) -> dict:
    """Return ``{is_error, error_text, payload, raw_text}``.

    - ``is_error`` mirrors ``result.isError`` from the MCP spec.
    - ``error_text`` is the concatenated text content when ``is_error``,
      otherwise ``""``.
    - ``payload`` is the JSON-decoded body when parseable, else the raw text,
      else ``None``.
    - ``raw_text`` is the joined plaintext of every TextContent block.
    """
    is_error = bool(getattr(result, "isError", False))
    raw_text = _join_text_content(getattr(result, "content", None))
    if is_error:
        return {
            "is_error": True,
            "error_text": raw_text,
            "payload": None,
            "raw_text": raw_text,
        }
    payload = _extract_json_from_call_result(result)
    return {
        "is_error": False,
        "error_text": "",
        "payload": payload,
        "raw_text": raw_text,
    }


# ---------------------------------------------------------------------------
# kubectl-table parsing
# ---------------------------------------------------------------------------
#
# The ai-agent-tool MCP wraps `manusa/kubernetes-mcp-server`, which only
# returns plaintext tables (the same shape `kubectl get -o wide` produces).
# We rebuild minimal {apiVersion, kind, metadata} dicts from those tables so
# downstream layers don't need to know about transport-level shapes.


_LABELS_RE = re.compile(r"([A-Za-z0-9_./\-]+)=([^,]*)")


def _split_columns(line: str) -> list[str]:
    """Split a header / data line on runs of >=2 spaces.

    Single-column kubectl output uses 2+ spaces between columns; tabs are
    converted up-stream. Trailing whitespace is stripped from each cell.
    """
    parts = re.split(r" {2,}|\t", line.rstrip())
    return [p.strip() for p in parts if p.strip() != ""]


def _column_offsets(header: str, names: list[str]) -> list[int]:
    """Return the byte offset where each column header starts in ``header``.

    Used so we can recover columns whose values themselves contain
    embedded single spaces (e.g. STATUS = "Running", but LABELS = "k1=v1,k2=v2").
    """
    offsets: list[int] = []
    cursor = 0
    for name in names:
        idx = header.find(name, cursor)
        if idx < 0:
            offsets.append(cursor)
        else:
            offsets.append(idx)
            cursor = idx + len(name)
    return offsets


def _slice_by_offsets(line: str, offsets: list[int]) -> list[str]:
    out: list[str] = []
    n = len(offsets)
    for i, start in enumerate(offsets):
        end = offsets[i + 1] if i + 1 < n else None
        cell = line[start:end] if end is not None else line[start:]
        out.append(cell.strip())
    return out


def _parse_labels(cell: str) -> dict[str, str]:
    if not cell or cell.strip() in {"<none>", ""}:
        return {}
    out: dict[str, str] = {}
    for match in _LABELS_RE.finditer(cell):
        key, val = match.group(1), match.group(2)
        if key:
            out[key] = val
    return out


def parse_kubectl_table(
    text: str,
    *,
    default_api_version: str = "",
    default_kind: str = "",
) -> list[dict]:
    """Parse a kubectl-style plaintext table into [{apiVersion, kind,
    metadata: {name, namespace, labels}}].

    Supports both the namespaced shape (NAMESPACE column present) and the
    cluster-scoped shape (no NAMESPACE column). Header is the first non-blank
    non-comment line; "No resources found." returns []. Unknown columns are
    ignored — we only need name / namespace / labels / apiVersion / kind to
    build graph nodes.

    `default_api_version` / `default_kind` are used when the table lacks the
    APIVERSION / KIND columns (rare; e.g. `kubectl get -ohelp` formats).
    """
    if not isinstance(text, str) or not text.strip():
        return []

    # Drop lines that look like CLI noise the wrapper sometimes prepends.
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return []
    if lines[0].strip().startswith("No resources found"):
        return []

    header = lines[0]
    header_cols = _split_columns(header)
    if not header_cols:
        return []
    offsets = _column_offsets(header, header_cols)

    name_to_idx = {h.upper(): i for i, h in enumerate(header_cols)}
    idx_name = name_to_idx.get("NAME")
    if idx_name is None:
        # Not a recognisable kubectl table — bail out empty rather than
        # mis-parse arbitrary text.
        return []
    idx_ns = name_to_idx.get("NAMESPACE")
    idx_apiv = name_to_idx.get("APIVERSION")
    idx_kind = name_to_idx.get("KIND")
    idx_labels = name_to_idx.get("LABELS")
    idx_status = name_to_idx.get("STATUS")
    idx_age = name_to_idx.get("AGE")
    idx_node = name_to_idx.get("NODE")

    out: list[dict] = []
    for raw in lines[1:]:
        cells = _slice_by_offsets(raw, offsets)
        if len(cells) <= idx_name:
            continue
        name = cells[idx_name].strip()
        if not name or name in {"<none>", ""}:
            continue
        ns = cells[idx_ns].strip() if idx_ns is not None else ""
        if ns in {"<none>"}:
            ns = ""
        api_version = (
            cells[idx_apiv].strip()
            if idx_apiv is not None and idx_apiv < len(cells)
            else default_api_version
        )
        kind = (
            cells[idx_kind].strip()
            if idx_kind is not None and idx_kind < len(cells)
            else default_kind
        )
        labels_cell = (
            cells[idx_labels]
            if idx_labels is not None and idx_labels < len(cells)
            else ""
        )
        labels = _parse_labels(labels_cell)
        # Map kubectl AGE column to a synthetic creationTimestamp marker so
        # downstream code that branches on metadata.creationTimestamp doesn't
        # see uniformly empty strings.
        age = (
            cells[idx_age].strip()
            if idx_age is not None and idx_age < len(cells)
            else ""
        )
        node_name = (
            cells[idx_node].strip()
            if idx_node is not None and idx_node < len(cells)
            else ""
        )
        if node_name in {"<none>", ""}:
            node_name = ""
        status = (
            cells[idx_status].strip()
            if idx_status is not None and idx_status < len(cells)
            else ""
        )
        meta: dict = {
            "name": name,
            "namespace": ns,
            "labels": labels,
            "annotations": {},
        }
        if age:
            meta["age"] = age
        spec: dict = {}
        if node_name and (kind == "Pod" or default_kind == "Pod"):
            spec["nodeName"] = node_name
        out.append(
            {
                "apiVersion": api_version or default_api_version,
                "kind": kind or default_kind,
                "metadata": meta,
                "spec": spec,
                "status": {"phase": status} if status else {},
            }
        )
    return out


def _resources_from_payload(payload: Any) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("items", "resources", "data", "result"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        if "metadata" in payload:
            return [payload]
    return []


def _import_mcp_session():
    try:
        from mcp import ClientSession  # type: ignore[import-not-found]
    except Exception as exc:
        raise ConnectionError(f"mcp Python SDK not installed: {exc}") from exc
    return ClientSession


async def _open_session(endpoint: dict, runner):
    _validate_endpoint(endpoint)
    ClientSession = _import_mcp_session()

    async def _run(read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await runner(session)

    try:
        if endpoint.get("url"):
            from mcp.client.streamable_http import streamablehttp_client  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {}
            headers = endpoint.get("headers")
            if isinstance(headers, dict) and headers:
                kwargs["headers"] = headers
            async with streamablehttp_client(endpoint["url"], **kwargs) as ctx:
                read, write = ctx[0], ctx[1]
                return await _run(read, write)
        else:
            from mcp import StdioServerParameters  # type: ignore[import-not-found]
            from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

            cmd = endpoint["stdio_cmd"]
            if isinstance(cmd, str):
                parts = shlex.split(cmd)
            elif isinstance(cmd, list):
                parts = list(cmd)
            else:
                raise ValueError("stdio_cmd must be a str or list")
            if not parts:
                raise ValueError("stdio_cmd is empty")
            env = endpoint.get("env")
            params = StdioServerParameters(
                command=parts[0], args=parts[1:], env=env
            )
            async with stdio_client(params) as (read, write):
                return await _run(read, write)
    except ConnectionError:
        raise
    except Exception as exc:
        raise ConnectionError(
            f"MCP unreachable at {_endpoint_str(endpoint)}: {exc}"
        ) from exc


# Sentinel set used by callers that want a single warning per missing tool /
# unavailable upstream per scan, rather than a deluge.
class _SeenWarn:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def once(self, key: str, msg: str) -> None:
        if key in self.seen:
            return
        self.seen.add(key)
        print(msg, file=sys.stderr)


def _looks_like_missing_tool(error_text: str) -> bool:
    if not error_text:
        return False
    s = error_text.lower()
    return (
        "unknown tool" in s
        or "tool not found" in s
        or "no tool" in s
        or "not implemented" in s
    )


def _looks_like_missing_kind(error_text: str) -> bool:
    if not error_text:
        return False
    s = error_text.lower()
    return (
        "no matches for kind" in s
        or "could not find the requested resource" in s
        or "the server doesn't have a resource type" in s
    )


def _looks_like_forbidden(error_text: str) -> bool:
    if not error_text:
        return False
    return "forbidden" in error_text.lower()


async def call_mcp_tool(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Generic MCP tool call.

    Returns:
      - the parsed JSON content of the first TextContent block,
      - or the raw text when the response is plain text,
      - or ``{"error": <message>}`` when the tool itself raised /
        ``isError=true``.

    Raises ``ConnectionError`` on transport failure.
    """

    async def _runner(session):
        try:
            result = await session.call_tool(tool_name, arguments or {})
        except Exception as exc:
            return {"error": f"tool {tool_name} failed: {exc}"}
        norm = _normalize_call_result(result)
        if norm["is_error"]:
            err = norm["error_text"] or f"tool {tool_name} returned isError"
            print(
                f"  warn: MCP {tool_name}({_compact_args(arguments)}) "
                f"returned isError=true: {err[:300]}",
                file=sys.stderr,
            )
            return {"error": err}
        return norm["payload"]

    return await _open_session(endpoint, _runner)


def _compact_args(args: dict | None) -> str:
    if not args:
        return ""
    items = []
    for k, v in args.items():
        sval = str(v)
        if len(sval) > 60:
            sval = sval[:57] + "..."
        items.append(f"{k}={sval}")
    return ",".join(items)


def call_tool(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Sync wrapper around `call_mcp_tool`.

    Safe to invoke from inside a running event loop (e.g. the FastMCP
    transport): in that case the coroutine is dispatched to a worker thread
    with its own loop. CLI invocations have no running loop and use
    ``asyncio.run`` directly.
    """
    return _run_coro_sync(lambda: call_mcp_tool(endpoint, tool_name, arguments))


async def _list_tools(session) -> set[str]:
    try:
        tlist = await session.list_tools()
    except Exception as exc:
        print(
            f"  warn: list_tools failed: {exc} — assuming all tools present",
            file=sys.stderr,
        )
        return set()
    return {t.name for t in getattr(tlist, "tools", []) or []}


async def fetch_live_resources(
    endpoint: dict,
    kinds: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict]]:
    """Connect to an MCP server and call resources_list for each
    (apiVersion, kind). Returns {(apiVersion, kind): [resource, ...]}.

    Hard-fails on transport error, but soft-degrades on per-call MCP errors:
    every (apiVersion, kind) that fails (missing CRD, RBAC-forbidden, or tool
    not found) ends up with an empty list and a single stderr WARN line.
    Plain-text kubectl tables are parsed with :func:`parse_kubectl_table`.
    """
    out: dict[tuple[str, str], list[dict]] = {}
    warn = _SeenWarn()

    async def _runner(session):
        available = await _list_tools(session)
        if available and "resources_list" not in available:
            warn.once(
                "resources_list-missing",
                "  warn: MCP does not expose 'resources_list' — falling back to "
                "pods_list / pods_list_in_namespace / namespaces_list where possible",
            )

        has_pods_list = (not available) or ("pods_list" in available)
        has_namespaces_list = (
            (not available) or ("namespaces_list" in available)
        )

        for api_version, kind in kinds:
            args = {"apiVersion": api_version, "kind": kind}
            try:
                result = await session.call_tool("resources_list", args)
            except Exception as exc:
                # tool missing or transport hiccup — try a fallback for the
                # well-known core kinds; otherwise surface the error.
                fallback = await _resources_list_fallback(
                    session, api_version, kind, warn,
                    has_pods_list=has_pods_list,
                    has_namespaces_list=has_namespaces_list,
                )
                if fallback is not None:
                    out[(api_version, kind)] = fallback
                else:
                    out[(api_version, kind)] = []
                    warn.once(
                        f"err-{api_version}/{kind}",
                        f"  warn: resources_list({api_version}/{kind}) failed: {exc}",
                    )
                continue

            norm = _normalize_call_result(result)
            if norm["is_error"]:
                err = norm["error_text"]
                if _looks_like_missing_tool(err):
                    warn.once(
                        "resources_list-unknown",
                        f"  warn: 'resources_list' rejected by MCP: {err[:200]}",
                    )
                    fallback = await _resources_list_fallback(
                        session, api_version, kind, warn,
                        has_pods_list=has_pods_list,
                        has_namespaces_list=has_namespaces_list,
                    )
                    out[(api_version, kind)] = fallback if fallback is not None else []
                    continue
                if _looks_like_missing_kind(err):
                    warn.once(
                        f"missing-{api_version}/{kind}",
                        f"  warn: kind {api_version}/{kind} not present in cluster — skipping",
                    )
                    out[(api_version, kind)] = []
                    continue
                if _looks_like_forbidden(err):
                    warn.once(
                        f"forbidden-{api_version}/{kind}",
                        f"  warn: RBAC forbids listing {api_version}/{kind} — skipping",
                    )
                    out[(api_version, kind)] = []
                    continue
                # Other errors — log + treat as empty.
                warn.once(
                    f"err-{api_version}/{kind}",
                    f"  warn: resources_list({api_version}/{kind}) error: {err[:200]}",
                )
                out[(api_version, kind)] = []
                continue

            payload = norm["payload"]
            raw_text = norm["raw_text"]
            items: list[dict] = []
            if isinstance(payload, list) or (
                isinstance(payload, dict) and ("items" in payload or "metadata" in payload)
            ):
                items = _resources_from_payload(payload)
            elif isinstance(raw_text, str) and raw_text.strip():
                items = parse_kubectl_table(
                    raw_text,
                    default_api_version=api_version,
                    default_kind=kind,
                )
            else:
                items = []
            out[(api_version, kind)] = items

        return None

    await _open_session(endpoint, _runner)
    return out


async def _resources_list_fallback(
    session,
    api_version: str,
    kind: str,
    warn: _SeenWarn,
    *,
    has_pods_list: bool,
    has_namespaces_list: bool,
) -> list[dict] | None:
    """Try a per-kind alias when ``resources_list`` is missing or rejected.

    Returns a list of resource-like dicts on success, or ``None`` to signal
    "no usable fallback exists, caller should treat as empty".
    """
    if api_version == "v1" and kind == "Pod" and has_pods_list:
        try:
            r = await session.call_tool("pods_list", {})
        except Exception as exc:
            warn.once("pods_list-err", f"  warn: pods_list fallback failed: {exc}")
            return None
        norm = _normalize_call_result(r)
        if norm["is_error"]:
            warn.once(
                "pods_list-iserror",
                f"  warn: pods_list isError: {norm['error_text'][:200]}",
            )
            return None
        return parse_kubectl_table(
            norm["raw_text"], default_api_version="v1", default_kind="Pod"
        )

    if api_version == "v1" and kind == "Namespace" and has_namespaces_list:
        try:
            r = await session.call_tool("namespaces_list", {})
        except Exception as exc:
            warn.once(
                "namespaces_list-err", f"  warn: namespaces_list fallback failed: {exc}"
            )
            return None
        norm = _normalize_call_result(r)
        if norm["is_error"]:
            return None
        return parse_kubectl_table(
            norm["raw_text"], default_api_version="v1", default_kind="Namespace"
        )

    return None


def fetch_live_resources_sync(
    endpoint: dict,
    kinds: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict]]:
    """Sync wrapper around ``fetch_live_resources``.

    Safe under a running event loop (FastMCP transport): in that case the
    coroutine runs in a worker thread with its own fresh loop. CLI path
    uses ``asyncio.run`` directly.
    """
    return _run_coro_sync(lambda: fetch_live_resources(endpoint, kinds))


def call_mcp_tool_sync(endpoint: dict, tool_name: str, arguments: dict) -> Any:
    """Sync alias for :func:`call_tool` — kept for layer-side clarity."""
    return call_tool(endpoint, tool_name, arguments)


# ---------------------------------------------------------------------------
# v0.46.0 — CRD-driven API discovery
# ---------------------------------------------------------------------------
#
# `manusa/kubernetes-mcp-server` (the upstream behind ai-agent-tool) returns
# `resources_get` payloads as plain YAML, NOT JSON. We avoid the PyYAML
# dependency by tolerantly walking the indentation: only the keys we need
# (spec.group, spec.scope, spec.names.{kind,plural,singular},
# spec.versions[*].name) are extracted, everything else is ignored.


def parse_crd_spec_yaml(text: str) -> dict:
    """Pull ``{group, kind, scope, plural, singular, versions: [name,...]}``
    from a Kubernetes CRD ``resources_get`` YAML response.

    Defensive line-based parse — never raises on malformed input. Returns a
    dict with empty fields when the YAML is unparseable. Multiple ``versions``
    entries are supported; we capture the FIRST ``name: <X>`` field at indent
    4 inside each ``- ...`` entry under ``  versions:``.
    """
    out: dict = {
        "group": "",
        "kind": "",
        "scope": "",
        "plural": "",
        "singular": "",
        "versions": [],  # list of {name, served, storage}
    }
    if not isinstance(text, str) or "spec:" not in text:
        return out

    lines = text.splitlines()
    in_spec = False
    in_names = False
    in_versions = False
    current_version: dict | None = None

    for ln in lines:
        if re.match(r"^spec:\s*$", ln):
            in_spec = True
            continue
        if not in_spec:
            continue
        # Top-level keys (no indent) end the spec block.
        if ln and not ln.startswith(" ") and not ln.startswith("\t"):
            break
        # Inside spec, only 2-space-indent keys are sibling.
        m_top = re.match(r"^  (\w+):\s*(.*)$", ln)
        if m_top:
            key, val = m_top.group(1), m_top.group(2).strip()
            in_names = key == "names"
            if in_versions and current_version is not None:
                # Closed off the previous version entry's scope.
                current_version = None
            in_versions = key == "versions"
            if key == "group" and val:
                out["group"] = val
            elif key == "scope" and val:
                out["scope"] = val
            continue
        # Inside spec.names — match 4-space indent.
        if in_names:
            m_n = re.match(r"^    (\w+):\s*(\S.*)?$", ln)
            if m_n:
                k, v = m_n.group(1), (m_n.group(2) or "").strip()
                if k == "kind" and v:
                    out["kind"] = v
                elif k == "plural" and v:
                    out["plural"] = v
                elif k == "singular" and v:
                    out["singular"] = v
            elif not ln.startswith("    "):
                in_names = False
        # Inside spec.versions: list — entries start with `^  - `.
        if in_versions:
            m_dash = re.match(r"^  - (.*)$", ln)
            if m_dash:
                # New version entry. Capture inline `name: vX` if any.
                current_version = {"name": "", "served": True, "storage": False}
                out["versions"].append(current_version)
                tail = m_dash.group(1).strip()
                m_inline = re.match(r"^name:\s*(\S+)\s*$", tail)
                if m_inline:
                    current_version["name"] = m_inline.group(1)
                continue
            if current_version is not None:
                m_field = re.match(r"^    (\w+):\s*(\S.*)?$", ln)
                if m_field:
                    k, v = m_field.group(1), (m_field.group(2) or "").strip()
                    if k == "name" and v and not current_version["name"]:
                        current_version["name"] = v
                    elif k == "served" and v:
                        current_version["served"] = v.lower() != "false"
                    elif k == "storage" and v:
                        current_version["storage"] = v.lower() == "true"
    # Drop versions that never produced a name.
    out["versions"] = [v for v in out["versions"] if v.get("name")]
    return out


# Curated builtin kinds — beyond the v0.45.1 hardcoded set, we sweep the
# common namespaced + cluster-scoped objects that any cluster exposes. The
# list is conservative: anything missing from the cluster soft-degrades.
BUILTIN_K8S_KINDS: list[tuple[str, str]] = [
    # Workloads
    ("apps/v1", "Deployment"),
    ("apps/v1", "StatefulSet"),
    ("apps/v1", "DaemonSet"),
    ("apps/v1", "ReplicaSet"),
    ("batch/v1", "Job"),
    ("batch/v1", "CronJob"),
    ("v1", "Pod"),
    ("v1", "Node"),
    ("v1", "Namespace"),
    ("v1", "Service"),
    ("v1", "Endpoints"),
    ("discovery.k8s.io/v1", "EndpointSlice"),
    ("v1", "ServiceAccount"),
    ("v1", "ConfigMap"),
    ("v1", "Secret"),
    ("v1", "PersistentVolume"),
    ("v1", "PersistentVolumeClaim"),
    ("v1", "Event"),
    ("v1", "ResourceQuota"),
    ("v1", "LimitRange"),
    # Networking
    ("networking.k8s.io/v1", "Ingress"),
    ("networking.k8s.io/v1", "IngressClass"),
    ("networking.k8s.io/v1", "NetworkPolicy"),
    # Storage
    ("storage.k8s.io/v1", "StorageClass"),
    ("storage.k8s.io/v1", "VolumeAttachment"),
    ("storage.k8s.io/v1", "CSIDriver"),
    ("storage.k8s.io/v1", "CSINode"),
    # Autoscaling / disruption
    ("autoscaling/v2", "HorizontalPodAutoscaler"),
    ("policy/v1", "PodDisruptionBudget"),
    # RBAC (often forbidden by SA scope; soft-degrades)
    ("rbac.authorization.k8s.io/v1", "Role"),
    ("rbac.authorization.k8s.io/v1", "RoleBinding"),
    ("rbac.authorization.k8s.io/v1", "ClusterRole"),
    ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding"),
    # Coordination / admission / API surface
    ("coordination.k8s.io/v1", "Lease"),
    ("admissionregistration.k8s.io/v1", "ValidatingWebhookConfiguration"),
    ("admissionregistration.k8s.io/v1", "MutatingWebhookConfiguration"),
    ("apiregistration.k8s.io/v1", "APIService"),
    # Scheduling
    ("scheduling.k8s.io/v1", "PriorityClass"),
    # Node-scoped runtime (sometimes absent)
    ("node.k8s.io/v1", "RuntimeClass"),
]


# Cluster-scoped kinds — used to set `<ns>` to "cluster" in node ids when
# resources_list returns blank namespace.
CLUSTER_SCOPED_BUILTINS: set[str] = {
    "Node",
    "Namespace",
    "PersistentVolume",
    "StorageClass",
    "VolumeAttachment",
    "CSIDriver",
    "CSINode",
    "ClusterRole",
    "ClusterRoleBinding",
    "ValidatingWebhookConfiguration",
    "MutatingWebhookConfiguration",
    "APIService",
    "PriorityClass",
    "RuntimeClass",
    "IngressClass",
    "CustomResourceDefinition",
}


async def discover_kinds(
    endpoint: dict,
    *,
    fetch_crd_specs: bool = True,
) -> tuple[list[tuple[str, str]], dict[str, dict], set[str]]:
    """Return ``(kinds, crd_index, cluster_scoped_kinds)`` discovered live.

    - ``kinds`` is a deduplicated list of ``(apiVersion, Kind)`` covering both
      ``BUILTIN_K8S_KINDS`` and every served version of every CustomResource-
      Definition currently registered on the cluster.
    - ``crd_index`` maps the CRD's ``metadata.name`` to its parsed spec
      ``{group, kind, scope, plural, versions}`` so K8sLayer can wire
      ``crd → k8s_resource`` defines_kind edges.
    - ``cluster_scoped_kinds`` is the set of bare ``Kind`` strings that are
      cluster-scoped (so K8sLayer knows when the empty-namespace from a
      kubectl-table really means "cluster", not "missing").

    Soft-degrades on every error path — if the discovery tool is missing or
    rejected, returns ``(BUILTIN_K8S_KINDS, {}, CLUSTER_SCOPED_BUILTINS)``.
    """
    kinds: list[tuple[str, str]] = list(BUILTIN_K8S_KINDS)
    crd_index: dict[str, dict] = {}
    cluster_scoped: set[str] = set(CLUSTER_SCOPED_BUILTINS)
    seen: set[tuple[str, str]] = set(kinds)
    warn = _SeenWarn()

    async def _runner(session):
        nonlocal kinds, crd_index, cluster_scoped
        try:
            r = await session.call_tool(
                "resources_list",
                {
                    "apiVersion": "apiextensions.k8s.io/v1",
                    "kind": "CustomResourceDefinition",
                },
            )
        except Exception as exc:
            warn.once(
                "discover-tool-err",
                f"  warn: discovery resources_list(CRD) failed: {exc} — "
                "falling back to BUILTIN_K8S_KINDS only",
            )
            return None
        norm = _normalize_call_result(r)
        if norm["is_error"]:
            err = norm["error_text"]
            if _looks_like_missing_tool(err):
                warn.once(
                    "discover-tool-missing",
                    "  warn: cluster MCP lacks 'resources_list' — using "
                    "BUILTIN_K8S_KINDS only (no CRD discovery)",
                )
            elif _looks_like_missing_kind(err):
                warn.once(
                    "discover-no-crds",
                    "  warn: cluster has no apiextensions.k8s.io/v1 — "
                    "using BUILTIN_K8S_KINDS only",
                )
            elif _looks_like_forbidden(err):
                warn.once(
                    "discover-forbidden",
                    "  warn: RBAC forbids listing CRDs — using "
                    "BUILTIN_K8S_KINDS only",
                )
            else:
                warn.once(
                    "discover-err",
                    f"  warn: CRD listing returned isError: {err[:200]}",
                )
            return None

        crd_items = parse_kubectl_table(
            norm["raw_text"],
            default_api_version="apiextensions.k8s.io/v1",
            default_kind="CustomResourceDefinition",
        )
        if not crd_items:
            warn.once(
                "discover-empty",
                "  warn: CRD list parsed as empty — using BUILTIN_K8S_KINDS only",
            )
            return None

        # For each CRD we need group + scope + versions[].name. The
        # kubectl-table view of a CRD only carries `name`, so we issue one
        # `resources_get` per CRD when ``fetch_crd_specs`` is True (default).
        # Skip the per-spec fetch when caller passes False (smoke tests).
        for item in crd_items:
            crd_name = (item.get("metadata") or {}).get("name") or ""
            if not crd_name:
                continue
            if not fetch_crd_specs:
                crd_index[crd_name] = {"group": "", "kind": "", "scope": "", "versions": []}
                continue
            try:
                rg = await session.call_tool(
                    "resources_get",
                    {
                        "apiVersion": "apiextensions.k8s.io/v1",
                        "kind": "CustomResourceDefinition",
                        "name": crd_name,
                    },
                )
            except Exception as exc:
                warn.once(
                    f"crd-get-err-{crd_name}",
                    f"  warn: resources_get(CRD/{crd_name}) failed: {exc}",
                )
                continue
            normg = _normalize_call_result(rg)
            if normg["is_error"]:
                warn.once(
                    f"crd-get-iserror-{crd_name}",
                    f"  warn: resources_get(CRD/{crd_name}) isError: "
                    f"{normg['error_text'][:200]}",
                )
                continue
            spec = parse_crd_spec_yaml(normg["raw_text"])
            crd_index[crd_name] = spec
            grp = spec.get("group") or ""
            scope = spec.get("scope") or ""
            kind = spec.get("kind") or ""
            if not (grp and kind and spec.get("versions")):
                continue
            if scope == "Cluster":
                cluster_scoped.add(kind)
            for v in spec["versions"]:
                vname = v.get("name") or ""
                if not vname:
                    continue
                api = f"{grp}/{vname}"
                key = (api, kind)
                if key not in seen:
                    seen.add(key)
                    kinds.append(key)
        return None

    try:
        await _open_session(endpoint, _runner)
    except ConnectionError as exc:
        warn.once(
            "discover-transport",
            f"  warn: discovery transport failed: {exc}",
        )
    return kinds, crd_index, cluster_scoped


def discover_kinds_sync(
    endpoint: dict,
    *,
    fetch_crd_specs: bool = True,
) -> tuple[list[tuple[str, str]], dict[str, dict], set[str]]:
    """Sync wrapper around :func:`discover_kinds`."""
    return _run_coro_sync(
        lambda: discover_kinds(endpoint, fetch_crd_specs=fetch_crd_specs)
    )


__all__ = [
    "BUILTIN_K8S_KINDS",
    "CLUSTER_SCOPED_BUILTINS",
    "call_mcp_tool",
    "call_mcp_tool_sync",
    "call_tool",
    "discover_kinds",
    "discover_kinds_sync",
    "fetch_live_resources",
    "fetch_live_resources_sync",
    "parse_crd_spec_yaml",
    "parse_kubectl_table",
]
