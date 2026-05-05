#!/usr/bin/env bash
# post_apm_install.sh — run after `apm install` to wire kuberly-skills into
# the consumer's runtime configs. Idempotent. Stdlib-only assumptions.
#
# Consumers ship a tiny bootstrap (`scripts/ensure_apm_skills.sh`) that runs
# `apm install` and then runs this script. Centralizing the post-install
# work here means a release of kuberly-skills can change the wiring without
# every consumer editing their own bootstrap.
#
# What this does:
#   1. Sync persona files into .claude/agents/ and .cursor/agents/
#   1b. Ensure ``.venv-mcp`` (PyPI ``mcp``) for stdio MCP when system python
#       does not have ``mcp`` installed.
#   2. Merge canonical hook + MCP entries into .claude/settings.json,
#      .mcp.json, .cursor/hooks.json, .cursor/mcp.json
#   2b. Copy canonical Cursor rules from .apm/cursor/rules/ -> .cursor/rules/
#   2c. Copy slash commands from .apm/cursor/commands/ -> .cursor/commands/ and
#       .claude/commands/ (same files for Cursor + Claude Code); prune *.md
#       removed from the package so stale prompts do not linger.
#   3. Ensure the pre-commit framework's git hook is installed (so the
#      consumer's .pre-commit-config.yaml entries — including
#      ensure-apm-skills — actually fire on commits)
#   4. Refresh `.kuberly/graph.json` (and sibling artifacts) by running the
#      kuberly-platform graph generator when **not** inside pre-commit.
#      (Pre-commit sets PRE_COMMIT=1.) That avoids rewriting tracked
#      `.kuberly/*.mmd` on unrelated commits and speeds hooks. Run
#      `python3 …/kuberly_platform.py generate …` from MCP or CI when
#      you need a fresh graph. Override: KUBERLY_GRAPH_ON_HOOK=1 forces
#      generation during hooks; KUBERLY_SKIP_GRAPH_ON_HOOK=1 skips even
#      outside pre-commit.
#   5. Report apm.lock.yaml drift (exit 1 if dependency resolution changed).
#
# Env: KUBERLY_LOCK_BEFORE_PATH — temp copy of apm.lock.yaml before `apm install`
# (set by ensure_apm_skills.sh). Used for drift check and to restore bytes when
# only generated_at changes.

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

# 1b. MCP stdio dependency (PyPI ``mcp``) — dedicated venv at repo root.
ENSURE_MCP_VENV="$PKG/scripts/ensure_mcp_venv.sh"
[[ -f "$ENSURE_MCP_VENV" ]] && bash "$ENSURE_MCP_VENV"

# 2. Hook + MCP wiring (writes to four runtime config files)
SYNC_CLAUDE="$PKG/scripts/sync_claude_config.py"
[[ -f "$SYNC_CLAUDE" ]] && python3 "$SYNC_CLAUDE"

RULES_SYNC="$PKG/scripts/sync_cursor_rules.sh"
[[ -f "$RULES_SYNC" ]] && bash "$RULES_SYNC"

CMD_SYNC="$PKG/scripts/sync_agent_commands.sh"
[[ -f "$CMD_SYNC" ]] && bash "$CMD_SYNC"

# 3. Pre-commit framework: ensure git hook is installed.
# The consumer's .pre-commit-config.yaml lists ensure-apm-skills (which calls
# this script). For the entry to actually fire on `git commit`, pre-commit
# must have written a hook script. New clones rarely run `pre-commit install`
# manually; self-heal here so every `apm install` makes future commits sync.
#
# Three cases for where the hook can live:
#   a. Default — .git/hooks/pre-commit (pre-commit's auto-install location)
#   b. Custom — core.hooksPath is set (e.g. .githooks/) and that path's
#      pre-commit script invokes the framework. Pre-commit refuses to
#      auto-install over core.hooksPath; treat this as already-wired if the
#      custom hook calls `pre-commit`.
#   c. Missing — neither (a) nor (b); install if pre-commit CLI present.
PCC="$ROOT/.pre-commit-config.yaml"
if [[ -f "$PCC" ]]; then
  HOOKS_PATH="$(git -C "$ROOT" config --get core.hooksPath 2>/dev/null || true)"

  if [[ -n "$HOOKS_PATH" ]]; then
    # case (b): custom hooksPath. Don't fight it — verify the user's hook
    # actually invokes pre-commit, otherwise warn.
    CUSTOM_HOOK="$ROOT/$HOOKS_PATH/pre-commit"
    if [[ -f "$CUSTOM_HOOK" ]] && grep -q "pre-commit" "$CUSTOM_HOOK" 2>/dev/null; then
      :  # silently OK — user's custom hook already calls pre-commit
    else
      echo "post_apm_install: core.hooksPath=$HOOKS_PATH but '$CUSTOM_HOOK' does not invoke pre-commit. Wire it manually or 'git config --unset-all core.hooksPath'." >&2
    fi
  else
    # case (a) or (c): default location.
    GIT_DIR="$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)"
    PRE_HOOK="$ROOT/$GIT_DIR/hooks/pre-commit"
    if [[ ! -f "$PRE_HOOK" ]] || ! grep -q "pre-commit" "$PRE_HOOK" 2>/dev/null; then
      if command -v pre-commit >/dev/null 2>&1; then
        ( cd "$ROOT" && pre-commit install --install-hooks ) >&2 \
          && echo "post_apm_install: pre-commit git hook installed"
      else
        echo "post_apm_install: pre-commit CLI not found — 'pip install pre-commit' or 'brew install pre-commit', then 'pre-commit install'" >&2
      fi
    fi
  fi
fi

# 4. Refresh kuberly-graph cache so MCP can read it on next session.
# Runs only if root.hcl exists (kuberly-stack repo marker). Silent on
# success — emits the one-line stats banner from kuberly_platform.py.
GRAPH_GEN="$PKG/mcp/kuberly-platform/kuberly_platform.py"
if [[ -f "$GRAPH_GEN" && -f "$ROOT/root.hcl" ]]; then
  if [[ "${KUBERLY_SKIP_GRAPH_ON_HOOK:-}" == "1" ]]; then
    :
  elif [[ "${PRE_COMMIT:-}" == "1" && "${KUBERLY_GRAPH_ON_HOOK:-}" != "1" ]]; then
    :
  else
    mkdir -p "$ROOT/.kuberly"
    python3 "$GRAPH_GEN" generate "$ROOT" -o "$ROOT/.kuberly" 2>&1 \
      | sed 's/^/graph: /' || true
  fi
fi

# 5. Lockfile drift report — only if caller passed KUBERLY_LOCK_BEFORE_PATH.
# Ignore generated_at for *comparison*. If semantic content is unchanged but
# bytes differ (usually generated_at), restore the snapshot so pre-commit does
# not fail on timestamp-only churn.
_lock_ignore_generated_at() {
  grep -v '^generated_at:' "$1" 2>/dev/null || true
}
LOCK="$ROOT/apm.lock.yaml"
if [[ -n "${KUBERLY_LOCK_BEFORE_PATH:-}" && -f "${KUBERLY_LOCK_BEFORE_PATH}" && -f "$LOCK" ]]; then
  before_sem="$(_lock_ignore_generated_at "$KUBERLY_LOCK_BEFORE_PATH")"
  after_sem="$(_lock_ignore_generated_at "$LOCK")"
  if [[ "$before_sem" != "$after_sem" ]]; then
    echo "post_apm_install: apm.lock.yaml changed — git add apm.lock.yaml && commit" >&2
    exit 1
  fi
  if ! cmp -s "$KUBERLY_LOCK_BEFORE_PATH" "$LOCK"; then
    cp "$KUBERLY_LOCK_BEFORE_PATH" "$LOCK"
  fi
fi

exit 0
