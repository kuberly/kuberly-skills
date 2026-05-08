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

# v0.56.0+: requirements-mcp.txt pulls in lancedb + pyarrow so the MCP
# can read kuberly-graph's LanceDB store. pyarrow has no Python 3.14
# wheel as of writing — prefer 3.12, then 3.13, before falling back to
# bare `python3`. If the existing venv is on a Python that pip then
# fails to install the deps into, recreate it on a known-good version.
pick_python() {
    for cand in python3.12 python3.13 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            echo "$cand"
            return 0
        fi
    done
    echo ""
}
NEW_PY="$(pick_python)"
if [[ -z "${NEW_PY}" ]]; then
    echo "ensure_mcp_venv: no usable python3 on PATH" >&2
    exit 1
fi

# Detect existing venv interpreter; recreate if it's >=3.14 (where
# pyarrow wheels are unavailable). Symlinked path resolves to the
# concrete pythonX.Y so a string compare is enough.
if [[ -d "${VENV}" ]]; then
    EXISTING_PY="$(readlink "${VENV}/bin/python3" 2>/dev/null || echo "")"
    if [[ "${EXISTING_PY}" == python3.14* || "${EXISTING_PY}" == python3.15* ]]; then
        echo "ensure_mcp_venv: existing ${VENV} is ${EXISTING_PY}; recreating with ${NEW_PY} (lancedb has no Python 3.14+ wheel yet)" >&2
        rm -rf "${VENV}"
    fi
fi

if [[ ! -d "${VENV}" ]]; then
    echo "ensure_mcp_venv: creating ${VENV} on ${NEW_PY}" >&2
    "${NEW_PY}" -m venv "${VENV}"
fi

# (Re)install so upgrades to requirements-mcp.txt apply.
"${VENV}/bin/pip" install -q -r "${REQ}"
echo "ensure_mcp_venv: OK (${VENV})" >&2
