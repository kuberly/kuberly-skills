#!/usr/bin/env bash
# ensure_mcp_venv.sh — workspace-local venv with PyPI `mcp` for stdio MCP.
#
# Cursor / Claude Code often invoke MCP with a bare ``python3`` that does not
# have ``mcp`` installed. This script creates ``<repo>/.venv-mcp`` (gitignored
# in consumer repos) and ``pip install -r …/requirements-mcp.txt``.
#
# Idempotent: safe to run on every ``post_apm_install``. Stdlib venv only;
# requires ``python3`` and ``pip`` usable from that interpreter.
#
# Usage (consumer repo root):
#   bash apm_modules/kuberly/kuberly-skills/scripts/ensure_mcp_venv.sh

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${ROOT}" ]]; then
  echo "ensure_mcp_venv: not inside a git repo — skip" >&2
  exit 0
fi

PKG="${ROOT}/apm_modules/kuberly/kuberly-skills"
REQ="${PKG}/mcp/kuberly-platform/requirements-mcp.txt"
VENV="${ROOT}/.venv-mcp"

if [[ ! -f "${REQ}" ]]; then
  echo "ensure_mcp_venv: ${REQ} missing — run apm install first" >&2
  exit 0
fi

if [[ ! -d "${VENV}" ]]; then
  echo "ensure_mcp_venv: creating ${VENV}" >&2
  python3 -m venv "${VENV}"
fi

# (Re)install so upgrades to requirements-mcp.txt apply.
"${VENV}/bin/pip" install -q -r "${REQ}"
echo "ensure_mcp_venv: OK (${VENV})" >&2
