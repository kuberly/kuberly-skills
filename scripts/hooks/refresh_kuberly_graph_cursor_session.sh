#!/bin/sh
# Cursor sessionStart hook: consume stdin JSON, refresh graph without polluting stdout,
# then emit a valid empty sessionStart response for Cursor.
#
# Invoked from the consumer repo root (see .cursor/hooks.json merged by sync_claude_config.py).
set -e
cat >/dev/null 2>&1 || true
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT" || exit 0
GEN="$ROOT/apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py"
if [ -f "$GEN" ]; then
  python3 "$GEN" generate "$ROOT" -o "$ROOT/.claude" >/dev/null 2>&1 || true
fi
printf '%s\n' '{}'
