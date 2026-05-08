#!/usr/bin/env python3
"""sync_claude_config.py — merge kuberly-skills wiring into consumer config.

APM has fixed semantics for hook + MCP deploy that don't fully reach Claude
Code's project-scope config (`.claude/settings.json` + `.mcp.json`) and
leaves Cursor's hook file empty. This script bridges those gaps: after
`apm install` lands kuberly-skills under `apm_modules/kuberly/kuberly-skills/`,
it merges canonical entries — pointing at that apm cache path — into:

  - `.claude/settings.json`  (Claude Code hooks)
  - `.mcp.json`              (Claude Code project-scope MCP servers)
  - `.cursor/hooks.json`     (Cursor hooks: beforeSubmitPrompt + sessionStart graph refresh)
  - `.cursor/mcp.json`       (Cursor project-scope MCP servers, stdio transport)

Canonical Cursor rules ship under **kuberly-skills** `.apm/cursor/rules/` and are copied into
`.cursor/rules/` by **`scripts/sync_cursor_rules.sh`** (invoked from **`post_apm_install.sh`**).

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

def _hooks_block_claude() -> dict[str, list[dict[str, Any]]]:
    """Hooks owned for Claude Code (`.claude/settings.json`).

    Claude uses the ``UserPromptSubmit`` event. SessionStart-on-every-launch
    graph regen was dropped in v0.13.0 (graph is refreshed in post_apm_install).
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


def _hooks_block_cursor() -> dict[str, list[dict[str, Any]]]:
    """Hooks owned for Cursor (`.cursor/hooks.json`).

    Cursor 0.4x+ expects ``beforeSubmitPrompt`` (``UserPromptSubmit`` is rejected).
    Each list entry must be a **flat** object with string ``command`` (not a
    nested ``hooks`` array — Cursor validates ``beforeSubmitPrompt[0].command``).
    """
    return {
        "beforeSubmitPrompt": [
            {
                "command": (
                    f"python3 {APM_CACHE_PATH}/scripts/hooks/orchestrator_route.py"
                ),
                "timeout": 5,
            }
        ],
    }


def _mcp_server_claude() -> dict[str, Any]:
    """kuberly-platform MCP server for Claude Code project-scope `.mcp.json`.

    Uses ``.venv-mcp/bin/python3`` (created by ``ensure_mcp_venv.sh``) so the
    PyPI ``mcp`` package is available even when system ``python3`` lacks it.
    Cwd for MCP is the project root, so this relative path resolves.
    """
    return {
        "command": ".venv-mcp/bin/python3",
        "args": [
            f"{APM_CACHE_PATH}/mcp/kuberly-platform/kuberly_platform.py",
            "mcp",
            "--repo",
            ".",
        ],
    }


def _mcp_server_cursor() -> dict[str, Any]:
    """kuberly-platform MCP server for Cursor `.cursor/mcp.json`.

    Cursor requires **stdio** MCP servers (`type: stdio`). We use
    ``${workspaceFolder}/.venv-mcp/bin/python3`` so the PyPI ``mcp`` package is
    present (``ensure_mcp_venv.sh``); ``${workspaceFolder}`` expands for the
    script path and ``--repo``.
    """
    return {
        "type": "stdio",
        "command": "${workspaceFolder}/.venv-mcp/bin/python3",
        "args": [
            "${workspaceFolder}/apm_modules/kuberly/kuberly-skills/mcp/"
            "kuberly-platform/kuberly_platform.py",
            "mcp",
            "--repo",
            "${workspaceFolder}",
        ],
    }


def _mcp_server_graph_claude() -> dict[str, Any]:
    """kuberly-graph MCP server for Claude Code project-scope `.mcp.json`.

    The package ships under `mcp/kuberly-graph/` in the apm cache; consumers
    `pip install -e` it into a local venv at
    `apm_modules/kuberly/kuberly-skills/mcp/kuberly-graph/.venv/`. The console
    script `kuberly-graph` is the entry point; `serve --transport stdio` is
    Claude Code's project-scope contract.
    """
    return {
        "command": (
            f"{APM_CACHE_PATH}/mcp/kuberly-graph/.venv/bin/kuberly-graph"
        ),
        "args": ["serve", "--transport", "stdio", "--repo", "."],
    }


def _mcp_server_graph_cursor() -> dict[str, Any]:
    """kuberly-graph MCP server for Cursor `.cursor/mcp.json`.

    Same package as Claude's entry; Cursor needs `type: stdio` and
    `${workspaceFolder}` expansion for the binary and the ``--repo`` flag.
    """
    return {
        "type": "stdio",
        "command": (
            "${workspaceFolder}/" + APM_CACHE_PATH
            + "/mcp/kuberly-graph/.venv/bin/kuberly-graph"
        ),
        "args": [
            "serve",
            "--transport",
            "stdio",
            "--repo",
            "${workspaceFolder}",
        ],
    }


_SESSION_START_COMMAND = (
    "sh apm_modules/kuberly/kuberly-skills/scripts/hooks/"
    "refresh_kuberly_graph_cursor_session.sh"
)


def _session_start_entry_owned(entry: Any) -> bool:
    """True if this sessionStart hook was installed by kuberly-skills (or legacy paths)."""
    if not isinstance(entry, dict):
        return False
    cmd = entry.get("command", "")
    if not isinstance(cmd, str):
        return False
    needles = (
        "refresh_kuberly_graph_cursor_session",
        "refresh_kuberly_graph_session.sh",
        f"{APM_CACHE_PATH}/mcp/kuberly-platform/kuberly_platform.py",
        "kuberly_platform.py generate",
        "scripts/kuberly_graph.py generate",
    )
    return any(n in cmd for n in needles)


def _merge_cursor_session_start_hooks(out: dict[str, Any]) -> None:
    """Merge Cursor `sessionStart` hooks (flat list shape, not UserPromptSubmit matchers)."""
    out.setdefault("hooks", {})
    hooks = out["hooks"]
    if not isinstance(hooks, dict):
        return
    current = hooks.get("sessionStart", [])
    if not isinstance(current, list):
        current = []
    filtered = [e for e in current if not _session_start_entry_owned(e)]
    canonical = {"command": _SESSION_START_COMMAND, "timeout": 120}
    hooks["sessionStart"] = filtered + [canonical]


# --- merge helpers ----------------------------------------------------------

def _is_kuberly_owned_command(cmd: str, status: str = "") -> bool:
    """A command is ours if it contains any KUBERLY_OWNED_MARKERS substring,
    in either the command itself or its statusMessage."""
    haystack = (cmd or "") + " " + (status or "")
    return any(m in haystack for m in KUBERLY_OWNED_MARKERS)


def _matcher_is_kuberly_owned(matcher: Any) -> bool:
    """A matcher is owned if every command it contains is recognizably ours.

    Supports (1) Claude-style nested ``{ "hooks": [ { "command": ... } ] }``
    matchers and (2) Cursor flat entries ``{ "command": "...", "timeout": … }``.

    Catches the current apm-cache layout (`mcp/kuberly-graph/`,
    `mcp/kuberly-platform/`) AND legacy hand-wired layouts
    (vendored scripts/kuberly_graph.py, scripts/mcp/kuberly-graph/, ...).
    Mixed matchers (some kuberly, some user) are left alone — safer to
    over-preserve than to delete user hooks.
    """
    if not isinstance(matcher, dict):
        return False
    hooks = matcher.get("hooks")
    if isinstance(hooks, list) and hooks:
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
    # Cursor flat hook entry (beforeSubmitPrompt / sessionStart style)
    cmd = matcher.get("command", "")
    if isinstance(cmd, str) and cmd:
        status = matcher.get("statusMessage", "")
        if not isinstance(status, str):
            status = ""
        return _is_kuberly_owned_command(cmd, status)
    return False


def _merge_hooks_file(existing: dict[str, Any]) -> dict[str, Any]:
    """Merge kuberly hooks into Claude Code ``.claude/settings.json`` (idempotent).

    Events ever owned by kuberly-skills are cleaned even if we no longer
    add hooks to them (so the v0.13.0 drop of SessionStart actually
    removes legacy entries from upgraded consumers).
    """
    HISTORICAL_EVENTS = ("SessionStart", "UserPromptSubmit")
    out = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    out.setdefault("hooks", {})
    if not isinstance(out["hooks"], dict):
        return existing  # something weird — refuse to clobber
    new_block = _hooks_block_claude()
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
    """Merge hooks for Cursor ``.cursor/hooks.json``.

    Cursor 3.x requires top-level ``version: 1``. Cursor's supported
    pre-submit hook name is ``beforeSubmitPrompt`` (not Claude's
    ``UserPromptSubmit``); we migrate by stripping kuberly-owned matchers
    from both keys and installing the canonical block on
    ``beforeSubmitPrompt`` only.

    ``sessionStart`` is merged separately (graph refresh shell hook).
    """
    out = json.loads(json.dumps(existing))
    out.setdefault("hooks", {})
    if not isinstance(out["hooks"], dict):
        return existing
    cursor_block = _hooks_block_cursor()
    # Clean legacy Cursor key + install canonical beforeSubmitPrompt.
    for event in ("UserPromptSubmit", "beforeSubmitPrompt"):
        current = out["hooks"].get(event, [])
        if not isinstance(current, list):
            current = []
        filtered = [m for m in current if not _matcher_is_kuberly_owned(m)]
        kuberly_matchers = cursor_block.get(event, [])
        merged = filtered + kuberly_matchers
        if merged:
            out["hooks"][event] = merged
        else:
            out["hooks"].pop(event, None)
    version = existing.get("version") if isinstance(existing, dict) else None
    if not isinstance(version, (int, str)):
        version = 1
    ordered: dict[str, Any] = {"version": version}
    for k, v in out.items():
        if k == "version":
            continue
        ordered[k] = v
    _merge_cursor_session_start_hooks(ordered)
    return ordered


def _merge_mcp_file(
    existing: dict[str, Any],
    server_entries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge canonical kuberly-skills MCP servers into an mcpServers dict (idempotent).

    `server_entries` is a `{name: entry}` map — currently `kuberly-platform`
    (v0.12.0+) and `kuberly-graph` (v0.44.0+, FastMCP package shipped under
    `mcp/kuberly-graph/`). Both names are treated as canonical: if either
    already exists with a non-kuberly command, this function still
    overwrites with the canonical entry (consistent with how
    `kuberly-platform` was always handled).
    """
    out = json.loads(json.dumps(existing))
    out.setdefault("mcpServers", {})
    if not isinstance(out["mcpServers"], dict):
        return existing
    for name, entry in server_entries.items():
        out["mcpServers"][name] = entry
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
             lambda d: _merge_mcp_file(d, {
                 "kuberly-platform": _mcp_server_claude(),
                 "kuberly-graph": _mcp_server_graph_claude(),
             }),
             ".mcp.json"),
        ]),
        ("cursor", [
            (root / ".cursor" / "hooks.json",
             {"version": 1, "hooks": {}},
             lambda d: _merge_cursor_hooks_file(d),
             ".cursor/hooks.json"),
            (root / ".cursor" / "mcp.json",
             {"mcpServers": {}},
             lambda d: _merge_mcp_file(d, {
                 "kuberly-platform": _mcp_server_cursor(),
                 "kuberly-graph": _mcp_server_graph_cursor(),
             }),
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
