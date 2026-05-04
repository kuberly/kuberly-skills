#!/usr/bin/env bash
# sync_mcp.sh — copy kuberly-platform MCP Python package into the consumer's scripts/mcp/kuberly-platform/.
#
# APM does not deploy arbitrary paths from kuberly-skills into the consumer tree; the full
# package is available under apm_modules/kuberly/kuberly-skills/ after `apm install`.
# This script materializes the MCP entrypoint next to other repo scripts so .cursor/mcp.json
# and .mcp.json can use stable paths (${workspaceFolder}/scripts/mcp/kuberly-platform/...).
#
# Idempotent. Run after `apm install` (typically from ensure-apm-skills pre-commit hook).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "sync_mcp.sh: not in a git repository — skipping" >&2
  exit 0
fi

SRC="$REPO_ROOT/apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform"
DST="$REPO_ROOT/scripts/mcp/kuberly-platform"

if [[ ! -d "$SRC" ]]; then
  echo "sync_mcp.sh: $SRC missing — run 'apm install' first" >&2
  exit 0
fi

rm -rf "$DST"
mkdir -p "$DST"

count=0
shopt -s nullglob
for f in "$SRC"/*.py "$SRC"/README.md; do
  [[ -f "$f" ]] || continue
  cp "$f" "$DST/"
  count=$((count + 1))
done
shopt -u nullglob

echo "sync_mcp.sh: synced kuberly-platform MCP to $DST ($count file(s))"
