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
#   3. Ensure the pre-commit framework's git hook is installed (so the
#      consumer's .pre-commit-config.yaml entries — including
#      ensure-apm-skills — actually fire on commits)
#   4. Report apm.lock.yaml drift (exit 1 if changed since last run)
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

# 3. Pre-commit framework: ensure git hook is installed.
# The consumer's .pre-commit-config.yaml lists ensure-apm-skills (which calls
# this script). For the entry to actually fire on `git commit`, pre-commit
# must have written .git/hooks/pre-commit. New clones rarely do this manually;
# self-heal here so every `apm install` makes future commits auto-sync.
PCC="$ROOT/.pre-commit-config.yaml"
GIT_DIR="$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)"
PRE_HOOK="$ROOT/$GIT_DIR/hooks/pre-commit"
if [[ -f "$PCC" ]]; then
  needs_install=1
  if [[ -f "$PRE_HOOK" ]] && grep -q "pre-commit" "$PRE_HOOK" 2>/dev/null; then
    needs_install=0
  fi
  if [[ "$needs_install" == "1" ]]; then
    if command -v pre-commit >/dev/null 2>&1; then
      ( cd "$ROOT" && pre-commit install --install-hooks ) >&2 \
        && echo "post_apm_install: pre-commit git hook installed"
    else
      echo "post_apm_install: pre-commit CLI not found — install with 'pip install pre-commit' or 'brew install pre-commit', then run 'pre-commit install'" >&2
    fi
  fi
fi

# 4. Lockfile drift report — only if caller passed KUBERLY_LOCK_BEFORE
LOCK="$ROOT/apm.lock.yaml"
if [[ -n "${KUBERLY_LOCK_BEFORE:-}" && -f "$LOCK" ]]; then
  if [[ "$KUBERLY_LOCK_BEFORE" != "$(cat "$LOCK")" ]]; then
    echo "post_apm_install: apm.lock.yaml changed — git add apm.lock.yaml && commit" >&2
    exit 1
  fi
fi

exit 0
