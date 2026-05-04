#!/usr/bin/env python3
"""sync_claude_config.py — merge kuberly-skills wiring into consumer Claude Code config.

APM does not natively populate Claude Code's `.claude/settings.json` (hooks)
or project-scope `.mcp.json` (MCP servers). This script bridges that gap:
after `apm install` lands kuberly-skills under `apm_modules/kuberly/kuberly-skills/`,
it merges canonical entries — pointing at that apm cache path — into both files.

Idempotent: same input -> no-op; nothing else in either file is touched.

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

# Anything in `.claude/settings.json` whose command contains this marker is
# considered "owned by kuberly-skills" and may be replaced. The marker
# deliberately matches the apm cache path so user hooks pointing elsewhere
# (e.g. their own scripts) survive untouched.
KUBERLY_OWNED_MARKER = APM_CACHE_PATH

# --- canonical entries ------------------------------------------------------

def _kuberly_hooks() -> dict[str, list[dict[str, Any]]]:
    """The two hooks kuberly-skills owns. Returns Claude Code matcher form."""
    return {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            f'python3 "$CLAUDE_PROJECT_DIR/{APM_CACHE_PATH}'
                            f'/mcp/kuberly-graph/kuberly_graph.py" generate '
                            f'"$CLAUDE_PROJECT_DIR" -o '
                            f'"$CLAUDE_PROJECT_DIR/.claude" 2>/dev/null'
                        ),
                        "timeout": 10,
                        "statusMessage": "Refreshing kuberly-graph...",
                    }
                ]
            }
        ],
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


def _kuberly_mcp_server() -> dict[str, Any]:
    """The kuberly-graph MCP server entry for project-scope `.mcp.json`."""
    return {
        "command": "python3",
        "args": [
            f"{APM_CACHE_PATH}/mcp/kuberly-graph/kuberly_graph.py",
            "mcp",
            "--repo",
            ".",
        ],
    }


# --- merge helpers ----------------------------------------------------------

def _matcher_is_kuberly_owned(matcher: Any) -> bool:
    """A matcher is owned if every command it contains references the apm cache.

    A matcher with mixed commands (some kuberly, some user) is left alone —
    safer to over-preserve than to delete user hooks.
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
        if not isinstance(cmd, str):
            return False
        if KUBERLY_OWNED_MARKER not in cmd:
            return False
    return True


def _merge_settings(existing: dict[str, Any]) -> dict[str, Any]:
    """Return a new settings dict with kuberly hooks installed (idempotent)."""
    out = json.loads(json.dumps(existing))  # deep copy via JSON round-trip
    out.setdefault("hooks", {})
    if not isinstance(out["hooks"], dict):
        # User had something weird here; refuse to clobber.
        return existing
    for event, kuberly_matchers in _kuberly_hooks().items():
        current = out["hooks"].get(event, [])
        if not isinstance(current, list):
            current = []
        # Drop any kuberly-owned matchers — about to re-add the canonical ones.
        filtered = [m for m in current if not _matcher_is_kuberly_owned(m)]
        out["hooks"][event] = filtered + kuberly_matchers
    return out


def _merge_mcp(existing: dict[str, Any]) -> dict[str, Any]:
    """Return a new mcp dict with kuberly-graph installed (idempotent)."""
    out = json.loads(json.dumps(existing))
    out.setdefault("mcpServers", {})
    if not isinstance(out["mcpServers"], dict):
        return existing
    out["mcpServers"]["kuberly-graph"] = _kuberly_mcp_server()
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

    settings_path = root / ".claude" / "settings.json"
    mcp_path = root / ".mcp.json"

    settings_before = _load_json(settings_path, {"hooks": {}})
    mcp_before = _load_json(mcp_path, {"mcpServers": {}})

    settings_after = _merge_settings(settings_before)
    mcp_after = _merge_mcp(mcp_before)

    changed = []
    if _write_if_changed(settings_path, settings_after):
        changed.append(str(settings_path.relative_to(root)))
    if _write_if_changed(mcp_path, mcp_after):
        changed.append(str(mcp_path.relative_to(root)))

    if changed:
        print(
            "sync_claude_config: updated "
            + ", ".join(changed)
            + " (kuberly-skills hooks + kuberly-graph MCP server)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
