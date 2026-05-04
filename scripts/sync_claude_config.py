#!/usr/bin/env python3
"""sync_claude_config.py — merge kuberly-skills wiring into consumer config.

APM has fixed semantics for hook + MCP deploy that don't fully reach Claude
Code's project-scope config (`.claude/settings.json` + `.mcp.json`) and
leaves Cursor's hook file empty. This script bridges those gaps: after
`apm install` lands kuberly-skills under `apm_modules/kuberly/kuberly-skills/`,
it merges canonical entries — pointing at that apm cache path — into:

  - `.claude/settings.json`  (Claude Code hooks)
  - `.mcp.json`              (Claude Code project-scope MCP servers)
  - `.cursor/hooks.json`     (Cursor hooks)
  - `.cursor/mcp.json`       (Cursor project-scope MCP servers)

Idempotent: same input -> no-op; nothing else in any file is touched.
User-authored entries that don't reference the apm cache path survive.

Run from the consumer repo root, typically via the `ensure-apm-skills`
pre-commit hook:

    python3 apm_modules/kuberly/kuberly-skills/scripts/sync_claude_config.py

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Path the script writes into command strings — the location APM lands the
# kuberly-skills package after install. Stable across versions.
APM_CACHE_PATH = "apm_modules/kuberly/kuberly-skills"

# A command is "owned by kuberly-skills" (and may be replaced on each run)
# if it contains ANY of these markers. Hooks pointing elsewhere (user's
# own scripts) survive untouched. Multiple markers cover legacy paths
# from before the v0.10.x sync model AND the v0.12.0 server rename.
KUBERLY_OWNED_MARKERS = (
    APM_CACHE_PATH,                # current — apm cache layout
    "scripts/kuberly_graph.py",    # legacy — pre-v0.10.x vendored MCP
    "scripts/kuberly_platform.py", # legacy — early-v0.12.x naming attempt
    "scripts/mcp/kuberly-graph/",  # legacy — sync_mcp.sh interim layout
    "scripts/mcp/kuberly-platform/", # legacy — same after v0.12.0 rename
    "scripts/hooks/orchestrator_route",  # legacy — pre-v0.10.4 consumer-local hook
    "Refreshing kuberly-graph",    # legacy — old statusMessage marker
    "Refreshing kuberly-platform", # current — statusMessage marker
)

# --- canonical entries ------------------------------------------------------

def _hooks_block() -> dict[str, list[dict[str, Any]]]:
    """The hooks kuberly-skills owns. Same shape for Claude and Cursor.

    Only UserPromptSubmit lives here. SessionStart was previously used to
    regenerate `.claude/graph.json` on every Claude Code session start —
    v0.13.0 moved that to the pre-commit pipeline (post_apm_install.sh)
    so each commit captures a fresh graph state and the MCP server reads
    the cached file at startup. SessionStart-on-every-Claude-launch is no
    longer needed and was removed to cut cold-start time.
    """
    return {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            f'python3 "$CLAUDE_PROJECT_DIR/{APM_CACHE_PATH}'
                            f'/scripts/hooks/orchestrator_route.py"'
                        ),
                        "timeout": 5,
                    }
                ]
            }
        ],
    }


def _mcp_server_claude() -> dict[str, Any]:
    """kuberly-platform MCP server for Claude Code project-scope `.mcp.json`."""
    return {
        "command": "python3",
        "args": [
            f"{APM_CACHE_PATH}/mcp/kuberly-platform/kuberly_platform.py",
            "mcp",
            "--repo",
            ".",
        ],
    }


def _mcp_server_cursor() -> dict[str, Any]:
    """kuberly-platform MCP server for Cursor `.cursor/mcp.json`.

    Cursor adds `type`, `tools`, `id` fields. Format mirrors what APM writes
    natively for self-defined stdio servers, so the two paths produce
    interchangeable output.
    """
    return {
        "type": "local",
        "tools": ["*"],
        "id": "",
        "command": "python3",
        "args": [
            f"{APM_CACHE_PATH}/mcp/kuberly-platform/kuberly_platform.py",
            "mcp",
            "--repo",
            ".",
        ],
    }


# --- merge helpers ----------------------------------------------------------

def _is_kuberly_owned_command(cmd: str, status: str = "") -> bool:
    """A command is ours if it contains any KUBERLY_OWNED_MARKERS substring,
    in either the command itself or its statusMessage."""
    haystack = (cmd or "") + " " + (status or "")
    return any(m in haystack for m in KUBERLY_OWNED_MARKERS)


def _matcher_is_kuberly_owned(matcher: Any) -> bool:
    """A matcher is owned if every command it contains is recognizably ours.

    Catches the current apm-cache layout AND legacy hand-wired layouts
    (vendored scripts/kuberly_graph.py, scripts/mcp/kuberly-graph/, ...).
    Mixed matchers (some kuberly, some user) are left alone — safer to
    over-preserve than to delete user hooks.
    """
    if not isinstance(matcher, dict):
        return False
    hooks = matcher.get("hooks")
    if not isinstance(hooks, list) or not hooks:
        return False
    for hook in hooks:
        if not isinstance(hook, dict):
            return False
        cmd = hook.get("command", "")
        status = hook.get("statusMessage", "")
        if not isinstance(cmd, str):
            return False
        if not _is_kuberly_owned_command(cmd, status):
            return False
    return True


def _merge_hooks_file(existing: dict[str, Any]) -> dict[str, Any]:
    """Merge kuberly hooks into a settings/hooks dict (idempotent).

    Same logic for Claude (`.claude/settings.json`) and Cursor
    (`.cursor/hooks.json`) — both runtimes use the same hooks shape.
    Per-runtime extras (e.g. Cursor's required top-level `version: 1`)
    are layered by the target's wrapper merger; see `_merge_cursor_hooks_file`.

    Events ever owned by kuberly-skills are cleaned even if we no longer
    add hooks to them (so the v0.13.0 drop of SessionStart actually
    removes legacy entries from upgraded consumers).
    """
    HISTORICAL_EVENTS = ("SessionStart", "UserPromptSubmit")
    out = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    out.setdefault("hooks", {})
    if not isinstance(out["hooks"], dict):
        return existing  # something weird — refuse to clobber
    new_block = _hooks_block()
    for event in HISTORICAL_EVENTS:
        current = out["hooks"].get(event, [])
        if not isinstance(current, list):
            current = []
        # Drop any kuberly-owned matchers from the existing list.
        filtered = [m for m in current if not _matcher_is_kuberly_owned(m)]
        # Add canonical matchers for events kuberly-skills currently owns.
        kuberly_matchers = new_block.get(event, [])
        merged = filtered + kuberly_matchers
        if merged:
            out["hooks"][event] = merged
        else:
            # Drop the empty key entirely so .claude/settings.json stays tidy.
            out["hooks"].pop(event, None)
    return out


def _merge_cursor_hooks_file(existing: dict[str, Any]) -> dict[str, Any]:
    """Cursor-flavored variant of `_merge_hooks_file`.

    Cursor 3.x rejects `.cursor/hooks.json` without a top-level
    `version: 1` field — the file is reported as invalid and ALL hooks
    silently fail to load (incl. our orchestrator-route + graph-refresh).
    Caught by Cursor's bugbot in the v0.12 PR review.

    `_merge_hooks_file` produces only `{"hooks": {...}}`; this wrapper
    layers `version: 1` on top. Idempotent — if the user already has a
    different version they wrote themselves, we don't override it.
    """
    inner = _merge_hooks_file(existing)
    # Build a new ordered dict so `version` lands at the top — easier
    # to read, matches Cursor's example schema. Preserve user's value
    # if they already set one.
    version = existing.get("version") if isinstance(existing, dict) else None
    if not isinstance(version, (int, str)):
        version = 1
    out: dict[str, Any] = {"version": version}
    for k, v in inner.items():
        if k == "version":
            continue
        out[k] = v
    return out


def _merge_mcp_file(existing: dict[str, Any], server_entry: dict[str, Any]) -> dict[str, Any]:
    """Merge kuberly-platform into an mcpServers dict (idempotent).

    Also removes any stale `kuberly-graph` entry — v0.12.0 renamed the
    server, no consumer should keep pointing at the old name.
    """
    out = json.loads(json.dumps(existing))
    out.setdefault("mcpServers", {})
    if not isinstance(out["mcpServers"], dict):
        return existing
    out["mcpServers"].pop("kuberly-graph", None)  # drop legacy on every sync
    out["mcpServers"]["kuberly-platform"] = server_entry
    return out


# --- io ---------------------------------------------------------------------

def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return dict(default)
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(
            f"sync_claude_config: WARNING — {path} is not valid JSON ({exc}); "
            "leaving it untouched.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return data if isinstance(data, dict) else dict(default)


def _write_if_changed(path: Path, data: dict[str, Any]) -> bool:
    """Write `data` to `path` (pretty-printed). Return True if file changed."""
    new = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if path.is_file():
        try:
            old = path.read_text(encoding="utf-8")
        except OSError:
            old = None
        if old == new:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return True


def _repo_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        )
        return Path(out.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path(os.getcwd())


def main() -> int:
    root = _repo_root()
    cache = root / APM_CACHE_PATH
    if not cache.is_dir():
        # APM hasn't installed kuberly-skills yet; nothing to wire.
        print(
            f"sync_claude_config: {APM_CACHE_PATH}/ not found — "
            "run `apm install` first.",
            file=sys.stderr,
        )
        return 0

    # The four files we manage, grouped by runtime. Each tuple:
    # (runtime_label, [(path, default, merger, file_label), ...])
    runtimes = [
        ("claude-code", [
            (root / ".claude" / "settings.json",
             {"hooks": {}},
             lambda d: _merge_hooks_file(d),
             ".claude/settings.json"),
            (root / ".mcp.json",
             {"mcpServers": {}},
             lambda d: _merge_mcp_file(d, _mcp_server_claude()),
             ".mcp.json"),
        ]),
        ("cursor", [
            (root / ".cursor" / "hooks.json",
             {"version": 1, "hooks": {}},
             lambda d: _merge_cursor_hooks_file(d),
             ".cursor/hooks.json"),
            (root / ".cursor" / "mcp.json",
             {"mcpServers": {}},
             lambda d: _merge_mcp_file(d, _mcp_server_cursor()),
             ".cursor/mcp.json"),
        ]),
    ]

    # Track per-runtime state so the summary shows what got configured.
    summary: list[tuple[str, list[str], list[str]]] = []
    for runtime, files in runtimes:
        wrote = []
        already = []
        for path, default, merger, label in files:
            before = _load_json(path, default)
            after = merger(before)
            if _write_if_changed(path, after):
                wrote.append(label)
            else:
                already.append(label)
        summary.append((runtime, wrote, already))

    # Always print the configured-runtime summary so it's obvious that
    # Claude Code is wired (apm-cli 0.9.x doesn't list it as a target —
    # this script is the wire).
    parts = []
    any_change = False
    for runtime, wrote, already in summary:
        managed = wrote + already
        verb = "updated" if wrote else "current"
        if wrote:
            any_change = True
        parts.append(f"{runtime}: {verb} ({', '.join(managed)})")
    print("sync_config configured -> " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
