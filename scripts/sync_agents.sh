#!/usr/bin/env bash
# sync_agents.sh — copy kuberly-skills agent personas into the consumer's .claude/agents/.
#
# APM does not deploy `agents/*.md` to `.claude/agents/<name>.md` natively (it ships
# skills, hooks, prompts). This script bridges the gap: after `apm install` populates
# `apm_modules/kuberly/kuberly-skills/agents/`, run this to materialize the persona
# files where Claude Code looks for subagent definitions.
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
DST="$REPO_ROOT/.claude/agents"

if [[ ! -d "$SRC" ]]; then
  echo "sync_agents.sh: $SRC missing — run 'apm install' first" >&2
  exit 0
fi

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

if [[ $count -gt 0 ]]; then
  echo "sync_agents.sh: synced $count persona file(s) to $DST"
fi
