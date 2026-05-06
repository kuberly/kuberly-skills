#!/usr/bin/env bash
# sync_agents.sh — copy kuberly-skills agent personas into the consumer's agent dirs.
#
# APM does not deploy `agents/*.md` natively (it ships skills, hooks, prompts). This script
# bridges the gap: after `apm install` populates `apm_modules/kuberly/kuberly-skills/agents/`,
# run this to materialize persona files where the supported runtimes read custom subagent
# definitions:
#   - Claude Code (`.claude/agents/`) — Claude Code frontmatter (`tools:` string).
#   - Cursor      (`.cursor/agents/`) — same Claude Code frontmatter.
#   - opencode    (`.opencode/agents/`) — opencode-native frontmatter (`mode: subagent`).
#
# Two source trees:
#   - `agents/`           → claude/cursor (string `tools:`).
#   - `agents-opencode/`  → opencode (`mode: subagent`, no `tools:` string).
#
# Idempotent. Run on every `apm install` (typically from the ensure-apm-skills
# pre-commit hook in the consumer repo).
set -euo pipefail

# Find the consumer repo root via git.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "sync_agents.sh: not in a git repository — skipping" >&2
  exit 0
fi

PKG_ROOT="$REPO_ROOT/apm_modules/kuberly/kuberly-skills"
SRC_CLAUDE="$PKG_ROOT/agents"
SRC_OPENCODE="$PKG_ROOT/agents-opencode"

if [[ ! -d "$SRC_CLAUDE" ]]; then
  echo "sync_agents.sh: $SRC_CLAUDE missing — run 'apm install' first" >&2
  exit 0
fi

# sync_pair <src-dir> <dst-dir> <label>
sync_pair() {
  local src="$1" dst="$2" label="$3"
  mkdir -p "$dst"
  local count=0
  for src_file in "$src"/*.md; do
    [[ -f "$src_file" ]] || continue
    local name dst_file
    name="$(basename "$src_file")"
    dst_file="$dst/$name"
    if ! cmp -s "$src_file" "$dst_file" 2>/dev/null; then
      cp "$src_file" "$dst_file"
      count=$((count + 1))
    fi
  done
  if [[ $count -gt 0 ]]; then
    echo "sync_agents.sh: synced $count persona file(s) to $dst ($label)" >&2
  fi
  printf '%s' "$count"
}

total=0
for dst in "$REPO_ROOT/.claude/agents" "$REPO_ROOT/.cursor/agents"; do
  c="$(sync_pair "$SRC_CLAUDE" "$dst" "claude/cursor")"
  total=$((total + c))
done

if [[ -d "$SRC_OPENCODE" ]]; then
  c="$(sync_pair "$SRC_OPENCODE" "$REPO_ROOT/.opencode/agents" "opencode")"
  total=$((total + c))
else
  echo "sync_agents.sh: $SRC_OPENCODE missing — opencode personas not synced" >&2
fi

if [[ $total -eq 0 ]]; then
  echo "sync_agents.sh: personas already up to date (claude/cursor/opencode)"
fi
