#!/usr/bin/env bash
# sync_mcp.sh — copy kuberly-skills MCP servers into the consumer repo.
#
# APM ships skills, hooks, and prompts; it does not natively deploy executable
# Python packages alongside them. This script bridges the gap: after `apm install`
# populates `apm_modules/kuberly/kuberly-skills/mcp/`, run this to materialize the
# MCP servers under `scripts/mcp/<server>/` in the consumer repo, where Claude
# Code's `.mcp.json` and Cursor's MCP config both expect them.
#
# Currently mirrors:
#   apm_modules/kuberly/kuberly-skills/mcp/kuberly-graph/  →  scripts/mcp/kuberly-graph/
#
# The consumer keeps a tiny `scripts/kuberly_graph.py` shim that exec's the
# materialized server, so SessionStart hooks and ad-hoc CLI invocations keep
# working regardless of where the real file lives.
#
# Idempotent. Run on every `apm install` (typically from the ensure-apm-skills
# pre-commit hook in the consumer repo — see consumer-side
# scripts/ensure_apm_skills.sh).
set -euo pipefail

# Find the consumer repo root via git.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "sync_mcp.sh: not in a git repository — skipping" >&2
  exit 0
fi

SRC_BASE="$REPO_ROOT/apm_modules/kuberly/kuberly-skills/mcp"
DST_BASE="$REPO_ROOT/scripts/mcp"

if [[ ! -d "$SRC_BASE" ]]; then
  echo "sync_mcp.sh: $SRC_BASE missing — run 'apm install' first" >&2
  exit 0
fi

mkdir -p "$DST_BASE"

# Copy every MCP server directory under mcp/<name>/ wholesale.
synced=0
for src_dir in "$SRC_BASE"/*/; do
  [[ -d "$src_dir" ]] || continue
  name="$(basename "$src_dir")"
  dst_dir="$DST_BASE/$name"
  mkdir -p "$dst_dir"

  # Sync only Python files + a few standard data files; skip __pycache__ etc.
  changed_in_server=0
  while IFS= read -r -d '' src_file; do
    rel="${src_file#"$src_dir"}"
    dst_file="$dst_dir/$rel"
    mkdir -p "$(dirname "$dst_file")"
    if ! cmp -s "$src_file" "$dst_file" 2>/dev/null; then
      cp "$src_file" "$dst_file"
      changed_in_server=$((changed_in_server + 1))
    fi
  done < <(find "$src_dir" \
              \( -name '__pycache__' -prune \) -o \
              \( -type f \( -name '*.py' -o -name '*.json' -o -name '*.md' \
                            -o -name 'requirements*.txt' \) -print0 \))

  if [[ $changed_in_server -gt 0 ]]; then
    synced=$((synced + 1))
    echo "sync_mcp.sh: synced $changed_in_server file(s) into $dst_dir"
  fi
done

if [[ $synced -eq 0 ]]; then
  : # already up to date — stay quiet so pre-commit logs aren't noisy
fi
