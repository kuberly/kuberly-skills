#!/usr/bin/env bash
# post_apm_install.sh — run after `apm install` to wire kuberly-skills into
# the consumer's runtime configs. Idempotent. Stdlib-only assumptions.
#
# Consumers ship a tiny bootstrap (`scripts/ensure_apm_skills.sh`) that runs
# `apm install` and then `exec`s this script. Centralizing the post-install
# work here means a release of kuberly-skills can change the wiring without
# every consumer editing their own bootstrap.
#
# What this does:
#   1. Sync persona files into .claude/agents/ and .cursor/agents/
#   2. Merge canonical hook + MCP entries into .claude/settings.json,
#      .mcp.json, .cursor/hooks.json, .cursor/mcp.json
#   3. Report apm.lock.yaml drift (exit 1 if changed since last run)
#
# Env: KUBERLY_LOCK_BEFORE — set by the consumer bootstrap to the apm.lock.yaml
# contents before `apm install` ran. Used for the drift check.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "post_apm_install: not in a git repo — skip" >&2
  exit 0
fi

PKG="$ROOT/apm_modules/kuberly/kuberly-skills"
if [[ ! -d "$PKG" ]]; then
  echo "post_apm_install: $PKG missing — apm install first" >&2
  exit 0
fi

# 1. Persona sync (writes to .claude/agents/ and .cursor/agents/)
SYNC_AGENTS="$PKG/scripts/sync_agents.sh"
[[ -x "$SYNC_AGENTS" ]] && bash "$SYNC_AGENTS"

# 2. Hook + MCP wiring (writes to four runtime config files)
SYNC_CLAUDE="$PKG/scripts/sync_claude_config.py"
[[ -f "$SYNC_CLAUDE" ]] && python3 "$SYNC_CLAUDE"

# 3. Lockfile drift report — only if caller passed KUBERLY_LOCK_BEFORE
LOCK="$ROOT/apm.lock.yaml"
if [[ -n "${KUBERLY_LOCK_BEFORE:-}" && -f "$LOCK" ]]; then
  if [[ "$KUBERLY_LOCK_BEFORE" != "$(cat "$LOCK")" ]]; then
    echo "post_apm_install: apm.lock.yaml changed — git add apm.lock.yaml && commit" >&2
    exit 1
  fi
fi

exit 0
