#!/usr/bin/env bash
# Copy canonical Cursor rules from the apm-installed kuberly-skills package into the
# consumer's .cursor/rules/. Idempotent; stdlib + coreutils only.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "sync_cursor_rules: not in a git repository — skipping" >&2
  exit 0
fi

PKG="$ROOT/apm_modules/kuberly/kuberly-skills"
SRC="$PKG/.apm/cursor/rules"
if [[ ! -d "$SRC" ]]; then
  echo "sync_cursor_rules: $SRC missing — apm install first" >&2
  exit 0
fi

mkdir -p "$ROOT/.cursor/rules"
count=0
shopt -s nullglob
for f in "$SRC"/*.mdc; do
  [[ -f "$f" ]] || continue
  cp "$f" "$ROOT/.cursor/rules/"
  count=$((count + 1))
done
shopt -u nullglob

echo "sync_cursor_rules: synced $count rule file(s) -> $ROOT/.cursor/rules/"
