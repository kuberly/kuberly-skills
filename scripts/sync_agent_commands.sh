#!/usr/bin/env bash
# Copy slash-command prompts from the apm-installed kuberly-skills package into the
# consumer's .cursor/commands/ and .claude/commands/. Same markdown is used for
# both Cursor and Claude Code (frontmatter: name, id, category, description).
# Idempotent; stdlib + coreutils only.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "sync_agent_commands: not in a git repository — skipping" >&2
  exit 0
fi

PKG="$ROOT/apm_modules/kuberly/kuberly-skills"
SRC="$PKG/.apm/cursor/commands"
if [[ ! -d "$SRC" ]]; then
  echo "sync_agent_commands: $SRC missing — apm install first" >&2
  exit 0
fi

mkdir -p "$ROOT/.cursor/commands" "$ROOT/.claude/commands"
count=0
shopt -s nullglob
for f in "$SRC"/*.md; do
  [[ -f "$f" ]] || continue
  bn="$(basename "$f")"
  cp "$f" "$ROOT/.cursor/commands/$bn"
  cp "$f" "$ROOT/.claude/commands/$bn"
  count=$((count + 1))
done
shopt -u nullglob

# Prune markdown prompts removed from the package so forks do not keep stale copies.
for dest in "$ROOT/.cursor/commands" "$ROOT/.claude/commands"; do
  [[ -d "$dest" ]] || continue
  shopt -s nullglob
  for t in "$dest"/*.md; do
    [[ -f "$t" ]] || continue
    bn="$(basename "$t")"
    if [[ ! -f "$SRC/$bn" ]]; then
      rm -f "$t"
    fi
  done
  shopt -u nullglob
done

echo "sync_agent_commands: synced $count command file(s) -> $ROOT/.cursor/commands + $ROOT/.claude/commands"
