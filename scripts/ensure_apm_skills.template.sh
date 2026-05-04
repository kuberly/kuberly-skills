#!/usr/bin/env bash
# ensure_apm_skills.sh — minimal consumer-side bootstrap.
#
# Each consumer repo (kuberly-stack, customer fork, etc.) copies this file to
# `scripts/ensure_apm_skills.sh` and wires it into their pre-commit config.
# All real work happens in `apm_modules/kuberly/kuberly-skills/scripts/post_apm_install.sh`,
# so updates flow through `apm install` — no per-consumer script edits.
#
# What this stub does:
#   1. Run `apm install` if apm.yml is present and `apm` CLI is available
#   2. Delegate to the post-install script shipped by kuberly-skills
#
# Skip with KUBERLY_SKIP_APM_SYNC=1.

set -euo pipefail

[[ "${KUBERLY_SKIP_APM_SYNC:-}" == "1" ]] && exit 0

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -z "$ROOT" || ! -f "$ROOT/apm.yml" ]] && exit 0

# Snapshot lockfile so post_apm_install.sh can detect drift.
LOCK="$ROOT/apm.lock.yaml"
if [[ -f "$LOCK" ]]; then
  export KUBERLY_LOCK_BEFORE
  KUBERLY_LOCK_BEFORE="$(cat "$LOCK")"
fi

# APM deploys skills only into existing layouts — make sure .claude exists.
mkdir -p "$ROOT/.claude/skills"

if command -v apm >/dev/null 2>&1; then
  ( cd "$ROOT" && apm install )
else
  echo "ensure_apm_skills: apm CLI not found — install APM first" >&2
fi

# Delegate to kuberly-skills (does sync_agents + sync_claude_config + drift check).
POST="$ROOT/apm_modules/kuberly/kuberly-skills/scripts/post_apm_install.sh"
if [[ -x "$POST" ]]; then
  exec bash "$POST"
fi

exit 0
