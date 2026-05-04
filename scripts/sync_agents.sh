#!/usr/bin/env bash
# sync_agents.sh — copy kuberly-skills agent personas into the consumer's agent dirs.
#
# APM does not deploy `agents/*.md` natively (it ships skills, hooks, prompts). This script
# bridges the gap: after `apm install` populates `apm_modules/kuberly/kuberly-skills/agents/`,
# run this to materialize persona files where **Claude Code** (`.claude/agents/`) and
# **Cursor** (`.cursor/agents/`) read custom subagent definitions.
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

SRC="$REPO_ROOT/apm_modules/kuberly/kuberly-skills/agents"

if [[ ! -d "$SRC" ]]; then
  echo "sync_agents.sh: $SRC missing — run 'apm install' first" >&2
  exit 0
fi

total=0
for DST in "$REPO_ROOT/.claude/agents" "$REPO_ROOT/.cursor/agents"; do
  mkdir -p "$DST"
  count=0
  for src_file in "$SRC"/*.md; do
    [[ -f "$src_file" ]] || continue
    name="$(basename "$src_file")"
    dst_file="$DST/$name"
    if ! cmp -s "$src_file" "$dst_file" 2>/dev/null; then
      cp "$src_file" "$dst_file"
      count=$((count + 1))
    fi
  done
  total=$((total + count))
  if [[ $count -gt 0 ]]; then
    echo "sync_agents.sh: synced $count persona file(s) to $DST"
  fi
done

if [[ $total -eq 0 ]]; then
  echo "sync_agents.sh: personas already up to date (.claude/agents and .cursor/agents)"
fi
