"""Discover the live-cluster MCP endpoint from a consumer repo's `.mcp.json`.

Used by `regenerate_graph` / `regenerate_all` so operators don't have to pass
a JSON `mcp_url` argument every time. After `aws sso login`, `kubectl`
configuration, and ai-agent-tool MCP wiring, the consumer repo already has the
HTTP URL + bearer token in `.mcp.json`. We resolve `${VAR}` env-var
substitution in headers and return a normalized endpoint dict.

Selection rule (in order):
  1. Entry whose name == "ai-agent-tool" or contains "ai-agent" (HTTP first,
     stdio second).
  2. Any entry with `"type": "http"` whose URL doesn't look like the
     kuberly-platform itself or the old kuberly-graph alias.
  3. Otherwise None.

Returned shape:
  {"url": "...", "headers": {...}}            # HTTP entries
  {"stdio_cmd": [...] | "...", "env": {...}}  # stdio entries
  None                                        # nothing usable

Pure stdlib. No crash on missing env vars — drops the offending header and
logs a warning to stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _resolve_env_string(value: str) -> tuple[str, list[str]]:
    """Substitute ${VAR} references from os.environ. Returns (resolved, missing).

    `missing` is the list of env vars that were referenced but not set.
    """
    missing: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        env_val = os.environ.get(name)
        if env_val is None:
            missing.append(name)
            return match.group(0)
        return env_val

    return _VAR_RE.sub(_sub, value), missing


def _resolve_headers(raw: dict | None) -> dict:
    """Resolve ${VAR} in header values. Drop headers whose vars are missing."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            out[key] = value
            continue
        resolved, missing = _resolve_env_string(value)
        if missing:
            print(
                f"  warn: dropping MCP header {key!r} — unresolved env "
                f"vars: {missing}",
                file=sys.stderr,
            )
            continue
        out[key] = resolved
    return out


def _looks_like_self(url: str | None, name: str) -> bool:
    """Skip kuberly-platform / old kuberly-graph entries.

    They are graph entrypoints, not live cluster MCPs.
    """
    lower_name = name.lower()
    if "kuberly-graph" in lower_name or "kuberly-platform" in lower_name:
        return True
    if not url:
        return False
    lower_url = url.lower()
    return "kuberly-graph" in lower_url or "kuberly-platform" in lower_url


def _entry_to_endpoint(name: str, entry: dict) -> dict | None:
    """Convert a single `mcpServers` entry to our endpoint shape, or None."""
    if not isinstance(entry, dict):
        return None
    entry_type = (entry.get("type") or "").lower()
    url = entry.get("url")
    if entry_type == "http" or url:
        if not url:
            return None
        return {
            "url": url,
            "headers": _resolve_headers(entry.get("headers")),
        }
    cmd = entry.get("command")
    if cmd:
        args = entry.get("args") or []
        if isinstance(cmd, str) and isinstance(args, list):
            stdio_cmd = [cmd, *[str(a) for a in args]]
        else:
            stdio_cmd = cmd
        env = entry.get("env")
        out: dict[str, Any] = {"stdio_cmd": stdio_cmd}
        if isinstance(env, dict):
            out["env"] = {**os.environ, **{k: str(v) for k, v in env.items()}}
        return out
    return None


def discover_live_mcp(repo_root: str | Path) -> dict | None:
    """Look up `<repo_root>/.mcp.json` for a live-cluster MCP entry.

    Prefers HTTP entries whose name matches `ai-agent-tool` (or contains
    `ai-agent`); falls back to any HTTP entry that doesn't look like
    kuberly-platform/kuberly-graph; finally any usable stdio entry under the
    same naming rule. Returns the endpoint dict or None.

    Logs to stderr (not stdout — stdout is reserved for tool JSON output).
    """
    root = Path(repo_root).resolve()
    mcp_path = root / ".mcp.json"
    if not mcp_path.is_file():
        return None
    try:
        raw = json.loads(mcp_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  warn: failed to read {mcp_path}: {exc}", file=sys.stderr)
        return None

    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        return None

    # Priority buckets.
    ai_http: list[tuple[str, dict]] = []
    ai_stdio: list[tuple[str, dict]] = []
    other_http: list[tuple[str, dict]] = []

    for name, entry in servers.items():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        entry_type = (entry.get("type") or "").lower()
        is_http = entry_type == "http" or bool(url)
        is_ai = name == "ai-agent-tool" or "ai-agent" in name.lower()
        if _looks_like_self(url, name):
            continue
        if is_ai and is_http:
            ai_http.append((name, entry))
        elif is_ai and not is_http:
            ai_stdio.append((name, entry))
        elif is_http:
            other_http.append((name, entry))

    for bucket in (ai_http, ai_stdio, other_http):
        for name, entry in bucket:
            endpoint = _entry_to_endpoint(name, entry)
            if endpoint:
                print(
                    f"  info: auto-discovered live MCP {name!r} "
                    f"({'url=' + endpoint['url'] if endpoint.get('url') else 'stdio'})",
                    file=sys.stderr,
                )
                return endpoint
    return None


__all__ = ["discover_live_mcp"]
