#!/usr/bin/env bash
# refresh_kuberly_overlays — one command to (re)build every overlay the
# kuberly-platform MCP server reads on init.
#
# Without these files the MCP either starts with a thin static-only graph
# or refuses to start at all (graph.json missing). Running this script
# once per fresh clone (or when graphs go stale) restores the full
# cross-layer view that powers query_resources / query_k8s / find_docs.
#
# Layers (each is independent — failures soft-stop the layer, not the run):
#
#   1. static (.kuberly/graph.json)
#        Producer:   mcp/kuberly-platform/kuberly_platform.py generate
#        Needs:      nothing (reads on-disk Terragrunt + JSON sidecars)
#        Always run.
#
#   2. state  (.kuberly/state_overlay_<env>.json)
#        Producer:   mcp/kuberly-platform/state_graph.py generate-all --resources
#        Needs:      AWS creds with s3:Get/List on the Terragrunt state bucket
#                    (use `aws sso login` first; AWS_PROFILE honored)
#        Skip with:  --no-state, or set NO_STATE=1, or no AWS creds detected
#
#   3. k8s    (.kuberly/k8s_overlay_<env>.json, one per env)
#        Producer:   mcp/kuberly-platform/k8s_graph.py generate --env <env>
#        Needs:      kubectl context that talks to that env's cluster
#                    (envs are discovered from components/<env>/shared-infra.json)
#        Skip with:  --no-k8s, or set NO_K8S=1, or no kubeconfig
#        Per-env:    if `--context <name>-${env}` (or current context) can't
#                    reach the apiserver, the env is skipped, not failed
#
#   4. docs   (.kuberly/docs_overlay.json)
#        Producer:   mcp/kuberly-platform/docs_graph.py generate
#        Needs:      nothing — semantic embeddings only if KUBERLY_DOCS_EMBED=openai
#        Skip with:  --no-docs
#
# Flags:
#   --full           force rescan, ignore incremental caches (state + docs)
#   --no-state       skip layer 2
#   --no-k8s         skip layer 3
#   --no-docs        skip layer 4
#   --env <name>     only refresh the named env in layers 2 and 3
#   --modules <csv>  passed through to state_graph (allowlist for --resources)
#   -h | --help      show this help
#
# Exit code is 0 even when credential-gated layers skip — callers want
# "is the static graph ready" yes/no, not "did everything succeed".

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PKG="${REPO_ROOT}/apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform"
OVERLAY_DIR="${REPO_ROOT}/.kuberly"

# Prefer the workspace MCP venv (Python 3.14 with the right deps);
# fall back to system python3.
if [[ -x "${REPO_ROOT}/.venv-mcp/bin/python3" ]]; then
    PY="${REPO_ROOT}/.venv-mcp/bin/python3"
else
    PY="$(command -v python3)"
fi

NO_STATE="${NO_STATE:-0}"
NO_K8S="${NO_K8S:-0}"
NO_DOCS="${NO_DOCS:-0}"
FULL=0
ONLY_ENV=""
MODULES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full)         FULL=1 ;;
        --no-state)     NO_STATE=1 ;;
        --no-k8s)       NO_K8S=1 ;;
        --no-docs)      NO_DOCS=1 ;;
        --env)          ONLY_ENV="${2:?--env needs a value}"; shift ;;
        --modules)      MODULES="${2:?--modules needs a value}"; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//;/^set -euo/d'
            exit 0
            ;;
        *)
            echo "refresh_kuberly_overlays: unknown flag '$1'" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ ! -d "$PKG" ]]; then
    echo "refresh_kuberly_overlays: $PKG not found — run 'apm install' first." >&2
    exit 1
fi

mkdir -p "$OVERLAY_DIR"
cd "$REPO_ROOT"

note() { printf '\033[1;34m[overlays]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[overlays]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[overlays]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Layer 1 — static graph
# ---------------------------------------------------------------------------
note "1/4 static graph (.kuberly/graph.json)"
"$PY" "$PKG/kuberly_platform.py" generate "$REPO_ROOT" -o "$OVERLAY_DIR" >/dev/null
ok "    -> .kuberly/graph.json refreshed"

# Discover envs once from components/ (canonical signal: shared-infra.json).
discover_envs() {
    if [[ -n "$ONLY_ENV" ]]; then
        echo "$ONLY_ENV"
        return
    fi
    [[ -d "$REPO_ROOT/components" ]] || return
    for d in "$REPO_ROOT"/components/*/; do
        [[ -f "$d/shared-infra.json" ]] || continue
        basename "$d"
    done
}

# ---------------------------------------------------------------------------
# Layer 2 — state overlay (AWS creds required)
# ---------------------------------------------------------------------------
if [[ "$NO_STATE" == "1" ]]; then
    warn "2/4 state overlay skipped (--no-state / NO_STATE=1)"
else
    note "2/4 state overlay (.kuberly/state_overlay_<env>.json)"
    if ! command -v aws >/dev/null 2>&1; then
        warn "    aws CLI not found — skipping state layer"
    elif ! aws sts get-caller-identity >/dev/null 2>&1; then
        warn "    no usable AWS creds — run 'aws sso login' first; skipping"
    else
        STATE_ARGS=("generate-all" "--repo" "$REPO_ROOT" "--output-dir" "$OVERLAY_DIR" "--resources")
        [[ -n "$MODULES" ]] && STATE_ARGS+=("--modules" "$MODULES")
        if "$PY" "$PKG/state_graph.py" "${STATE_ARGS[@]}"; then
            ok "    -> state overlays refreshed"
        else
            warn "    state_graph exited non-zero — partial overlays may be present"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Layer 3 — k8s overlay (kubectl context required, per env)
# ---------------------------------------------------------------------------
if [[ "$NO_K8S" == "1" ]]; then
    warn "3/4 k8s overlay skipped (--no-k8s / NO_K8S=1)"
else
    note "3/4 k8s overlay (.kuberly/k8s_overlay_<env>.json)"
    if ! command -v kubectl >/dev/null 2>&1; then
        warn "    kubectl not found — skipping k8s layer"
    else
        envs=$(discover_envs || true)
        if [[ -z "$envs" ]]; then
            warn "    no envs found under components/ — skipping k8s layer"
        else
            for env in $envs; do
                if kubectl --request-timeout=5s --context="$env" get --raw=/version >/dev/null 2>&1; then
                    if "$PY" "$PKG/k8s_graph.py" generate --env "$env" \
                        --context "$env" \
                        --output "$OVERLAY_DIR/k8s_overlay_${env}.json" \
                        >/dev/null 2>&1; then
                        ok "    -> $env"
                    else
                        warn "    -> $env: k8s_graph errored, skipped"
                    fi
                else
                    warn "    -> $env: no kubectl context named '$env' or apiserver unreachable, skipped"
                fi
            done
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Layer 4 — docs overlay
# ---------------------------------------------------------------------------
if [[ "$NO_DOCS" == "1" ]]; then
    warn "4/4 docs overlay skipped (--no-docs / NO_DOCS=1)"
else
    note "4/4 docs overlay (.kuberly/docs_overlay.json)"
    DOCS_ARGS=("generate")
    [[ "$FULL" == "1" ]] && DOCS_ARGS+=("--full")
    [[ -n "${KUBERLY_DOCS_EMBED:-}" ]] && DOCS_ARGS+=("--embed")
    if "$PY" "$PKG/docs_graph.py" "${DOCS_ARGS[@]}" >/dev/null; then
        ok "    -> .kuberly/docs_overlay.json refreshed"
    else
        warn "    docs_graph errored, overlay may be stale"
    fi
fi

ok "done — restart the MCP server to pick up new overlays"
