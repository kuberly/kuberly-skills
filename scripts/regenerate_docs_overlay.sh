#!/usr/bin/env bash
# regenerate_docs_overlay — pre-commit hook entry that refreshes
# kuberly/docs_overlay.json incrementally (only changed docs are
# re-extracted; embeddings reused via content_sha).
#
# Wire into the consumer's .pre-commit-config.yaml:
#
#   - repo: local
#     hooks:
#       - id: regenerate-docs-overlay
#         name: Refresh kuberly/docs_overlay.json
#         entry: bash apm_modules/kuberly/kuberly-skills/scripts/regenerate_docs_overlay.sh
#         language: system
#         pass_filenames: false
#         files: '\.(md|json)$'
#
# Embeddings are off by default (so the hook needs no API key and runs
# offline). Set KUBERLY_DOCS_EMBED=openai (and OPENAI_API_KEY) in your
# shell to also (re-)embed changed files.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
SCRIPT="${REPO_ROOT}/apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/docs_graph.py"

if [[ ! -f "$SCRIPT" ]]; then
    # apm install hasn't run yet; not our problem to bootstrap.
    exit 0
fi

cd "$REPO_ROOT"

EMBED_ARG=""
if [[ -n "${KUBERLY_DOCS_EMBED:-}" ]]; then
    EMBED_ARG="--embed"
fi

python3 "$SCRIPT" generate $EMBED_ARG "$@"
