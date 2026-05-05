#!/usr/bin/env python3
"""
kuberly-platform: Knowledge graph generator for kuberly-stack Terragrunt/OpenTofu monorepos.

Parses components, applications, modules, and their dependencies to produce:
  - graph.json  — queryable graph structure
  - graph.html  — interactive cytoscape.js compound-node visualization
  - GRAPH_REPORT.md — summary with critical nodes, dependency chains, and cross-env drift
"""

import argparse
import base64
import json
import math
import os
import re
import string
import struct
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _b64_to_float_list(b64: str) -> list[float]:
    """Decode a base64-encoded float32 array into a Python list. Returns
    [] on any decoding failure — caller treats as "no embedding"."""
    try:
        raw = base64.b64decode(b64, validate=True)
        n = len(raw) // 4
        return list(struct.unpack(f"{n}f", raw))
    except Exception:
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Orchestration constants — used by plan_persona_fanout and friends.
# Adding a task_kind or persona is a one-dict edit.
# ---------------------------------------------------------------------------

INTEGRATION_BRANCHES = {
    "main", "master", "prod", "production",
    "dev", "stage", "staging",
    "gcp-dev", "azure-dev",
}
# Customer cluster branches like "872098898041-eu-central-1-prod"
INTEGRATION_BRANCH_RE = re.compile(r"^\d{6,}-[a-z0-9-]+-prod$")

# Path prefixes that trigger the OpenSpec gate
OPENSPEC_PATHS = ("clouds/", "components/", "applications/", "cue/")

EXPECTED_PERSONAS = {
    "agent-planner",
    "agent-infra-ops",
    "agent-sre",
    "agent-cicd",
    "agent-k8s-ops",
    # v0.14.0: cold + in-context reviewers merged into a single
    # diff-only pr-reviewer; legacy names removed.
    "pr-reviewer",
    "findings-reconciler",
    "terragrunt-plan-reviewer",
}

# Keyword scoring for task_kind inference. Lower-cased substring match.
# Customer-language keywords come first (they're how operators actually phrase
# requests). Generic phrasing follows.
KEYWORDS = {
    "incident":      ["slow", "oom", "crash", "page", "incident", "failing", "error",
                      "broken", "timeout", "down", "unhealthy", "5xx"],
    "resource-bump": ["bump", "raise", "increase", "decrease", "tune", "size",
                      "more cpu", "more memory", "more ram", "right-size", "rightsizing"],
    "new-application": ["new application", "add application", "new app", "add app",
                        "create application", "create app", "deploy a new app",
                        "scaffold app", "add backend", "add frontend", "add worker"],
    "new-database":  ["new database", "add database", "new db", "add db",
                      "new aurora", "new postgres", "new mysql", "new redis cluster",
                      "provision database"],
    "new-module":    ["scaffold", "new module", "add module", "create module", "introduce"],
    "drift-fix":     ["drift", "align", "match envs", "consistent", "sync env", "parity"],
    "cicd":          ["github actions", "codebuild", "workflow", "pipeline", "oidc",
                      "ci/cd", "ci cd", "ci yaml", "ci file", "github yaml"],
    "cleanup":       ["delete", "remove", "decommission", "drop module", "retire"],
    "plan-review":   ["review plan", "review the plan", "check plan", "check the plan",
                      "plan output", "terragrunt plan output", "review terragrunt plan",
                      "plan from pr", "plan comment", "review pr plan"],
}

# Persona DAG per task_kind. Each phase: id, personas, parallel, needs_approval.
# Approval is requested only for phases that mutate the repo.
#
# v0.14.0: review phase removed from DEFAULT DAGs.
#   Rationale: cold + in-context parallel review averaged ~43k tokens per
#   session for marginal additional signal — CI runs terraform_validate +
#   tflint on the PR and the human reviews the diff in GitHub/Bitbucket.
#   Pass `with_review=True` to plan_persona_fanout (or include "review"
#   in the task) to opt in. When opted in, a SINGLE merged pr-reviewer
#   runs (replaces the old cold + in-context pair).
_REVIEW_PHASE = [
    {"id": "review", "personas": ["pr-reviewer"],
     "parallel": False, "needs_approval": False},
]

PERSONA_DAGS = {
    "resource-bump": [
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "incident": [
        # Diagnose + scope in parallel: agent-sre looks at observability,
        # agent-k8s-ops looks at live cluster state, planner pins the
        # codebase scope. All three feed decisions.md.
        {"id": "diagnose",  "personas": ["agent-sre", "agent-k8s-ops", "agent-planner"],
         "parallel": True,  "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "new-application": [
        # Adding a new application is a CUE / applications/ JSON change
        # against existing cluster modules. Same shape as new-module but
        # the implementer touches applications/, not clouds/.
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "new-database": [
        # New DB is usually a new components/<env>/<db>.json + module reference.
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "new-module": [
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "drift-fix": [
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    "cicd": [
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-cicd"],   "parallel": False, "needs_approval": True},
    ],
    "cleanup": [
        {"id": "scope",     "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["agent-infra-ops"],       "parallel": False, "needs_approval": True},
    ],
    # Plan review: kuberly platform posts `terragrunt run plan` output as PR /
    # commit comments. The plan-reviewer reads those, checks against scope.md,
    # and signs off (or refuses). No code edits, no fanout afterwards.
    "plan-review": [
        {"id": "plan-review", "personas": ["terragrunt-plan-reviewer"],
         "parallel": False, "needs_approval": False},
    ],
    # Fallback: dispatch the planner alone, replan once scope.md exists.
    "unknown": [
        {"id": "scope", "personas": ["agent-planner"], "parallel": False, "needs_approval": False},
    ],
    # Pre-flight halt: caller named modules that don't exist as graph nodes.
    "stop-target-absent": [
        {"id": "halt", "personas": [], "parallel": False, "needs_approval": False},
    ],
    # v0.15.0 pre-flight halt: caller named modules that EXIST in the graph
    # but are leaves with no component instance invoking them. The work is
    # unactionable as a "bump" — what the user probably means is "create a
    # new component instance" (a different task_kind). Halt the DAG and ask.
    "stop-no-instance": [
        {"id": "halt", "personas": [], "parallel": False, "needs_approval": False},
    ],
}

# ---------------------------------------------------------------------------
# Renderer "vocabulary" — kept as a dict for source-stability with previous
# versions, but every value is now an empty string. v0.10.6 stripped emojis
# (and all their byte cost) from MCP card outputs to save tokens.
# Resurrect single keys here only if a downstream consumer truly needs them.
# ---------------------------------------------------------------------------
EMOJI = {
    "queued": "", "running": "", "done": "", "blocked": "", "skipped": "",
    "ok": "", "block": "", "warn": "",
    "environment": "", "shared-infra": "", "component": "", "application": "",
    "module": "", "cloud_provider": "", "component_type": "",
    "scope": "", "blast": "", "drift": "", "stats": "", "approval": "",
    "fanout": "", "review": "", "session": "", "critical": "", "graph": "",
    "neighbor": "", "path": "", "files": "", "time": "", "info": "",
    "spark": "",
    "parallel": "||",      # kept short — table cells benefit from the marker
    "sequential": "->",    # ASCII arrow
}

# Used by session_init to seed context.md — mirrors apm_modules' init_agent_session.py
# layout so MCP and CLI produce identical session dirs.
_CONTEXT_TEMPLATE = """\
# Context — session: {session}

Created: {created}

## Goal
{task}

## Graph snapshot at session start
{graph_summary}

## Constraints (standing)
- **Plan-only.** No `terragrunt apply`, no `tofu apply`, no `--destroy`.
- **OpenSpec required** for changes under `clouds/`, `components/`, `applications/`, `cue/`, behavioral `*.hcl`.
- **Pre-commit must pass** before any commit; never `--no-verify`.
- **Branch off MERGE_BASE** before any file edit (see `infra-bootstrap-mandatory`).
- **No recursive subagents.** Personas are leaves.

## Decisions
_(orchestrator records irreversible calls in `decisions.md` as the session unfolds)_

## Roster reference
Personas live under `.claude/agents/*.md`. Each persona reads everything in this
directory; each writes only its own file. The orchestrator owns `context.md`
and `decisions.md`.
"""


def _slugify(text: str) -> str:
    """Lower-case kebab slug; mirrors apm init_agent_session.slugify so MCP and
    CLI produce the same session dir names."""
    out = []
    for ch in (text or "").lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in "-_ ":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "session"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_hcl_dependencies(hcl_path: Path) -> list[str]:
    """Extract dependency names from terragrunt.hcl dependency blocks."""
    deps = []
    try:
        text = hcl_path.read_text()
        for m in re.finditer(r'dependency\s+"([^"]+)"', text):
            deps.append(m.group(1))
    except Exception:
        pass
    return deps


def parse_hcl_component_refs(hcl_path: Path) -> list[str]:
    """Extract component JSON references like include.root.locals.components.X.

    Returns refs sorted by name. The sort is load-bearing for output
    determinism: these refs are added as edges, and the BFS traversal
    used by `blast_radius` walks adjacency lists in insertion order, so
    a non-deterministic edge order surfaces as a non-deterministic
    blast_*.mmd output across regenerations of identical inputs.
    """
    refs = []
    try:
        text = hcl_path.read_text()
        for m in re.finditer(r'include\.root\.locals\.components\.(\w+)', text):
            refs.append(m.group(1))
        # Also catch components["X"] style
        for m in re.finditer(r'components\["([^"]+)"\]', text):
            refs.append(m.group(1))
    except Exception:
        pass
    return sorted(set(refs))


def load_json_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

class KuberlyPlatform:
    def __init__(self, repo_root: str):
        self.repo = Path(repo_root)
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    # -- node helpers --
    def add_node(self, nid: str, **attrs):
        self.nodes[nid] = {**attrs, "id": nid}

    def add_edge(self, src: str, dst: str, **attrs):
        self.edges.append({"source": src, "target": dst, **attrs})

    def _serializable_edges(self) -> list[dict]:
        """Return edges with both endpoints materialized as real nodes.

        Several scans intentionally emit edges to endpoints that are not
        added as nodes — abstract HCL `component_type:*` references,
        `tool:*` labels on agent docs, `k8s_namespace:*` targets, and
        state-overlay refs to resources the producer redacts. These
        carry useful query semantics (and existing tests assert on them
        in `self.edges`), but cytoscape aborts on the first orphan and
        refuses to render the canvas at all. Filter them out of the
        serialized projection only — leave `self.edges` intact for
        in-memory queries.
        """
        node_ids = self.nodes.keys()
        return [
            e for e in self.edges
            if e["source"] in node_ids and e["target"] in node_ids
        ]

    # -- scanners --
    def scan_environments(self):
        """Scan components/ for environments and their JSON configs."""
        comp_dir = self.repo / "components"
        if not comp_dir.exists():
            return
        for env_dir in sorted(comp_dir.iterdir()):
            if not env_dir.is_dir():
                continue
            env_name = env_dir.name
            self.add_node(f"env:{env_name}", type="environment", label=env_name)

            for jf in sorted(env_dir.glob("*.json")):
                comp_name = jf.stem
                nid = f"component:{env_name}/{comp_name}"
                data = load_json_safe(jf)
                is_shared = comp_name == "shared-infra"

                meta = {}
                if is_shared and data:
                    si = data.get("shared-infra", {})
                    target = si.get("target", {})
                    meta = {
                        "account_id": target.get("account_id", ""),
                        "region": target.get("region", ""),
                        "cluster_name": target.get("cluster", {}).get("name", ""),
                        "env_label": si.get("env", ""),
                    }

                self.add_node(nid, type="shared-infra" if is_shared else "component",
                              label=comp_name, environment=env_name, **meta)
                self.add_edge(f"env:{env_name}", nid, relation="contains")

                # shared-infra feeds all other components in the same env
                if is_shared:
                    for other in env_dir.glob("*.json"):
                        if other.stem != "shared-infra":
                            self.add_edge(nid, f"component:{env_name}/{other.stem}",
                                          relation="configures")

    def scan_applications(self):
        """Scan applications/ for app configs."""
        app_dir = self.repo / "applications"
        if not app_dir.exists():
            return
        for env_dir in sorted(app_dir.iterdir()):
            if not env_dir.is_dir():
                continue
            env_name = env_dir.name
            # Link to environment node if it exists (env names may differ)
            env_nid = f"env:{env_name}"
            if env_nid not in self.nodes:
                self.add_node(env_nid, type="environment", label=env_name)

            for jf in sorted(env_dir.glob("*.json")):
                app_name = jf.stem
                nid = f"app:{env_name}/{app_name}"
                data = load_json_safe(jf)
                meta = {}
                runtime_module = None  # explicit edge target (module name in clouds/aws/modules/)
                if data:
                    # Five app shapes are recognized. See the
                    # `application-types-and-deploy-paths` skill for full
                    # discrimination rules.
                    #
                    #   1. top-level `argo-app` key      -> CUE -> ArgoCD ApplicationSet -> K8s
                    #   2. top-level `deployment` key    -> CUE -> ArgoCD ApplicationSet -> K8s
                    #   3. `application.type = "ecs"`            -> module:aws/ecs_app
                    #   4. `application.type = "lambda"`         -> module:aws/lambda_app
                    #   5. `application.type = "bedrock_agentcore"` -> module:aws/bedrock_agentcore_app
                    #
                    # An app belongs to exactly one shape. Order of checks
                    # below matters when a JSON happens to carry overlapping
                    # keys (top-level wins; the new ECS/Lambda shape uses
                    # `application.type`, not a top-level discriminator).

                    if data.get("argo-app") is not None:
                        meta["runtime"] = "argo-app"
                        runtime_module = "argocd"
                        # Re-extract from inside argo-app for nicer metadata
                        argo = data["argo-app"]
                        dep = (argo or {}).get("deployment") or {}
                    elif data.get("deployment") is not None and data.get("application") is None:
                        meta["runtime"] = "deployment"
                        runtime_module = "argocd"
                        dep = data.get("deployment") or {}
                    else:
                        dep = data.get("deployment") or {}

                    # Common deployment.* metadata (works for argo-app, deployment,
                    # and ECS-shape JSONs that include a deployment block).
                    if dep:
                        if dep.get("port") is not None:
                            meta["port"] = dep["port"]
                        if dep.get("replicas") is not None:
                            meta["replicas"] = dep["replicas"]
                        container = dep.get("container") or {}
                        img = container.get("image") or {}
                        if img.get("repository"):
                            meta["image"] = img["repository"]
                        env_block = container.get("env") or {}
                        if env_block:
                            meta["secret_count"] = len(env_block.get("secrets", []) or [])
                            meta["env_var_count"] = len(env_block.get("env_vars", {}) or {})

                    app_block = data.get("application") or {}
                    if app_block:
                        # Discriminator the orchestrator uses to pick task_kind +
                        # which module to invoke (ecs_app vs lambda_app vs ...).
                        rt = app_block.get("type") or ""
                        if rt:
                            meta["runtime"] = rt   # e.g. "ecs", "lambda", "bedrock_agentcore"
                            # Conventional module names per runtime — match
                            # clouds/aws/modules/<name>/.
                            module_map = {
                                "ecs":                "ecs_app",
                                "lambda":             "lambda_app",
                                "bedrock_agentcore":  "bedrock_agentcore_app",
                                "ecs-app":            "ecs_app",      # alt spelling
                                "lambda-app":         "lambda_app",
                            }
                            runtime_module = module_map.get(rt)
                        if app_block.get("name"):
                            meta["app_name"] = app_block["name"]
                        if app_block.get("namespace_name"):
                            meta["namespace"] = app_block["namespace_name"]

                    # Cluster target — common across all shapes
                    common = data.get("common") or {}
                    if common.get("cluster_name"):
                        meta["cluster"] = common["cluster_name"]

                self.add_node(nid, type="application", label=app_name,
                              environment=env_name, **meta)
                self.add_edge(env_nid, nid, relation="deploys")

                # Application -> runtime module edge. Lets blast_radius surface
                # "all apps affected if the ecs_app/lambda_app/argocd module
                # changes." For argo-app and deployment shapes the conceptual
                # "module" is argocd (the ApplicationSet syncs the CUE-rendered
                # manifests); there is no per-app HCL module for those.
                if runtime_module:
                    rt_nid = f"module:aws/{runtime_module}"
                    self.add_edge(nid, rt_nid, relation="uses_module")

    def scan_modules(self):
        """Scan clouds/*/modules/ for Terragrunt modules and their dependency DAG."""
        clouds_dir = self.repo / "clouds"
        if not clouds_dir.exists():
            return
        for provider_dir in sorted(clouds_dir.iterdir()):
            if not provider_dir.is_dir() or provider_dir.name.startswith("."):
                continue
            provider = provider_dir.name
            modules_dir = provider_dir / "modules"
            if not modules_dir.exists():
                continue

            provider_nid = f"cloud:{provider}"
            self.add_node(provider_nid, type="cloud_provider", label=provider)

            for mod_dir in sorted(modules_dir.iterdir()):
                if not mod_dir.is_dir() or mod_dir.name.startswith("."):
                    continue
                mod_name = mod_dir.name
                nid = f"module:{provider}/{mod_name}"

                # Read kuberly.json metadata if present.
                # Field names vary across forks: `desc` (mami) or `description`.
                meta = {}
                declared_deps: list[str] = []
                kj = mod_dir / "kuberly.json"
                if kj.exists():
                    kdata = load_json_safe(kj)
                    if kdata:
                        # Prefer 'desc' (current spec) but accept 'description' as fallback.
                        desc = kdata.get("desc") or kdata.get("description") or ""
                        if desc:
                            meta["description"] = desc
                        if kdata.get("version"):
                            meta["version"] = kdata.get("version")
                        if kdata.get("types"):
                            meta["types"] = kdata.get("types")
                        if kdata.get("author"):
                            meta["author"] = kdata.get("author")
                        # `deps` is the module's authoritative dep list — more
                        # reliable than parsing terragrunt.hcl when present.
                        d = kdata.get("deps") or []
                        if isinstance(d, list):
                            declared_deps = [str(x) for x in d if x]

                self.add_node(nid, type="module", label=mod_name,
                              provider=provider, path=str(mod_dir.relative_to(self.repo)), **meta)
                self.add_edge(provider_nid, nid, relation="provides")

                # Edges from kuberly.json `deps`. These are AUTHORITATIVE — the
                # module author declared them. Cross-link to same-provider sibling.
                for dep_name in declared_deps:
                    dep_nid = f"module:{provider}/{dep_name}"
                    self.add_edge(nid, dep_nid, relation="depends_on")

                # Parse terragrunt.hcl for dependencies (heuristic, picks up
                # additional refs the kuberly.json author may have omitted).
                tg = mod_dir / "terragrunt.hcl"
                if tg.exists():
                    for dep in parse_hcl_dependencies(tg):
                        dep_nid = f"module:{provider}/{dep}"
                        self.add_edge(nid, dep_nid, relation="depends_on")

                    # Parse component JSON references
                    for comp_ref in parse_hcl_component_refs(tg):
                        self.add_edge(nid, f"component_type:{comp_ref}",
                                      relation="reads_config")

    # Resource types whose value-bearing attributes the producer
    # specifically suppresses. We surface the resource nodes (so the
    # graph reflects "this exists") but tag them so consumers / UIs can
    # render them with a redaction marker.
    _SENSITIVE_RESOURCE_TYPES = frozenset({
        "aws_secretsmanager_secret",
        "aws_secretsmanager_secret_version",
        "aws_ssm_parameter",
        "aws_iam_access_key",
        "aws_iam_user_login_profile",
        "aws_db_instance",
        "aws_rds_cluster",
        "kubernetes_secret",
        "kubernetes_secret_v1",
        "helm_release",
        "tls_private_key",
        "tls_self_signed_cert",
        "random_password",
        "random_string",
        "external",
    })

    def scan_state_overlays(self):
        """Augment the static graph with what is *actually deployed*, read
        from `.kuberly/state_overlay_*.json` files generated by
        `state_graph.py`.

        Schema 1 (list-only): synthesize `component:<env>/<m>` for every
        module found in the state bucket. Fixes `stop-no-instance` for
        modules deployed without a JSON sidecar (loki / grafana / alloy).

        Schema 2 (with resources): also synthesize per-resource nodes
        `resource:<env>/<m>/<address>` and `depends_on` edges between
        them. Resource attribute values are NOT in the overlay — the
        producer drops them at write time. Sensitive types (secrets,
        passwords, TLS keys) get `redacted: true` so renderers can mask.

        The overlay file is producer-validated by `state_graph.py`. Here
        we only read fields we need and skip any file that fails to
        parse — the consumer never touches state contents.
        """
        overlay_dir = self.repo / ".kuberly"
        if not overlay_dir.is_dir():
            return
        for of in sorted(overlay_dir.glob("state_overlay_*.json")):
            data = load_json_safe(of)
            if not data or data.get("schema_version") not in (1, 2):
                continue
            cluster = data.get("cluster") or {}
            env = cluster.get("env")
            if not env or not isinstance(env, str):
                continue
            env_nid = f"env:{env}"
            if env_nid not in self.nodes:
                self.add_node(env_nid, type="environment", label=env)
            for entry in data.get("deployed_modules") or []:
                name = entry.get("name") if isinstance(entry, dict) else None
                if not name or not isinstance(name, str):
                    continue
                comp_nid = f"component:{env}/{name}"
                existing = self.nodes.get(comp_nid)
                if existing:
                    # Static JSON sidecar already created this — annotate
                    # that state confirms it.
                    existing["also_in_state"] = True
                else:
                    self.add_node(
                        comp_nid,
                        type="component",
                        label=name,
                        environment=env,
                        source="state",
                    )
                    self.add_edge(env_nid, comp_nid, relation="contains")

            # Schema 2: resource graph
            modules_section = data.get("modules") or {}
            if isinstance(modules_section, dict):
                self._scan_state_resources(env, modules_section)

    # Annotations whose presence we want to surface as graph attrs.
    _K8S_REDACTED_KINDS = frozenset({"Secret", "ConfigMap"})

    def scan_k8s_overlays(self):
        """Load `.kuberly/k8s_overlay_*.json` (produced by `k8s_graph.py`)
        and synthesize live-cluster nodes + edges into the graph.

        Where state_graph.py is the **infrastructure** layer (Terraform
        modules + resources), k8s_graph.py is the **runtime** layer
        (Deployments, Services, ServiceAccounts, etc.). The two are
        bridged via IRSA: a k8s ServiceAccount with
        `eks.amazonaws.com/role-arn` annotation gets an `irsa_bound`
        edge to the matching `resource:*/aws_iam_role.<name>` node from
        the state overlay.

        The overlay file is producer-validated. Here we only consume
        whitelisted fields and skip malformed entries silently.
        """
        overlay_dir = self.repo / ".kuberly"
        if not overlay_dir.is_dir():
            return
        for of in sorted(overlay_dir.glob("k8s_overlay_*.json")):
            data = load_json_safe(of)
            if not data or data.get("schema_version") != 1:
                continue
            cluster = data.get("cluster") or {}
            env = cluster.get("env")
            if not env or not isinstance(env, str):
                continue
            self._scan_k8s_resources(env, data.get("resources") or [])

    def _scan_k8s_resources(self, env: str, resources: list) -> None:
        """Synthesize k8s:* nodes + intra-cluster edges from one overlay."""
        # Index by (ns, kind, name) for selector / ref resolution.
        nodes_by_kind_ns: dict[tuple[str, str], list[dict]] = {}
        # Pass 1: create all nodes.
        for r in resources:
            if not isinstance(r, dict):
                continue
            kind = r.get("kind") or ""
            ns = r.get("namespace") or ""
            name = r.get("name") or ""
            if not (kind and name):
                continue
            nid = f"k8s:{env}/{ns}/{kind}/{name}" if ns else f"k8s:{env}//{kind}/{name}"
            attrs = {
                "type": "k8s_resource",
                "label": f"{kind}/{name}",
                "environment": env,
                "k8s_kind": kind,
                "k8s_namespace": ns,
                "k8s_name": name,
                "labels": r.get("labels") or {},
                "annotations": r.get("annotations") or {},
            }
            # Carry kind-specific fields through verbatim.
            for k in (
                "replicas", "service_account", "containers", "images",
                "config_refs", "secret_refs", "pvc_refs",
                "selector", "ports", "service_type",
                "hosts", "backends",
                "data_keys", "secret_type",
                "irsa_role_arn",
                "min_replicas", "max_replicas", "target_kind", "target_name",
                "pod_selector", "policy_types",
                "owner_refs",
                # Karpenter
                "node_class_kind", "node_class_name", "limits_cpu",
                "limits_memory", "consolidation_policy", "requirement_keys",
                "ami_family", "iam_role",
                # ArgoCD
                "argocd_project", "source_repo", "source_path",
                "source_revision", "dest_server", "dest_namespace",
                "source_repos", "destinations",
                # Istio
                "gateways", "routes", "servers",
                "host", "tls_mode", "location",
                "mtls_mode", "action",
            ):
                if k in r:
                    attrs[k] = r[k]
            if kind in self._K8S_REDACTED_KINDS:
                attrs["redacted"] = True
            self.add_node(nid, **attrs)
            nodes_by_kind_ns.setdefault((ns, kind), []).append(attrs | {"_id": nid})

        # Pass 2: edges.
        for r in resources:
            if not isinstance(r, dict):
                continue
            kind = r.get("kind") or ""
            ns = r.get("namespace") or ""
            name = r.get("name") or ""
            if not (kind and name):
                continue
            src = f"k8s:{env}/{ns}/{kind}/{name}" if ns else f"k8s:{env}//{kind}/{name}"

            # ownerRefs -> parent in same ns
            for owner in (r.get("owner_refs") or []):
                ok, on = owner.get("kind"), owner.get("name")
                if ok and on:
                    parent = f"k8s:{env}/{ns}/{ok}/{on}"
                    self.add_edge(parent, src, relation="owns")

            # workload -> ServiceAccount
            sa = r.get("service_account")
            if sa:
                self.add_edge(src, f"k8s:{env}/{ns}/ServiceAccount/{sa}",
                              relation="uses_sa")

            # workload -> ConfigMap / Secret / PVC
            for cn in (r.get("config_refs") or []):
                self.add_edge(src, f"k8s:{env}/{ns}/ConfigMap/{cn}",
                              relation="reads_configmap")
            for sn in (r.get("secret_refs") or []):
                self.add_edge(src, f"k8s:{env}/{ns}/Secret/{sn}",
                              relation="reads_secret")
            for pn in (r.get("pvc_refs") or []):
                self.add_edge(src, f"k8s:{env}/{ns}/PersistentVolumeClaim/{pn}",
                              relation="mounts_pvc")

            # Service -> workload via selector
            if kind == "Service":
                sel = r.get("selector") or {}
                if sel:
                    for (cand_ns, cand_kind), cands in nodes_by_kind_ns.items():
                        if cand_ns != ns:
                            continue
                        if cand_kind not in (
                                "Deployment", "StatefulSet", "DaemonSet",
                                "ReplicaSet", "Pod", "Job", "CronJob"):
                            continue
                        for c in cands:
                            labels = c.get("labels") or {}
                            if all(labels.get(k) == v for k, v in sel.items()):
                                self.add_edge(src, c["_id"], relation="selects")

            # Ingress -> Service
            if kind == "Ingress":
                for be in (r.get("backends") or []):
                    s = be.get("service")
                    if s:
                        self.add_edge(src, f"k8s:{env}/{ns}/Service/{s}",
                                      relation="routes_to")

            # HPA -> target workload
            if kind == "HorizontalPodAutoscaler":
                tk, tn = r.get("target_kind"), r.get("target_name")
                if tk and tn:
                    self.add_edge(src, f"k8s:{env}/{ns}/{tk}/{tn}",
                                  relation="scales")

            # Karpenter NodePool / NodeClaim -> EC2NodeClass (cluster-scoped, ns="")
            if kind in ("NodePool", "NodeClaim"):
                ck, cn = r.get("node_class_kind"), r.get("node_class_name")
                if ck and cn:
                    self.add_edge(src, f"k8s:{env}//{ck}/{cn}",
                                  relation="uses_node_class")

            # ArgoCD Application -> destination namespace ref (informational
            # edge — the namespace node is not a k8s_resource here, but
            # we still record the relation for downstream queries).
            if kind in ("Application", "ApplicationSet"):
                dest_ns = r.get("dest_namespace")
                if dest_ns:
                    self.add_edge(src, f"k8s_namespace:{env}/{dest_ns}",
                                  relation="targets_namespace")

            # Istio VirtualService -> Gateway + Service routes
            if kind == "VirtualService":
                # gateways[] entries: "<name>" or "<ns>/<name>"
                for gw in (r.get("gateways") or []):
                    if "/" in gw:
                        gw_ns, gw_name = gw.split("/", 1)
                    else:
                        gw_ns, gw_name = ns, gw
                    self.add_edge(src, f"k8s:{env}/{gw_ns}/Gateway/{gw_name}",
                                  relation="bound_to_gateway")
                # routes[].host: short name or FQDN; pull short name when
                # it ends with .svc.cluster.local or is a single token.
                for route in (r.get("routes") or []):
                    host = (route.get("host") or "")
                    if not host:
                        continue
                    parts = host.split(".")
                    svc_ns = ns
                    svc_name = parts[0] if parts else host
                    # FQDN form: <svc>.<ns>.svc.cluster.local
                    if len(parts) >= 2 and (".svc." in host or len(parts) >= 4):
                        svc_name, svc_ns = parts[0], parts[1]
                    self.add_edge(src, f"k8s:{env}/{svc_ns}/Service/{svc_name}",
                                  relation="routes_to")

            # Istio DestinationRule -> Service (host)
            if kind == "DestinationRule":
                host = r.get("host") or ""
                if host:
                    parts = host.split(".")
                    svc_ns = ns
                    svc_name = parts[0]
                    if len(parts) >= 2 and (".svc." in host or len(parts) >= 4):
                        svc_name, svc_ns = parts[0], parts[1]
                    self.add_edge(src, f"k8s:{env}/{svc_ns}/Service/{svc_name}",
                                  relation="configures_service")

        # Pass 3: IRSA bridge — link ServiceAccount with role ARN to
        # matching aws_iam_role resource node from the state overlay.
        for r in resources:
            if not isinstance(r, dict) or r.get("kind") != "ServiceAccount":
                continue
            arn = r.get("irsa_role_arn") or ""
            if not arn or ":role/" not in arn:
                continue
            # ARN: arn:aws:iam::<acct>:role/<optional-path/>/<name>
            #   strip ":role/" prefix, take the last path segment.
            tail = arn.split(":role/", 1)[-1]
            role_name = tail.rsplit("/", 1)[-1]
            ns = r.get("namespace") or ""
            sa_name = r.get("name") or ""
            sa_nid = f"k8s:{env}/{ns}/ServiceAccount/{sa_name}"
            # Find resource:<env>/<mod>/...aws_iam_role.<role_name>
            for nid, node in self.nodes.items():
                if (node.get("type") == "resource"
                        and node.get("environment") == env
                        and node.get("resource_type") == "aws_iam_role"
                        and node.get("resource_name") == role_name):
                    self.add_edge(sa_nid, nid, relation="irsa_bound")

    def scan_docs_overlay(self):
        """Load `.kuberly/docs_overlay.json` (produced by `docs_graph.py`)
        and synthesize doc nodes + cross-link edges.

        Knowledge layer of the graph: which file explains which thing,
        what links what, what skill mentions which module. Embeddings
        (if present) are kept on the node attrs for `find_docs` semantic
        ranking; they're not used by node-level graph traversal.
        """
        overlay_path = self.repo / ".kuberly" / "docs_overlay.json"
        if not overlay_path.is_file():
            return
        data = load_json_safe(overlay_path)
        if not data or data.get("schema_version") != 1:
            return
        for d in data.get("docs") or []:
            if not isinstance(d, dict):
                continue
            did = d.get("id")
            kind = d.get("kind")
            path = d.get("path")
            if not (did and kind and path):
                continue
            nid = f"doc:{did}"
            attrs = {
                "type": "doc",
                "label": d.get("title") or did,
                "doc_kind": kind,
                "path": path,
                "description": d.get("description", "") or "",
                "headings": d.get("headings") or [],
                "tools": d.get("tools") or [],
                "content_sha": d.get("content_sha", ""),
            }
            embed = d.get("embedding_b64", "")
            if embed:
                attrs["has_embedding"] = True
                # Keep the raw embedding off the public node dict to
                # avoid bloating query_nodes responses; stash separately.
                self._doc_embeddings = getattr(self, "_doc_embeddings", {})
                self._doc_embeddings[nid] = embed
            self.add_node(nid, **attrs)

        # Pass 2: edges. linked_docs map to other doc IDs by path.
        path_to_did: dict[str, str] = {}
        for d in data.get("docs") or []:
            if isinstance(d, dict) and d.get("path") and d.get("id"):
                path_to_did[d["path"]] = d["id"]

        for d in data.get("docs") or []:
            if not isinstance(d, dict):
                continue
            src = f"doc:{d.get('id')}"
            for link_path in d.get("linked_docs") or []:
                if link_path in path_to_did:
                    self.add_edge(src, f"doc:{path_to_did[link_path]}",
                                  relation="links_to")
            mentions = d.get("mentions") or {}
            for mod in (mentions.get("modules") or []):
                # Find the module node — there may be multiple cloud variants.
                for nid, node in self.nodes.items():
                    if node.get("type") == "module" and node.get("label") == mod:
                        self.add_edge(src, nid, relation="mentions")
            for comp in (mentions.get("components") or []):
                for nid, node in self.nodes.items():
                    if node.get("type") == "component" and node.get("label") == comp:
                        self.add_edge(src, nid, relation="mentions")
            for app in (mentions.get("applications") or []):
                for nid, node in self.nodes.items():
                    if node.get("type") == "application" and node.get("label") == app:
                        self.add_edge(src, nid, relation="mentions")
            # Agent → tool edges (informational; tool node may not exist
            # but the edge target is a stable id like "tool:Read").
            for t in d.get("tools") or []:
                self.add_edge(src, f"tool:{t}", relation="uses_tool")

        # Stash overlay metadata for graph_index().
        self._docs_overlay_meta = {
            "generated_at": data.get("generated_at", ""),
            "embed_provider": data.get("embed_provider", ""),
            "doc_count": len(data.get("docs") or []),
        }

    def _scan_state_resources(self, env: str, modules_section: dict) -> None:
        """Synthesize `resource:<env>/<mod>/<addr>` nodes and depends_on
        edges from the schema 2 overlay's `modules` section."""
        for mod_name, payload in modules_section.items():
            if not isinstance(payload, dict):
                continue
            comp_nid = f"component:{env}/{mod_name}"
            if comp_nid in self.nodes:
                rc = payload.get("resource_count")
                if isinstance(rc, int):
                    self.nodes[comp_nid]["resource_count"] = rc
                onames = payload.get("output_names") or []
                if isinstance(onames, list):
                    self.nodes[comp_nid]["output_names"] = list(onames)

            for r in payload.get("resources") or []:
                if not isinstance(r, dict):
                    continue
                addr = r.get("address")
                rtype = r.get("type")
                rname = r.get("name")
                if not (addr and rtype and rname):
                    continue
                rid = f"resource:{env}/{mod_name}/{addr}"
                attrs = {
                    "type": "resource",
                    "label": addr,
                    "environment": env,
                    "module": mod_name,
                    "resource_type": rtype,
                    "resource_name": rname,
                    "provider": r.get("provider", ""),
                    "instance_count": r.get("instance_count", 0),
                }
                if rtype in self._SENSITIVE_RESOURCE_TYPES:
                    attrs["redacted"] = True
                self.add_node(rid, **attrs)
                if comp_nid in self.nodes:
                    self.add_edge(comp_nid, rid, relation="contains")
                for dep_addr in r.get("depends_on") or []:
                    if not isinstance(dep_addr, str):
                        continue
                    dep_rid = f"resource:{env}/{mod_name}/{dep_addr}"
                    self.add_edge(rid, dep_rid, relation="depends_on")

    def scan_catalog(self):
        """Enrich modules with catalog metadata."""
        catalog_path = self.repo / "catalog" / "modules.json"
        if not catalog_path.exists():
            return
        data = load_json_safe(catalog_path)
        if not data:
            return
        for mod in data.get("modules", []):
            name = mod.get("name", "")
            # Try to match to existing module node
            for nid, node in self.nodes.items():
                if node.get("type") == "module" and node.get("label") == name:
                    node["resource_count"] = mod.get("resource_count", 0)
                    node["providers"] = mod.get("providers", [])
                    node["has_readme"] = mod.get("has_readme", False)
                    node["state_key"] = mod.get("state_key", "")
                    break

    def link_components_to_modules(self):
        """Link component JSONs to modules they configure."""
        # Sorted so the resulting `configures_module` edges are added in
        # a stable order across runs. A bare `{...}` set comprehension
        # iterates in PYTHONHASHSEED-dependent order, which surfaces as
        # non-deterministic edge ordering in graph.json.
        module_names = sorted({n["label"] for n in self.nodes.values() if n["type"] == "module"})
        for nid, node in list(self.nodes.items()):
            if node["type"] == "component":
                comp_name = node["label"]
                # Direct name match (e.g. eks.json -> module eks)
                normalized = comp_name.replace("-", "_")
                if normalized in module_names:
                    for provider_mod_nid, pnode in self.nodes.items():
                        if pnode.get("type") == "module" and pnode["label"] == normalized:
                            self.add_edge(nid, provider_mod_nid, relation="configures_module")
                # Also match partial names (e.g. secretsmanager_secrets -> secrets)
                for mname in module_names:
                    if mname in normalized or normalized in mname:
                        if mname != normalized:
                            for provider_mod_nid, pnode in self.nodes.items():
                                if pnode.get("type") == "module" and pnode["label"] == mname:
                                    self.add_edge(nid, provider_mod_nid,
                                                  relation="configures_module")

    def cross_env_drift(self) -> dict:
        """Detect components/apps present in some envs but not others."""
        env_components = defaultdict(set)
        env_apps = defaultdict(set)
        for nid, node in self.nodes.items():
            if node["type"] == "component":
                env_components[node["environment"]].add(node["label"])
            elif node["type"] == "application":
                env_apps[node["environment"]].add(node["label"])

        all_comps = set().union(*env_components.values()) if env_components else set()
        all_apps = set().union(*env_apps.values()) if env_apps else set()

        drift = {"components": {}, "applications": {}}
        for env in env_components:
            missing = all_comps - env_components[env]
            if missing:
                drift["components"][env] = sorted(missing)
        for env in env_apps:
            missing = all_apps - env_apps[env]
            if missing:
                drift["applications"][env] = sorted(missing)
        return drift

    def compute_stats(self) -> dict:
        """Compute graph statistics."""
        # In-degree / out-degree
        in_deg = defaultdict(int)
        out_deg = defaultdict(int)
        for e in self.edges:
            out_deg[e["source"]] += 1
            in_deg[e["target"]] += 1

        # Critical nodes (highest in-degree = most depended upon)
        all_nodes_deg = [(nid, in_deg.get(nid, 0), out_deg.get(nid, 0))
                         for nid in self.nodes]
        critical = sorted(all_nodes_deg, key=lambda x: x[1], reverse=True)[:10]

        # Dependency chains - find longest path from each root module
        module_deps = defaultdict(list)
        for e in self.edges:
            if e.get("relation") == "depends_on":
                module_deps[e["source"]].append(e["target"])

        def longest_chain(node, visited=None):
            if visited is None:
                visited = set()
            if node in visited:
                return [node + " (CYCLE)"]
            visited.add(node)
            best = [node]
            for dep in module_deps.get(node, []):
                chain = [node] + longest_chain(dep, visited.copy())
                if len(chain) > len(best):
                    best = chain
            return best

        chains = []
        for nid in self.nodes:
            if nid.startswith("module:") and module_deps.get(nid):
                chain = longest_chain(nid)
                if len(chain) > 1:
                    chains.append(chain)
        chains.sort(key=len, reverse=True)

        type_counts = defaultdict(int)
        for n in self.nodes.values():
            type_counts[n["type"]] += 1

        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "type_counts": dict(type_counts),
            "critical_nodes": [(nid, ind, outd) for nid, ind, outd in critical],
            "longest_chains": chains[:5],
        }

    # -- graph traversal --
    def _build_adjacency(self) -> tuple[dict[str, list], dict[str, list]]:
        """Build forward (outgoing) and reverse (incoming) adjacency lists."""
        fwd = defaultdict(list)  # node -> [(target, relation)]
        rev = defaultdict(list)  # node -> [(source, relation)]
        for e in self.edges:
            fwd[e["source"]].append((e["target"], e.get("relation", "")))
            rev[e["target"]].append((e["source"], e.get("relation", "")))
        return dict(fwd), dict(rev)

    def blast_radius(self, node_query: str, direction: str = "both",
                     max_depth: int = 20) -> dict:
        """Compute blast radius for a node — what it affects (downstream) and what affects it (upstream).

        node_query: exact node id or partial match on label/id.
        direction: 'downstream', 'upstream', or 'both'.
        """
        # Resolve node
        match = None
        for nid, node in self.nodes.items():
            if nid == node_query or node.get("label") == node_query:
                match = nid
                break
        if not match:
            # Fuzzy: substring match
            candidates = [nid for nid, n in self.nodes.items()
                          if node_query.lower() in nid.lower()
                          or node_query.lower() in n.get("label", "").lower()]
            if len(candidates) == 1:
                match = candidates[0]
            elif candidates:
                return {"error": f"Ambiguous query '{node_query}', matches: {candidates[:10]}"}
            else:
                return {"error": f"No node matching '{node_query}'"}

        fwd, rev = self._build_adjacency()

        def walk(start, adj, depth=0):
            visited = {}
            queue = [(start, 0)]
            while queue:
                current, d = queue.pop(0)
                if current in visited or d > max_depth:
                    continue
                visited[current] = d
                for neighbor, rel in adj.get(current, []):
                    if neighbor not in visited:
                        queue.append((neighbor, d + 1))
            visited.pop(start, None)
            return visited

        result = {
            "node": match,
            "node_info": self.nodes.get(match, {}),
        }
        if direction in ("downstream", "both"):
            ds = walk(match, fwd)
            result["downstream"] = {nid: {"depth": d, **self.nodes.get(nid, {})}
                                    for nid, d in sorted(ds.items(), key=lambda x: x[1])}
            result["downstream_count"] = len(ds)
        if direction in ("upstream", "both"):
            us = walk(match, rev)
            result["upstream"] = {nid: {"depth": d, **self.nodes.get(nid, {})}
                                  for nid, d in sorted(us.items(), key=lambda x: x[1])}
            result["upstream_count"] = len(us)

        return result

    def shortest_path(self, source_query: str, target_query: str) -> dict:
        """Find shortest path between two nodes (BFS, undirected)."""
        def resolve(q):
            for nid in self.nodes:
                if nid == q or self.nodes[nid].get("label") == q:
                    return nid
            cands = [nid for nid in self.nodes
                     if q.lower() in nid.lower()
                     or q.lower() in self.nodes[nid].get("label", "").lower()]
            return cands[0] if len(cands) == 1 else None

        src = resolve(source_query)
        tgt = resolve(target_query)
        if not src:
            return {"error": f"Cannot resolve source '{source_query}'"}
        if not tgt:
            return {"error": f"Cannot resolve target '{target_query}'"}

        # BFS undirected
        adj = defaultdict(set)
        for e in self.edges:
            adj[e["source"]].add(e["target"])
            adj[e["target"]].add(e["source"])

        visited = {src: None}
        queue = [src]
        while queue:
            current = queue.pop(0)
            if current == tgt:
                path = []
                while current is not None:
                    path.append(current)
                    current = visited[current]
                path.reverse()
                return {"path": path, "length": len(path) - 1}
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = current
                    queue.append(neighbor)
        return {"error": f"No path between '{src}' and '{tgt}'"}

    def query_nodes(self, node_type: str = None, environment: str = None,
                    name_contains: str = None) -> list[dict]:
        """Filter nodes by type, environment, and/or name substring."""
        results = []
        for nid, node in self.nodes.items():
            if node_type and node.get("type") != node_type:
                continue
            if environment and node.get("environment") != environment:
                continue
            if name_contains and name_contains.lower() not in node.get("label", "").lower() \
               and name_contains.lower() not in nid.lower():
                continue
            results.append(node)
        return results

    def find_docs(self, query: str = "", kind: str = None,
                  semantic: bool = True, limit: int = 20) -> dict:
        """Search the docs overlay. Always does keyword scoring (title
        + description + headings). If embeddings are present and
        `semantic=True`, *also* computes cosine similarity and combines
        the two scores. Returns top `limit` hits."""
        q = (query or "").strip().lower()
        q_terms = [t for t in re.split(r"[\s,]+", q) if t]
        embeddings = getattr(self, "_doc_embeddings", {})
        provider = (getattr(self, "_docs_overlay_meta", {}) or {}).get("embed_provider", "")

        # Optional: embed the query.
        q_vec: list[float] | None = None
        if semantic and embeddings and provider and q:
            q_b64 = self._embed_query(q, provider)
            if q_b64:
                q_vec = _b64_to_float_list(q_b64)

        scored: list[tuple[float, dict]] = []
        for nid, node in self.nodes.items():
            if node.get("type") != "doc":
                continue
            if kind and node.get("doc_kind") != kind:
                continue
            kw_score = 0.0
            if q_terms:
                hay = " ".join([
                    node.get("label", ""),
                    node.get("description", ""),
                    " ".join(node.get("headings", []) or []),
                    nid,
                ]).lower()
                kw_score = sum(1.0 for t in q_terms if t in hay) / max(1, len(q_terms))
            sem_score = 0.0
            if q_vec is not None and nid in embeddings:
                doc_vec = _b64_to_float_list(embeddings[nid])
                sem_score = _cosine(q_vec, doc_vec)
            # Combine: 0.4 * keyword + 0.6 * semantic if semantic available,
            # else 100% keyword.
            if q_vec is not None:
                score = 0.4 * kw_score + 0.6 * sem_score
            else:
                score = kw_score
            if not q_terms:
                score = 1.0  # no query -> rank by id alphabetically
            if score > 0:
                scored.append((score, node))
        scored.sort(key=lambda x: (-x[0], x[1].get("id", "")))
        matches = [n for _, n in scored[:limit]]
        return {
            "matches": matches,
            "count": len(scored),
            "semantic_used": q_vec is not None,
        }

    def _embed_query(self, query: str, provider: str) -> str:
        """Best-effort: embed a query string via the same provider used
        for the overlay. Returns base64 string or "" on failure."""
        try:
            sg_dir = Path(__file__).resolve().parent
            if str(sg_dir) not in sys.path:
                sys.path.insert(0, str(sg_dir))
            import docs_graph  # noqa: E402
            return docs_graph._embed_text_b64(query, provider)
        except Exception:
            return ""

    def graph_index(self) -> dict:
        """Meta-tool: summarize the loaded graph layers, their freshness,
        and which cross-layer bridges fired."""
        layer_counts: dict[str, int] = {}
        for n in self.nodes.values():
            t = n.get("type", "?")
            layer_counts[t] = layer_counts.get(t, 0) + 1
        edge_counts: dict[str, int] = {}
        bridges = {"irsa_bound": 0, "configures_module": 0,
                   "depends_on": 0, "mentions": 0}
        for e in self.edges:
            r = e.get("relation", "")
            edge_counts[r] = edge_counts.get(r, 0) + 1
            if r in bridges:
                bridges[r] += 1
        # Discover overlay files for freshness reporting.
        overlay_dir = self.repo / ".kuberly"
        overlays = []
        if overlay_dir.is_dir():
            for of in sorted(overlay_dir.glob("*_overlay*.json")):
                try:
                    with of.open("r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    overlays.append({
                        "file": str(of.relative_to(self.repo)),
                        "schema_version": data.get("schema_version"),
                        "generated_at": data.get("generated_at", ""),
                    })
                except (OSError, json.JSONDecodeError):
                    continue
        return {
            "node_counts_by_type": layer_counts,
            "edge_counts_by_relation": dict(sorted(edge_counts.items())),
            "cross_layer_bridges": bridges,
            "overlays_found": overlays,
            "docs_overlay_meta": getattr(self, "_docs_overlay_meta", {}),
        }

    def query_k8s(self, environment: str = None, namespace: str = None,
                  kind: str = None, name_contains: str = None,
                  label_selector: dict = None,
                  include_redacted: bool = True) -> dict:
        """Filter `k8s_resource:` nodes synthesized from
        `.kuberly/k8s_overlay_*.json`. label_selector is a {key: value}
        dict — matches only resources whose labels contain ALL pairs."""
        matches: list[dict] = []
        for nid, node in self.nodes.items():
            if node.get("type") != "k8s_resource":
                continue
            if environment and node.get("environment") != environment:
                continue
            if namespace and node.get("k8s_namespace") != namespace:
                continue
            if kind and node.get("k8s_kind") != kind:
                continue
            if not include_redacted and node.get("redacted"):
                continue
            if name_contains:
                hay = (node.get("k8s_name", "") + " " + nid).lower()
                if name_contains.lower() not in hay:
                    continue
            if label_selector:
                labels = node.get("labels") or {}
                if not all(labels.get(k) == v for k, v in label_selector.items()):
                    continue
            matches.append(node)
        matches.sort(key=lambda n: n.get("id", ""))
        truncated = len(matches) > 200
        return {"matches": matches[:200], "count": len(matches), "truncated": truncated}

    def query_resources(self, environment: str = None, module: str = None,
                        resource_type: str = None, name_contains: str = None,
                        include_redacted: bool = True) -> dict:
        """Filter `resource:` nodes synthesized from the schema 2 state
        overlay. Returns at most 200 matches by default — pagination is
        the caller's job (add filters)."""
        matches: list[dict] = []
        for nid, node in self.nodes.items():
            if node.get("type") != "resource":
                continue
            if environment and node.get("environment") != environment:
                continue
            if module and node.get("module") != module:
                continue
            if resource_type and node.get("resource_type") != resource_type:
                continue
            if not include_redacted and node.get("redacted"):
                continue
            if name_contains:
                hay = (node.get("label", "") + " " + nid).lower()
                if name_contains.lower() not in hay:
                    continue
            matches.append(node)
        # Stable sort
        matches.sort(key=lambda n: n.get("id", ""))
        truncated = len(matches) > 200
        return {
            "matches": matches[:200],
            "count": len(matches),
            "truncated": truncated,
        }

    def get_neighbors(self, node_query: str) -> dict:
        """Get immediate incoming and outgoing neighbors."""
        match = None
        for nid in self.nodes:
            if nid == node_query or self.nodes[nid].get("label") == node_query:
                match = nid
                break
        if not match:
            cands = [nid for nid in self.nodes if node_query.lower() in nid.lower()]
            match = cands[0] if len(cands) == 1 else None
        if not match:
            return {"error": f"No node matching '{node_query}'"}

        incoming = [{"source": e["source"], "relation": e.get("relation", "")}
                    for e in self.edges if e["target"] == match]
        outgoing = [{"target": e["target"], "relation": e.get("relation", "")}
                    for e in self.edges if e["source"] == match]
        return {
            "node": match,
            "node_info": self.nodes[match],
            "incoming": incoming,
            "outgoing": outgoing,
        }

    # ------------------------------------------------------------------
    # Orchestration: task classification, scope, gates, plan_persona_fanout
    # ------------------------------------------------------------------

    def infer_task_kind(self, task: str) -> tuple[str, str]:
        """Score the task string against KEYWORDS, return (kind, confidence)."""
        if not task:
            return "unknown", "low"
        text = task.lower()
        scores = {kind: 0 for kind in KEYWORDS}
        for kind, words in KEYWORDS.items():
            for w in words:
                if w in text:
                    scores[kind] += 1
        best_kind, best_score = max(scores.items(), key=lambda x: x[1])
        if best_score == 0:
            return "unknown", "low"
        # Confidence: how dominant is the winner?
        runner_up = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0
        if best_score >= 2 and best_score > runner_up:
            return best_kind, "high"
        if best_score >= 1 and best_score > runner_up:
            return best_kind, "medium"
        return best_kind, "low"

    def _resolve_modules(self, names: list[str]) -> list[dict]:
        """For each name, find matching module nodes. Returns list of node dicts."""
        if not names:
            return []
        resolved = []
        for name in names:
            name_low = name.lower()
            for nid, node in self.nodes.items():
                if node.get("type") != "module":
                    continue
                if (node.get("label", "").lower() == name_low
                        or nid.lower() == name_low
                        or name_low in nid.lower()):
                    if node not in resolved:
                        resolved.append(node)
                    break
        return resolved

    def _files_likely_changed(self, module_path: str) -> list[str]:
        """Heuristic: list edit-likely files in a module dir."""
        out = []
        if not module_path:
            return out
        mod_dir = self.repo / module_path
        if not mod_dir.is_dir():
            return out
        # Common edit targets, in priority order
        for rel in ("variables.tf", "main.tf", "helm.tf", "terragrunt.hcl"):
            p = mod_dir / rel
            if p.is_file():
                out.append(str(p.relative_to(self.repo)))
        # Helm values
        values_dir = mod_dir / "values"
        if values_dir.is_dir():
            for vf in sorted(values_dir.glob("*.yaml")):
                out.append(str(vf.relative_to(self.repo)))
        return out

    def scope_for_change(self, modules: list[str] | None = None,
                         envs: list[str] | None = None) -> dict:
        """Combined scope slice: blast radius + drift + likely-changed files."""
        resolved = self._resolve_modules(modules or [])

        # Blast radius union across all named modules
        downstream_count = 0
        upstream_labels: set[str] = set()
        files: list[str] = []
        for node in resolved:
            br = self.blast_radius(node["id"], direction="both", max_depth=3)
            if "error" not in br:
                downstream_count += br.get("downstream_count", 0)
                for un in br.get("upstream", {}).values():
                    if un.get("type") == "module":
                        upstream_labels.add(un.get("label", ""))
            files.extend(self._files_likely_changed(node.get("path", "")))

        # Drift slice — only relevant if envs are named
        drift_slice = None
        if envs:
            full = self.cross_env_drift()
            drift_slice = {e: full["components"].get(e, [])
                           for e in envs if e in full["components"]} or None

        # OpenSpec required if any module sits under the trigger paths
        openspec_paths_touched = []
        for node in resolved:
            path = node.get("path", "")
            if any(path.startswith(p) for p in OPENSPEC_PATHS):
                openspec_paths_touched.append(path)

        return {
            "modules": [n["id"] for n in resolved],
            "blast_radius": {
                "downstream": downstream_count,
                "upstream":   sorted(upstream_labels - {""}),
                "summary":    self._blast_summary(resolved, downstream_count),
            },
            "drift_slice": drift_slice,
            "files_likely_changed": files,
            "openspec_paths_touched": openspec_paths_touched,
        }

    @staticmethod
    def _blast_summary(resolved: list[dict], downstream_count: int) -> str:
        if not resolved:
            return "No modules resolved from query."
        if downstream_count == 0:
            return "Leaf node(s) — no downstream impact."
        return f"{downstream_count} downstream nodes affected; review carefully."

    def _find_existing_openspec_change(self, task: str,
                                        slug_hint: str | None = None) -> str | None:
        """Look for an active openspec/changes/<slug>/ folder matching the task.
        Active = not under archive/. Match heuristic: slug_hint substring in folder
        name, or any KEYWORDS hit shared between task and folder name."""
        changes_dir = self.repo / "openspec" / "changes"
        if not changes_dir.is_dir():
            return None
        slug = slug_hint or _slugify(task)
        slug_tokens = [t for t in slug.split("-") if len(t) >= 4]

        for entry in sorted(changes_dir.iterdir()):
            if not entry.is_dir() or entry.name == "archive":
                continue
            yaml_path = entry / ".openspec.yaml"
            if not yaml_path.is_file():
                continue
            # Match on folder name tokens
            name_low = entry.name.lower()
            if slug in name_low or name_low in slug:
                return entry.name
            if slug_tokens and any(tok in name_low for tok in slug_tokens):
                return entry.name
        return None

    def gate_check(self, modules: list[str] | None = None,
                   current_branch: str | None = None,
                   task: str | None = None) -> dict:
        """Branch gate, OpenSpec gate, personas-synced gate."""
        resolved = self._resolve_modules(modules or [])
        # OpenSpec required?
        openspec_required = any(
            any(node.get("path", "").startswith(p) for p in OPENSPEC_PATHS)
            for node in resolved
        )
        existing = (
            self._find_existing_openspec_change(task or "")
            if openspec_required and task
            else None
        )

        # Branch gate
        branch_verdict = "ok"
        branch_reason = None
        if current_branch:
            if (current_branch in INTEGRATION_BRANCHES
                    or INTEGRATION_BRANCH_RE.match(current_branch)):
                branch_verdict = "block"
                branch_reason = (f"On integration branch '{current_branch}'. "
                                 "Cut a feature branch before any edit.")

        # Personas synced
        agents_dir = self.repo / ".claude" / "agents"
        found = set()
        if agents_dir.is_dir():
            for f in agents_dir.glob("*.md"):
                found.add(f.stem)
        missing = sorted(EXPECTED_PERSONAS - found)

        return {
            "openspec": {
                "required": openspec_required,
                "reason":   ("module path under " + ", ".join(OPENSPEC_PATHS)
                             if openspec_required else None),
                "existing_change_folder": existing,
            },
            "branch": {
                "current":  current_branch,
                "verdict":  branch_verdict,
                "reason":   branch_reason,
            },
            "personas_synced": {
                "verdict":  "ok" if not missing else "missing",
                "found":    len(found & EXPECTED_PERSONAS),
                "expected": len(EXPECTED_PERSONAS),
                "missing":  missing,
            },
        }

    # ---- Edit-target precedence helpers (Rule A + Rule B) ----

    # Substring patterns indicating a module reads cluster-spine config from
    # shared-infra.json. Covers both root-level (include.root.locals.cluster)
    # and per-env (include.env.locals.cluster_config) wiring patterns observed
    # across kuberly-stack modules.
    _CLUSTER_SPINE_PATTERNS = (
        "include.root.locals.cluster",
        "include.env.locals.cluster",
        "local.cluster_config",
        "locals.cluster_config",
    )

    def _classify_input_wiring(self, module_node: dict) -> dict:
        """Classify how a module's terragrunt.hcl sources its inputs.

        Returns dict with:
            reads_cluster: bool — references the cluster spine
                (any of _CLUSTER_SPINE_PATTERNS)
            tg_path:       str — path to the module's terragrunt.hcl, or "" if missing

        Note: JSON-sidecar detection is graph-based, NOT regex-based — the
        presence of `component:<env>/<label>` in the graph is the canonical
        signal that `components/<env>/<label>.json` exists. Module terragrunt.hcl
        wiring uses several conventions (include.root.locals.components,
        jsondecode(file(...))), so substring matches are unreliable.
        """
        path = module_node.get("path", "")
        tg_path = ""
        reads_cluster = False
        if path:
            tg = self.repo / path / "terragrunt.hcl"
            if tg.is_file():
                tg_path = str(tg.relative_to(self.repo))
                try:
                    text = tg.read_text(encoding="utf-8", errors="ignore")
                    reads_cluster = any(p in text for p in self._CLUSTER_SPINE_PATTERNS)
                except OSError:
                    pass
        return {"reads_cluster": reads_cluster, "tg_path": tg_path}

    def _has_json_sidecar(self, env: str, module_label: str) -> bool:
        """True iff `components/<env>/<module_label>.json` is present in the graph."""
        return f"component:{env}/{module_label}" in self.nodes

    def _envs_for_module(self, module_id: str) -> list[str]:
        """List environments where a component invokes the module."""
        envs: list[str] = []
        for e in self.edges:
            if e.get("target") != module_id:
                continue
            src = e.get("source", "")
            src_node = self.nodes.get(src, {})
            if src_node.get("type") in {"component", "application"} and src.startswith(("component:", "app:")):
                # source id shape: "component:<env>/<name>" or "app:<env>/<name>"
                payload = src.split(":", 1)[1]
                env = payload.split("/", 1)[0] if "/" in payload else ""
                if env and env not in envs:
                    envs.append(env)
        return envs

    def _shared_infra_consumers(self) -> list[str]:
        """List module ids whose terragrunt.hcl reads cluster-spine config.

        Cached on the graph instance — scans clouds/*/modules/*/terragrunt.hcl
        once for any of `_CLUSTER_SPINE_PATTERNS`.
        """
        if hasattr(self, "_si_consumers_cache"):
            return self._si_consumers_cache
        consumers: list[str] = []
        for nid, node in self.nodes.items():
            if node.get("type") != "module":
                continue
            path = node.get("path", "")
            if not path:
                continue
            tg = self.repo / path / "terragrunt.hcl"
            if not tg.is_file():
                continue
            try:
                text = tg.read_text(encoding="utf-8", errors="ignore")
                if any(p in text for p in self._CLUSTER_SPINE_PATTERNS):
                    consumers.append(nid)
            except OSError:
                continue
        consumers.sort()
        self._si_consumers_cache = consumers
        return consumers

    def quick_scope(self, task: str,
                    named_modules: list[str] | None = None,
                    target_envs:   list[str] | None = None) -> dict:
        """Server-side scope.md generation. v0.15.0.

        Replaces the `agent-planner` agent for typical tasks
        ('bump X memory', 'add Y database', 'increase Z replicas'). The
        orchestrator calls this, gets a fully-formed scope.md body back,
        writes it directly to `.agents/prompts/<session>/scope.md` — no
        agent dispatch, no 18k-token round-trip.

        Returns:
            scope_md: ready-to-write Markdown body
            modules: resolved module ids
            actionable: bool — True if at least one module has a component
                invoker (and is therefore tune-able)
            recommendation: 'dispatch-agent-infra-ops' | 'stop-target-absent'
                | 'stop-no-instance' | 'fall-back-to-scope-planner'
            blast_summary: one-line summary of impact
            unactionable: list of unactionable module labels
            unresolved: list of named modules that didn't resolve
        """
        scope = self.scope_for_change(named_modules, target_envs)
        modules = scope.get("modules", [])

        # Resolution + actionability — same logic as plan_persona_fanout
        unresolved: list[str] = []
        unactionable: list[str] = []
        if named_modules:
            found_labels = {
                self.nodes[nid].get("label", "").lower()
                for nid in modules if nid in self.nodes
            }
            unresolved = [m for m in named_modules if m.lower() not in found_labels]
        for nid in modules:
            has_consumer = False
            for e in self.edges:
                if e.get("target") != nid:
                    continue
                src_node = self.nodes.get(e.get("source"), {})
                if src_node.get("type") in {"component", "application"}:
                    has_consumer = True
                    break
            # v0.22.0: a module deployed directly via terragrunt apply
            # (state_overlay-only, no components/<env>/<x>.json invoker) has
            # no component edge but DOES have a synthetic source="state"
            # component node — recognize it as actionable.
            if not has_consumer:
                for cnid, cnode in self.nodes.items():
                    if cnode.get("type") != "component":
                        continue
                    if cnode.get("source") != "state":
                        continue
                    if cnode.get("label") == self.nodes.get(nid, {}).get("label"):
                        has_consumer = True
                        break
            if not has_consumer:
                label = self.nodes.get(nid, {}).get("label", nid)
                unactionable.append(label)

        # Build affected-nodes section
        affected: list[str] = []
        for nid in modules:
            node = self.nodes.get(nid, {})
            desc = node.get("description") or "module"
            affected.append(f"- {nid} — direct edit ({desc[:80]})")
            for e in self.edges:
                if e.get("target") == nid:
                    src_node = self.nodes.get(e.get("source"), {})
                    if src_node.get("type") == "component":
                        affected.append(f"- {e['source']} — invokes {nid}")
                    elif src_node.get("type") == "application":
                        rt = src_node.get("runtime", "")
                        affected.append(f"- {e['source']} — uses {nid}{f' (runtime={rt})' if rt else ''}")

        # Blast — scope_for_change returns counts/labels; call blast_radius()
        # on the first module to get the id-keyed dict shape we want.
        downstream_ids: list[str] = []
        upstream_ids: list[str] = []
        blast_summary = scope.get("blast_radius", {}).get("summary", "")
        if modules:
            br = self.blast_radius(modules[0], direction="both", max_depth=3)
            if "error" not in br:
                ds = br.get("downstream") or {}
                downstream_ids = list(ds.keys()) if isinstance(ds, dict) else list(ds)
                us = br.get("upstream") or {}
                upstream_ids = list(us.keys()) if isinstance(us, dict) else list(us)
        files = scope.get("files_likely_changed") or []

        # Recommendation
        if not named_modules:
            recommendation = "fall-back-to-scope-planner"
        elif unresolved and not modules:
            recommendation = "stop-target-absent"
        elif unactionable and len(unactionable) == len(modules):
            recommendation = "stop-no-instance"
        else:
            recommendation = "dispatch-agent-infra-ops"

        # Build scope.md body
        lines = [f"# Scope: {task or 'unspecified'}", ""]
        if recommendation == "stop-target-absent":
            lines += [
                "## STOP — target absent",
                f"Named: {', '.join(unresolved)}. None resolve to graph nodes.",
                "Confirm with user; do NOT dispatch personas.",
                "",
            ]
        elif recommendation == "stop-no-instance":
            lines += [
                "## STOP — no component instance",
                f"Module(s) {', '.join(unactionable)} exist but have zero component invokers.",
                "A 'bump' is moot — there is nothing deployed to bump.",
                "Likely intent: 'create new component instance' (task_kind=new-application/new-database).",
                "",
            ]
        else:
            if affected:
                lines += ["## Affected"] + affected + [""]

            # Edit-target precedence (Rule A) — graph-based JSON detection +
            # cluster-spine regex.
            edit_target_lines: list[str] = []
            shared_infra_modules: list[str] = []
            for nid in modules:
                node = self.nodes.get(nid, {})
                wiring = self._classify_input_wiring(node)
                envs = self._envs_for_module(nid)
                if target_envs:
                    envs = [e for e in envs if e in target_envs] or envs
                label = node.get("label", nid.split("/", 1)[-1])
                tg_path = wiring["tg_path"] or f"clouds/<cloud>/modules/{label}/terragrunt.hcl"
                if wiring["reads_cluster"]:
                    shared_infra_modules.append(nid)
                if envs:
                    for env in envs:
                        if self._has_json_sidecar(env, label):
                            edit_target_lines.append(
                                f"- components/{env}/{label}.json (json-sidecar) — PREFERRED: per-component knobs go here"
                            )
                        else:
                            edit_target_lines.append(
                                f"- {tg_path} (hardcoded — no components/{env}/{label}.json sidecar) — refactor to a JSON-driven try() lookup before editing if value is env-specific; otherwise edit the literal"
                            )
                        if wiring["reads_cluster"]:
                            edit_target_lines.append(
                                f"- components/{env}/shared-infra.json (cluster spine) — ONLY for cluster-level keys (region, account, cluster.*); high blast — see 'Shared-infra blast' below"
                            )
                else:
                    edit_target_lines.append(
                        f"- {tg_path} — no component invoker found; verify intent before editing"
                    )
                edit_target_lines.append(
                    f"- clouds/<cloud>/modules/{label}/variables.tf — LAST RESORT: only when adding a NEW variable to the module's surface"
                )
            if edit_target_lines:
                lines += ["## Edit target"] + edit_target_lines + [""]

            blast_line_down = (f"down={len(downstream_ids)} ids="
                               + (",".join(downstream_ids[:5]) if downstream_ids else "leaf"))
            blast_line_up = (f"up={len(upstream_ids)} ids="
                             + (",".join(upstream_ids[:5]) if upstream_ids else "-"))
            lines += ["## Blast", blast_line_down, blast_line_up, ""]

            # Shared-infra blast warning (Rule B) — only when at least one module
            # reads include.root.locals.cluster.* and the orchestrator may need
            # to edit shared-infra.json.
            if shared_infra_modules:
                consumers = self._shared_infra_consumers()
                consumer_labels = [self.nodes.get(c, {}).get("label", c) for c in consumers]
                lines += [
                    "## Shared-infra blast",
                    f"- modules in scope reading cluster spine: {', '.join(self.nodes.get(m, {}).get('label', m) for m in shared_infra_modules)}",
                    f"- shared-infra.json consumers across stack ({len(consumer_labels)}): {', '.join(consumer_labels[:15])}{' …' if len(consumer_labels) > 15 else ''}",
                    "- editing components/<env>/shared-infra.json affects ALL listed modules — record reason in decisions.md",
                    "",
                ]

            if files:
                lines += ["## Files likely changed"] + [f"- {f}" for f in files[:10]] + [""]
            if unresolved:
                lines += ["## Unresolved (partial)", f"- {', '.join(unresolved)}", ""]
            if unactionable:
                lines += ["## Open questions",
                          f"- {', '.join(unactionable)} — module exists but no component invoker; verify intent",
                          ""]
            if scope.get("openspec_paths_touched"):
                paths = ", ".join(scope["openspec_paths_touched"])
                lines += ["## OpenSpec",
                          f"required (paths under {paths}); confirm change folder exists before agent-infra-ops",
                          ""]

        return {
            "scope_md":        "\n".join(lines).rstrip() + "\n",
            "modules":         modules,
            "actionable":      bool(modules) and recommendation == "dispatch-agent-infra-ops",
            "recommendation":  recommendation,
            "blast_summary":   blast_summary,
            "downstream_ids":  downstream_ids[:10],
            "upstream_ids":    upstream_ids[:10],
            "unresolved":      unresolved,
            "unactionable":    unactionable,
            "files_likely_changed": files[:20],
        }

    def recommend_personas(self, task_kind: str) -> dict:
        """Return the persona DAG for a given task_kind."""
        if task_kind not in PERSONA_DAGS:
            task_kind = "unknown"
        return {
            "task_kind": task_kind,
            "phases": [dict(phase) for phase in PERSONA_DAGS[task_kind]],
        }

    def plan_persona_fanout(self, task: str,
                            named_modules: list[str] | None = None,
                            target_envs:   list[str] | None = None,
                            current_branch: str | None = None,
                            session_name:  str | None = None,
                            task_kind:     str | None = None,
                            with_review:   bool = False) -> dict:
        """One-shot orchestration plan: classify task, build scope slice, run gates,
        emit persona DAG with parallelism markers, and produce a ready-to-paste
        context.md body.

        v0.14.0: review phase removed from default DAGs. Pass `with_review=True`
        (or include the literal word 'review' in the task description) to opt
        in. When opted in, a single merged `pr-reviewer` runs after implement.
        """
        if task_kind:
            confidence = "high"  # caller-overridden
        else:
            task_kind, confidence = self.infer_task_kind(task)

        # Auto-detect review opt-in from task text. Avoids forcing the
        # orchestrator to remember the parameter for "do X and review the
        # diff" prompts. Disable by passing with_review=False explicitly.
        if not with_review and task and " review" in (" " + (task or "").lower()):
            # Cheap heuristic: any unambiguous "review" mention opts in.
            # Conflicts with task_kind=plan-review (already its own flow);
            # leave that one alone.
            if task_kind != "plan-review":
                with_review = True

        scope = self.scope_for_change(named_modules, target_envs)
        gates = self.gate_check(named_modules, current_branch, task)

        # Existence pre-flight. If the caller named modules but NONE of them
        # resolve to a graph node, override the DAG to a no-persona halt so
        # the orchestrator can't fan out personas that would just re-discover
        # the absence. This is the v0.10.2 root-cause guard for the "Loki
        # not deployed but planner+agent-sre both spawned" pattern.
        unresolved_modules: list[str] = []
        unactionable_modules: list[str] = []
        if named_modules:
            found_labels = {
                self.nodes[nid].get("label", "").lower()
                for nid in scope.get("modules", [])
                if nid in self.nodes
            }
            unresolved_modules = [
                m for m in named_modules if m.lower() not in found_labels
            ]
            if not scope.get("modules"):
                # Caller-supplied task_kind is honored everywhere else, but
                # an empty resolution against named modules is a hard signal
                # that overrides classification.
                task_kind = "stop-target-absent"
                confidence = "high"
            else:
                # v0.15.0: actionability pre-flight. Apply ONLY to task_kinds
                # that mutate an EXISTING deployment. For incident (investigation),
                # new-* (we're creating the first instance), unknown, plan-review,
                # leaf modules are normal/expected.
                MUTATES_EXISTING = {"resource-bump", "drift-fix", "cleanup", "cicd"}
                if task_kind in MUTATES_EXISTING:
                    for nid in scope.get("modules", []):
                        # Actionable iff any component or application edge points at it.
                        has_consumer = False
                        for e in self.edges:
                            if e.get("target") != nid:
                                continue
                            src_node = self.nodes.get(e.get("source"), {})
                            if src_node.get("type") in {"component", "application"}:
                                has_consumer = True
                                break
                        # v0.22.0: a module deployed directly via terragrunt
                        # apply (state_overlay-only, no components/<env>/<x>.json
                        # invoker) has no edge but DOES have a synthetic
                        # source="state" component node — recognize it as
                        # actionable.
                        if not has_consumer:
                            mod_label = self.nodes.get(nid, {}).get("label")
                            for cnode in self.nodes.values():
                                if cnode.get("type") != "component":
                                    continue
                                if cnode.get("source") != "state":
                                    continue
                                if cnode.get("label") == mod_label:
                                    has_consumer = True
                                    break
                        if not has_consumer:
                            label = self.nodes.get(nid, {}).get("label", nid)
                            unactionable_modules.append(label)
                    # If EVERY resolved module is unactionable, halt.
                    resolved_count = len(scope.get("modules", []))
                    if unactionable_modules and len(unactionable_modules) == resolved_count:
                        task_kind = "stop-no-instance"
                        confidence = "high"

        recommended = self.recommend_personas(task_kind)
        phases = recommended["phases"]

        # Append the optional review phase. Skipped when:
        #   - the DAG is already a special flow (plan-review, stop-*, unknown — they have their own structure)
        #   - the implement phase doesn't exist (review without code change is moot)
        if with_review and task_kind not in {"plan-review", "stop-target-absent", "stop-no-instance", "unknown"}:
            phases = phases + [
                {"id": "review", "personas": ["pr-reviewer"],
                 "parallel": False, "needs_approval": False},
            ]

        slug = _slugify(session_name or task)
        context_md = self._build_context_md(
            session=slug, task=task, scope=scope, gates=gates,
            unresolved_modules=unresolved_modules,
            unactionable_modules=unactionable_modules,
        )

        return {
            "task_kind":   task_kind,
            "confidence":  confidence,
            "scope":       scope,
            "gates":       gates,
            "phases":      phases,
            "session_slug": slug,
            "context_md":  context_md,
            "unresolved_modules":   unresolved_modules,
            "unactionable_modules": unactionable_modules,
            "with_review": with_review,
        }

    def _build_context_md(self, session: str, task: str,
                          scope: dict, gates: dict,
                          unresolved_modules: list[str] | None = None,
                          unactionable_modules: list[str] | None = None) -> str:
        """Build the Markdown body session_init writes to context.md."""
        # Graph snapshot lines
        glines = [f"- **Modules in scope:** {', '.join(scope['modules']) or '(none resolved)'}"]
        br = scope["blast_radius"]
        glines.append(f"- **Blast radius:** {br['summary']} "
                      f"(upstream: {', '.join(br['upstream']) or '—'})")
        if scope["files_likely_changed"]:
            glines.append("- **Files likely changed:**")
            for f in scope["files_likely_changed"]:
                glines.append(f"- `{f}`")
        if scope["drift_slice"]:
            glines.append(f"- **Drift slice:** {scope['drift_slice']}")

        # Pre-flight halt — named modules absent from graph
        halt_block = ""
        if unresolved_modules and not scope.get("modules"):
            names = ", ".join(f"`{m}`" for m in unresolved_modules)
            halt_block = (
                f"\n## Pre-flight halt — target absent\n"
                f"Named module(s) {names} have **no matching node** in the "
                f"kuberly-platform. DAG: `stop-target-absent` (zero personas). "
                f"Confirm with the user before any persona dispatch.\n"
            )
        elif unactionable_modules:
            # v0.15.0: module exists but is a graph leaf (no component invokes it).
            names = ", ".join(f"`{m}`" for m in unactionable_modules)
            halt_block = (
                f"\n## Pre-flight halt — module not deployed\n"
                f"Module(s) {names} exist in the graph but have **no component "
                f"instance** invoking them. A 'bump' / 'tune' task is moot — "
                f"there is nothing to tune. Likely the user means 'create a "
                f"new component instance' (different task_kind: new-application "
                f"or new-database). Confirm before any persona dispatch.\n"
            )
        elif unresolved_modules:
            # Some resolved, some didn't — informational, not a halt.
            names = ", ".join(f"`{m}`" for m in unresolved_modules)
            halt_block = (
                f"\n## Partial resolution\n"
                f"These named module(s) did not resolve: {names}. Proceeding "
                f"with the modules that did — confirm scope before fanning "
                f"out implementation personas.\n"
            )

        # OpenSpec note
        os_block = ""
        if gates["openspec"]["required"]:
            existing = gates["openspec"]["existing_change_folder"]
            if existing:
                os_block = f"\n## OpenSpec\nExisting change folder: `openspec/changes/{existing}/` — extend rather than create new.\n"
            else:
                os_block = (f"\n## OpenSpec\nRequired (paths under "
                            f"{', '.join(OPENSPEC_PATHS)}). Create folder before "
                            f"delegating to `agent-infra-ops`.\n")

        # Branch note
        br_block = ""
        if gates["branch"]["verdict"] == "block":
            br_block = f"\n## Branch gate\n**BLOCKED** — {gates['branch']['reason']}\n"

        return _CONTEXT_TEMPLATE.format(
            session=session,
            created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            task=task or "_(orchestrator: fill in)_",
            graph_summary="\n".join(glines),
        ) + halt_block + os_block + br_block

    # ------------------------------------------------------------------
    # Session memory: thin wrappers over .agents/prompts/<name>/
    # ------------------------------------------------------------------

    def _session_dir(self, name: str) -> Path:
        slug = _slugify(name)
        return self.repo / ".agents" / "prompts" / slug

    def _validate_session_path(self, name: str, file: str) -> Path:
        """Refuse writes outside the session dir. Resolves symlinks too."""
        base = self._session_dir(name).resolve()
        target = (base / file).resolve()
        if not str(target).startswith(str(base) + os.sep) and target != base:
            raise ValueError(f"path '{file}' resolves outside session dir")
        return target

    def session_init(self, name: str, task: str | None = None,
                     modules: list[str] | None = None,
                     current_branch: str | None = None) -> dict:
        """Create .agents/prompts/<slug>/ with context.md + findings/ + tasks/.
        Returns session_dir path and seeded files."""
        session_dir = self._session_dir(name)
        if session_dir.exists():
            return {"error": f"session already exists: {session_dir}",
                    "session_dir": str(session_dir.relative_to(self.repo))}

        session_dir.mkdir(parents=True)
        (session_dir / "findings").mkdir()
        (session_dir / "tasks").mkdir()
        (session_dir / "findings" / ".gitkeep").touch()
        (session_dir / "tasks" / ".gitkeep").touch()

        # Seed context.md from a fresh plan
        plan = self.plan_persona_fanout(
            task or "(fill in)",
            named_modules=modules,
            current_branch=current_branch,
            session_name=name,
        )
        (session_dir / "context.md").write_text(plan["context_md"])

        # Seed status.json so session_status renders the fanout dashboard
        # immediately, with every persona starting in `queued`.
        self._init_status_json(name, plan)

        return {
            "session_dir": str(session_dir.relative_to(self.repo)),
            "files":       ["context.md", "status.json",
                            "findings/.gitkeep", "tasks/.gitkeep"],
            "task_kind":   plan["task_kind"],
            "confidence":  plan["confidence"],
            "phases":      plan["phases"],
            "session_slug": plan["session_slug"],
        }

    def session_read(self, name: str, file: str) -> dict:
        try:
            target = self._validate_session_path(name, file)
        except ValueError as e:
            return {"error": str(e)}
        if not target.is_file():
            return {"error": f"file not found: {file}"}
        return {
            "file":    file,
            "content": target.read_text(),
            "bytes":   target.stat().st_size,
        }

    def session_write(self, name: str, file: str, content: str) -> dict:
        try:
            target = self._validate_session_path(name, file)
        except ValueError as e:
            return {"error": str(e)}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return {
            "file":  file,
            "bytes": target.stat().st_size,
        }

    def session_list(self, name: str) -> dict:
        session_dir = self._session_dir(name)
        if not session_dir.is_dir():
            return {"error": f"no such session: {name}", "files": []}
        out = []
        for f in sorted(session_dir.rglob("*")):
            if f.is_file():
                out.append({
                    "file":  str(f.relative_to(session_dir)),
                    "bytes": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(
                        f.stat().st_mtime, timezone.utc
                    ).isoformat(timespec="seconds"),
                })
        return {"session": session_dir.name, "files": out}

    # ------------------------------------------------------------------
    # Session status: live fanout dashboard backed by status.json
    # ------------------------------------------------------------------

    _VALID_STATUSES = {"queued", "running", "done", "blocked", "skipped"}

    def _status_path(self, name: str) -> Path:
        return self._session_dir(name) / "status.json"

    def _init_status_json(self, name: str, plan: dict) -> None:
        """Seed status.json from a plan_persona_fanout result. All phases queued."""
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        phases = []
        personas: dict[str, dict] = {}
        for ph in plan.get("phases", []):
            phases.append({
                "id":             ph["id"],
                "personas":       list(ph["personas"]),
                "parallel":       ph.get("parallel", False),
                "needs_approval": ph.get("needs_approval", False),
                "status":         "queued",
            })
            for p in ph["personas"]:
                personas.setdefault(p, {"status": "queued"})
        status = {
            "session":        plan.get("session_slug", _slugify(name)),
            "task_kind":      plan.get("task_kind"),
            "confidence":     plan.get("confidence"),
            "created_at":     now,
            "updated_at":     now,
            "phases":         phases,
            "personas":       personas,
        }
        self._status_path(name).write_text(json.dumps(status, indent=2))

    def _read_status(self, name: str) -> dict | None:
        p = self._status_path(name)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    def _write_status(self, name: str, status: dict) -> None:
        status["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._status_path(name).write_text(json.dumps(status, indent=2))

    def session_set_status(self, name: str, target: str, status: str,
                           kind: str | None = None) -> dict:
        """Update status of a persona or a phase.

        target: persona name (e.g. 'agent-infra-ops') or phase id (e.g. 'implement')
        kind:   'persona' | 'phase' — auto-detected when None.
        status: queued|running|done|blocked|skipped
        """
        if status not in self._VALID_STATUSES:
            return {"error": f"invalid status '{status}'; "
                             f"must be one of {sorted(self._VALID_STATUSES)}"}
        st = self._read_status(name)
        if st is None:
            return {"error": f"no status.json for session '{name}'. "
                             "Call session_init first."}

        # Auto-detect kind
        if kind is None:
            if target in st["personas"]:
                kind = "persona"
            elif any(ph["id"] == target for ph in st["phases"]):
                kind = "phase"
            else:
                return {"error": f"target '{target}' not found in personas or phases"}

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if kind == "persona":
            if target not in st["personas"]:
                return {"error": f"persona '{target}' not in fanout plan"}
            entry = st["personas"][target]
            entry["status"] = status
            if status == "running" and "started_at" not in entry:
                entry["started_at"] = now
            if status in ("done", "blocked", "skipped"):
                entry["ended_at"] = now
            # Roll up phase status from its personas
            for ph in st["phases"]:
                if target not in ph["personas"]:
                    continue
                stats = [st["personas"][p]["status"] for p in ph["personas"]]
                if all(s == "done" for s in stats):
                    ph["status"] = "done"
                elif any(s == "blocked" for s in stats):
                    ph["status"] = "blocked"
                elif any(s == "running" for s in stats):
                    ph["status"] = "running"
                else:
                    ph["status"] = "queued"
        elif kind == "phase":
            ph = next((p for p in st["phases"] if p["id"] == target), None)
            if ph is None:
                return {"error": f"phase '{target}' not in fanout plan"}
            ph["status"] = status
            # Cascade to personas in the phase if a definitive status
            if status in ("done", "blocked", "skipped"):
                for p in ph["personas"]:
                    st["personas"].setdefault(p, {})["status"] = status
                    st["personas"][p]["ended_at"] = now
            elif status == "running":
                for p in ph["personas"]:
                    st["personas"].setdefault(p, {})["status"] = "running"
                    st["personas"][p].setdefault("started_at", now)
        else:
            return {"error": f"unknown kind '{kind}'"}

        self._write_status(name, st)
        return {"target": target, "kind": kind, "status": status,
                "updated_at": st["updated_at"]}

    def session_status(self, name: str) -> dict:
        """Return current fanout status + file listing. Result is also rendered
        as a dashboard card by the MCP renderer."""
        session_dir = self._session_dir(name)
        if not session_dir.is_dir():
            return {"error": f"no such session: {name}"}

        st = self._read_status(name) or {
            "session":   session_dir.name,
            "task_kind": None,
            "phases":    [],
            "personas":  {},
            "_no_status_yet": True,
        }
        listing = self.session_list(name)
        st["files"] = listing.get("files", [])
        return st

    # -- build --
    def build(self):
        self.scan_environments()
        self.scan_applications()
        self.scan_modules()
        self.scan_state_overlays()
        # k8s overlay must run AFTER state overlay so the IRSA bridge can
        # find aws_iam_role resource nodes synthesized from state.
        self.scan_k8s_overlays()
        # Docs overlay can run anytime — its mentions edges target nodes
        # that already exist by this point (modules/components/apps).
        self.scan_docs_overlay()
        self.scan_catalog()
        self.link_components_to_modules()

    def load_from_cache(self, cache_path: Path) -> None:
        """Hydrate `nodes` and `edges` from a previously-generated graph.json.

        Skips the expensive repo walk in `build()`. Stats and drift are
        computed lazily from the loaded nodes/edges so they don't go stale.
        """
        with cache_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"{cache_path} is not a valid graph dump")
        self.nodes = {n["id"]: n for n in (data.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
        self.edges = list(data.get("edges") or [])

    # -- export --
    def to_json(self) -> dict:
        return {
            "nodes": list(self.nodes.values()),
            "edges": self._serializable_edges(),
            "stats": self.compute_stats(),
            "drift": self.cross_env_drift(),
        }


# ---------------------------------------------------------------------------
# Output generators
# ---------------------------------------------------------------------------

def write_graph_json(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    data = graph.to_json()
    path = out_dir / "graph.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    if verbose:
        print(f"wrote {path} ({len(data['nodes'])} nodes, {len(data['edges'])} edges)")


def _node_source_layer(node: dict) -> str:
    """Classify a node into one of {static, state, k8s, docs} for the
    cytoscape viz color encoding. The graph builder doesn't always set
    a `source` attr explicitly, so we derive it from type / id prefix.
    """
    ntype = node.get("type", "") or ""
    nid = node.get("id", "") or ""
    if ntype == "k8s_resource" or nid.startswith("k8s:"):
        return "k8s"
    if ntype == "doc" or nid.startswith("doc:"):
        return "docs"
    if ntype == "resource" or nid.startswith("resource:"):
        return "state"
    if (node.get("source") or "") == "state":
        return "state"
    return "static"


def _node_compound_parent(node: dict, layer: str) -> str | None:
    """Build the cytoscape `parent` id for compound-nesting.

    Hierarchy:
      compound:env:<env>
        |- compound:k8s:<env>/<ns>          (k8s layer only)
        |- compound:layer:<env>/<layer>     (everything else)
    Nodes with no `environment` attr land under `cross-env` instead of
    a real env compound. The `environment` node itself is unparented —
    it's the root the env-compound visually represents.
    """
    if node.get("type") == "environment":
        return None
    env = node.get("environment") or ""
    env_key = env or "cross-env"
    if layer == "k8s":
        ns = node.get("k8s_namespace") or "_cluster"
        return f"compound:k8s:{env_key}/{ns}"
    return f"compound:layer:{env_key}/{layer}"


def _build_cytoscape_elements(data: dict) -> tuple[list, list]:
    """Convert the kuberly-platform graph dump into cytoscape elements.

    Returns (nodes, edges). Compound parents are emitted as nodes
    themselves (cytoscape requires every `parent` id to be a real node)
    with a `compound: True` flag and a `kind` of either `env`, `k8s_ns`,
    or `layer` so the stylesheet can theme them.
    """
    cy_nodes: list[dict] = []
    compound_ids: dict[str, dict] = {}
    # Track which env-compounds we need to materialize.
    seen_envs: set[str] = set()

    for n in data["nodes"]:
        layer = _node_source_layer(n)
        parent = _node_compound_parent(n, layer)
        env = n.get("environment") or ""
        env_key = env or "cross-env"
        if n.get("type") != "environment":
            seen_envs.add(env_key)
        attrs = {k: v for k, v in n.items()
                 if k not in ("id", "label") and v not in (None, "", [], {})}
        cy_nodes.append({
            "data": {
                "id": n["id"],
                "label": n.get("label") or n["id"],
                "type": n.get("type", ""),
                "source_layer": layer,
                "parent": parent,
                "attrs": attrs,
            },
            "classes": layer,
        })
        # Materialize the layer/k8s-ns compound on first sight.
        if parent and parent not in compound_ids:
            if parent.startswith("compound:k8s:"):
                ns = parent.split("/", 1)[1] if "/" in parent else "_cluster"
                compound_ids[parent] = {
                    "data": {
                        "id": parent,
                        "label": f"ns: {ns}",
                        "compound": True,
                        "kind": "k8s_ns",
                        "source_layer": "k8s",
                        "parent": f"compound:env:{env_key}",
                    },
                    "classes": "compound k8s",
                }
            else:
                # compound:layer:<env>/<layer>
                compound_ids[parent] = {
                    "data": {
                        "id": parent,
                        "label": layer,
                        "compound": True,
                        "kind": "layer",
                        "source_layer": layer,
                        "parent": f"compound:env:{env_key}",
                    },
                    "classes": f"compound {layer}",
                }

    # Materialize env compounds last so they exist as parents.
    for env_key in sorted(seen_envs):
        cid = f"compound:env:{env_key}"
        compound_ids.setdefault(cid, {
            "data": {
                "id": cid,
                "label": f"env: {env_key}",
                "compound": True,
                "kind": "env",
                "source_layer": "static",
                "parent": None,
            },
            "classes": "compound env",
        })

    cy_nodes.extend(compound_ids.values())

    cy_edges: list[dict] = []
    for i, e in enumerate(data["edges"]):
        cy_edges.append({
            "data": {
                "id": f"e{i}",
                "source": e["source"],
                "target": e["target"],
                "relation": e.get("relation", ""),
            },
        })
    return cy_nodes, cy_edges


# String.Template — NOT f-strings. The HTML body has many `$${}` JS
# template literals that f-strings would mangle. Every JS `$${...}` is
# escaped as `$$${...}` here (Template treats `$$` as a literal `$`),
# while `$NODES_JSON` and `$EDGES_JSON` are substituted.
_GRAPH_HTML_TEMPLATE = string.Template(r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>kuberly-stack Knowledge Graph</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.30.1/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/layout-base@2.0.1/layout-base.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cose-base@2.2.0/cose-base.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-fcose@2.2.0/cytoscape-fcose.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"></script>
<style>
  :root {
    /* Surfaces (kuberly-web globals.css) */
    --bg:        #090b0d;
    --bg-raised: #11151a;
    --bg-card:   #161b22;
    --bg-elev:   #1c222b;

    /* Type */
    --ink:        #ffffff;
    --ink-soft:   rgba(255,255,255,0.85);
    --ink-mute:   rgba(255,255,255,0.65);
    --ink-faint:  rgba(255,255,255,0.45);
    --ink-line:   rgba(255,255,255,0.10);
    --ink-line-soft: rgba(255,255,255,0.06);

    /* Brand accents */
    --blue:       #1677ff;
    --blue-soft:  #3c89e8;
    --blue-deep:  #1554ad;
    --blue-glow:  rgba(22,119,255,0.22);
    --aws:        #ff9900;
    --aws-soft:   #ffb84d;
    --aws-deep:   #cc7a00;
    --aws-glow:   rgba(255,153,0,0.22);
    --amber:      #d89614;
    --amber-warm: #f5b042;

    --radius:     14px;
    --radius-lg:  22px;

    /* Lifts */
    --lift-blue:   0 30px 80px -40px rgba(22,119,255,0.18);
    --lift-modal:  0 30px 80px -30px rgba(0,0,0,0.6);

    /* Fonts */
    --font-sans: "Geist", -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", system-ui, sans-serif;
    --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    font-family: var(--font-sans);
    background: var(--bg);
    color: var(--ink);
    overflow: hidden;
  }
  #topbar {
    position: fixed;
    top: 0; left: 0; right: 0;
    height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 0 20px;
    background: rgba(15,20,25,0.72);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--ink-line);
    z-index: 10;
    font-size: 13px;
  }
  #topbar .brand { display: flex; align-items: center; gap: 12px; }
  #topbar .brand .logo { display: inline-flex; color: var(--ink); }
  #topbar .brand .wordmark {
    font-family: var(--font-sans);
    font-weight: 600; font-size: 15px;
    letter-spacing: -0.02em; color: var(--ink);
  }
  #topbar .eyebrow {
    font-family: var(--font-mono);
    font-size: 10px; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--ink-faint);
    padding: 2px 8px; border: 1px solid var(--ink-line); border-radius: 999px;
  }
  #topbar .controls { display: flex; align-items: center; gap: 12px; }
  #topbar .stats { color: var(--ink-mute); font-size: 12px; font-family: var(--font-mono); }
  #search {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 10px;
    border-radius: var(--radius);
    font-family: var(--font-sans);
    font-size: 13px;
    width: 220px;
    outline: none;
  }
  #search:focus { border-color: var(--blue); }
  .layer-toggles { display: flex; gap: 6px; align-items: center; }
  .layer-toggle {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 10px; border-radius: 999px;
    font-family: var(--font-mono); font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--ink-soft);
    border: 1px solid var(--ink-line);
    cursor: pointer; user-select: none;
    transition: all 0.15s ease;
  }
  .layer-toggle.active { background: rgba(255,255,255,0.04); }
  .layer-toggle.inactive { opacity: 0.45; }
  .layer-toggle input { display: none; }
  .layer-toggle .dot { width: 6px; height: 6px; border-radius: 50%; }
  .layer-toggle[data-layer=static] .dot { background: var(--blue); }
  .layer-toggle[data-layer=state]  .dot { background: var(--aws); }
  .layer-toggle[data-layer=k8s]    .dot { background: var(--amber); }
  .layer-toggle[data-layer=docs]   .dot { background: var(--ink-mute); }
  #layout-select {
    background: rgba(255,255,255,0.04);
    color: var(--ink);
    border: 1px solid var(--ink-line);
    padding: 6px 10px;
    border-radius: var(--radius);
    font-family: var(--font-sans);
    font-size: 13px;
    cursor: pointer;
  }
  #cy {
    position: fixed;
    top: 56px; left: 0; right: 0; bottom: 0;
    background-color: var(--bg);
    background-image: radial-gradient(circle, rgba(255,255,255,0.05) 1px, transparent 1.4px);
    background-size: 22px 22px;
  }
  #sidebar {
    position: fixed;
    top: 72px; right: 16px; bottom: 16px;
    width: 320px;
    background: var(--bg-card);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius-lg);
    transform: translateX(calc(100% + 32px));
    transition: transform 180ms ease-out;
    overflow-y: auto;
    padding: 24px;
    font-size: 13px;
    color: var(--ink);
    box-shadow: var(--lift-modal);
    z-index: 9;
  }
  #sidebar.open { transform: translateX(0); }
  #sidebar h2 {
    font-size: 13px; font-weight: 500; letter-spacing: -0.01em;
    color: var(--ink); margin-bottom: 12px;
    word-break: break-all; line-height: 1.3;
    font-family: var(--font-mono);
  }
  #sidebar .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
  #sidebar .chip {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 8px; border-radius: 999px;
    font-family: var(--font-mono); font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--ink-mute);
    border: 1px solid var(--ink-line);
    background: rgba(255,255,255,0.04);
  }
  #sidebar .chip.layer-static { color: var(--blue);       border-color: rgba(22,119,255,0.30); background: rgba(22,119,255,0.08); }
  #sidebar .chip.layer-state  { color: var(--aws);        border-color: rgba(255,153,0,0.30); background: rgba(255,153,0,0.08); }
  #sidebar .chip.layer-k8s    { color: var(--amber-warm); border-color: rgba(245,176,66,0.30); background: rgba(245,176,66,0.08); }
  #sidebar .chip.layer-docs   { color: var(--ink-mute);   border-color: var(--ink-line);      background: rgba(255,255,255,0.04); }
  #sidebar h3 {
    font-family: var(--font-mono);
    font-size: 10px; font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.18em;
    color: var(--ink-faint);
    margin: 14px 0 6px;
  }
  #sidebar details {
    background: rgba(255,255,255,0.02);
    border: 1px solid var(--ink-line);
    border-radius: var(--radius);
    padding: 8px 10px;
  }
  #sidebar details summary {
    cursor: pointer; font-size: 12px; color: var(--ink-mute);
    font-family: var(--font-mono);
  }
  #sidebar .attrs { font-family: var(--font-mono); font-size: 11px; line-height: 1.5; word-break: break-all; }
  #sidebar .attrs .k { color: var(--ink-faint); }
  #sidebar .attrs .v { color: var(--ink); }
  #sidebar .edges a {
    display: block; padding: 4px 6px; border-radius: 4px;
    color: var(--ink-soft); text-decoration: none; font-size: 12px;
    font-family: var(--font-mono);
    word-break: break-all;
  }
  #sidebar .edges a:hover { background: rgba(22,119,255,0.10); color: var(--blue); }
  #sidebar .edges .rel { color: var(--ink-faint); font-size: 10px; margin-left: 4px; }
  #sidebar .actions { display: flex; gap: 8px; margin-top: 14px; }
  #sidebar .btn {
    flex: 1;
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    padding: 8px 14px; border-radius: var(--radius);
    background: var(--blue); color: white; border: none;
    font-family: var(--font-sans); font-weight: 500; font-size: 13px;
    cursor: pointer; transition: background 0.15s ease;
  }
  #sidebar .btn:hover { background: var(--blue-soft); }
  #sidebar .btn:active { background: var(--blue-deep); }
  #sidebar .btn.ghost {
    background: transparent; color: var(--ink-soft);
    border: 1px solid var(--ink-line);
  }
  #sidebar .btn.ghost:hover { background: rgba(255,255,255,0.04); border-color: var(--ink-line-soft); }
  #sidebar #close-btn {
    position: absolute; top: 12px; right: 12px;
    background: transparent; border: none; color: var(--ink-faint);
    cursor: pointer; font-size: 18px; padding: 4px 8px;
    flex: none;
  }
  #sidebar #close-btn:hover { color: var(--ink); }
  .pulse { animation: pulse 0.9s ease-in-out 3; }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(22,119,255,0.6); }
    50%      { box-shadow: 0 0 0 6px rgba(22,119,255,0.0); }
  }
</style>
</head>
<body>

<div id="topbar">
  <div class="brand">
    <span class="logo">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
        <path d="M11.3647 2.92733C11.7021 2.73258 12.1173 2.73119 12.4559 2.92369L19.8781 7.14305C20.2213 7.33813 20.4333 7.70247 20.4333 8.09721V16.5758C20.4333 16.9679 20.224 17.3303 19.8844 17.5263L19.5582 17.7146V18.6654C19.5582 19.4476 19.3772 20.2041 19.0449 20.8836L21.1282 19.6809C22.2376 19.0404 22.9211 17.8568 22.9211 16.5758V8.09721C22.9211 6.80772 22.2286 5.61756 21.1076 4.98029L13.6854 0.760927C12.5793 0.132111 11.2228 0.136639 10.1208 0.772828L7.66167 2.19263C6.55236 2.83309 5.86899 4.01672 5.86899 5.29765V13.7891C5.86899 15.07 6.55236 16.2536 7.66167 16.8941L12.2536 19.5452L14.1436 18.4542V17.7638L8.90558 14.7396C8.56599 14.5435 8.3568 14.1812 8.3568 13.7891V5.29765C8.3568 4.90553 8.56599 4.54319 8.90558 4.34713L11.3647 2.92733Z" fill="currentColor"/>
        <path d="M11.6634 4.44474L9.82021 5.5089V6.25864L15.0519 9.23272C15.395 9.42781 15.607 9.79214 15.607 10.1869V18.6655C15.607 19.0576 15.3978 19.4199 15.0582 19.616L12.5307 21.0751C12.1911 21.2711 11.7727 21.2711 11.4332 21.075L4.07931 16.8293C3.73972 16.6332 3.53053 16.2709 3.53053 15.8788V7.38732C3.53053 6.9952 3.73972 6.63287 4.07931 6.43681L4.40558 6.24844V5.29767C4.40558 4.51538 4.58658 3.75886 4.91902 3.07933L2.83541 4.2823C1.72609 4.92277 1.04272 6.10639 1.04272 7.38732V15.8788C1.04272 17.1597 1.72609 18.3433 2.83541 18.9838L10.1893 23.2295C11.2985 23.87 12.6652 23.87 13.7745 23.2296L16.302 21.7706C17.4114 21.1301 18.0948 19.9464 18.0948 18.6655V10.1869C18.0948 8.8974 17.4023 7.70723 16.2813 7.06996L11.6634 4.44474Z" fill="currentColor"/>
      </svg>
    </span>
    <span class="wordmark">kuberly-graph</span>
    <span class="eyebrow">v0.25.0</span>
  </div>
  <div class="controls">
    <input id="search" type="text" placeholder="Search nodes..." autocomplete="off" />
    <div class="layer-toggles">
      <label class="layer-toggle active" data-layer="static"><input type="checkbox" data-layer="static" checked><span class="dot"></span>static</label>
      <label class="layer-toggle active" data-layer="state"><input type="checkbox" data-layer="state" checked><span class="dot"></span>state</label>
      <label class="layer-toggle inactive" data-layer="k8s"><input type="checkbox" data-layer="k8s"><span class="dot"></span>k8s</label>
      <label class="layer-toggle active" data-layer="docs"><input type="checkbox" data-layer="docs" checked><span class="dot"></span>docs</label>
    </div>
    <select id="layout-select" title="Layout algorithm">
      <option value="fcose" selected>fcose (compound force)</option>
      <option value="dagre">dagre (hierarchy)</option>
      <option value="concentric">concentric</option>
    </select>
    <span class="stats" id="stats"></span>
  </div>
</div>

<div id="cy"></div>

<aside id="sidebar">
  <button id="close-btn" title="Close (ESC)">&times;</button>
  <div id="sidebar-body"></div>
</aside>

<script>
const NODES = $NODES_JSON;
const EDGES = $EDGES_JSON;

// Single source of truth: read brand tokens from CSS custom properties at
// runtime. Cytoscape inline styles can't use var(), so we read once and
// inject as constants into the cytoscape style array.
const _root = getComputedStyle(document.documentElement);
function _v(name) { return _root.getPropertyValue(name).trim(); }

const BRAND = {
  bg:         _v("--bg"),
  ink:        _v("--ink"),
  inkMute:    "rgba(255,255,255,0.65)",  // --ink-mute (computed value)
  inkFaint:   "rgba(255,255,255,0.45)",
  inkLine:    "rgba(255,255,255,0.10)",
  inkLineHi:  "rgba(255,255,255,0.18)",
  blue:       _v("--blue"),
  blueSoft:   _v("--blue-soft"),
  aws:        _v("--aws"),
  amber:      _v("--amber"),
  amberWarm:  _v("--amber-warm"),
};

const LAYER_COLORS = {
  static: BRAND.blue,       // declared HCL/JSON — brand primary
  state:  BRAND.aws,        // terraform-managed AWS resources
  k8s:    BRAND.amber,      // live cluster workloads
  docs:   BRAND.inkMute,    // metadata — opacity hierarchy
};

document.getElementById("stats").textContent =
  NODES.filter(n => !n.data.compound).length + " nodes · " + EDGES.length + " edges";

const cy = cytoscape({
  container: document.getElementById("cy"),
  elements: { nodes: NODES, edges: EDGES },
  wheelSensitivity: 0.2,
  style: [
    {
      selector: "node",
      style: {
        "label": "data(label)",
        "font-size": 9,
        "font-family": "Geist, -apple-system, BlinkMacSystemFont, system-ui, sans-serif",
        "color": BRAND.ink,
        "text-valign": "center",
        "text-halign": "center",
        "text-outline-color": BRAND.bg,
        "text-outline-width": 2,
        "background-color": "#999",
        "width": 18,
        "height": 18,
        "border-width": 0,
      },
    },
    { selector: "node.static", style: { "background-color": LAYER_COLORS.static } },
    { selector: "node.state",  style: { "background-color": LAYER_COLORS.state  } },
    { selector: "node.k8s",    style: { "background-color": LAYER_COLORS.k8s    } },
    { selector: "node.docs",   style: { "background-color": LAYER_COLORS.docs   } },
    {
      selector: "node:parent",
      style: {
        "background-color": "rgba(255,255,255,0.04)",
        "background-opacity": 1,
        "border-color": BRAND.inkLine,
        "border-width": 1,
        "shape": "round-rectangle",
        "label": "data(label)",
        "text-valign": "top",
        "text-halign": "center",
        "font-size": 10,
        "font-family": "Geist, -apple-system, BlinkMacSystemFont, system-ui, sans-serif",
        "color": BRAND.inkFaint,
        "padding": 14,
        "min-zoomed-font-size": 8,
      },
    },
    {
      selector: "node:parent.env",
      style: {
        "border-color": BRAND.inkLineHi,
        "background-color": "rgba(255,255,255,0.02)",
        "font-size": 12,
        "color": BRAND.ink,
      },
    },
    // k8s OFF by default — hide both leaf nodes and their compound parents.
    { selector: "node.k8s.layer-off", style: { "display": "none" } },
    { selector: "node.static.layer-off", style: { "display": "none" } },
    { selector: "node.state.layer-off", style: { "display": "none" } },
    { selector: "node.docs.layer-off", style: { "display": "none" } },
    {
      selector: "edge",
      style: {
        "width": 1.2,
        "line-color": BRAND.inkLine,
        "target-arrow-color": BRAND.inkLine,
        "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "arrow-scale": 0.7,
        "opacity": 0.7,
      },
    },
    { selector: "edge.dim", style: { "opacity": 0.08 } },
    { selector: "node.dim", style: { "opacity": 0.15 } },
    {
      selector: "node:selected",
      style: {
        "border-width": 3,
        "border-color": BRAND.blue,
        "background-color": "data(color)",
      },
    },
    { selector: "node.match", style: { "border-width": 2, "border-color": BRAND.blue } },
    {
      selector: "node.upstream",
      style: { "border-width": 3, "border-color": BRAND.aws, "background-color": BRAND.aws },
    },
    {
      selector: "node.downstream",
      style: { "border-width": 3, "border-color": BRAND.blue },
    },
    {
      selector: "edge.highlight",
      style: { "line-color": BRAND.blue, "target-arrow-color": BRAND.blue, "opacity": 1, "width": 2 },
    },
  ],
  layout: { name: "fcose", quality: "default", animate: false, randomize: true,
            nodeSeparation: 80, idealEdgeLength: 80, packComponents: true },
});

// Apply default k8s OFF state via `layer-off` class on every k8s node.
function applyLayerVisibility(layer, on) {
  cy.batch(() => {
    cy.nodes("." + layer).forEach(n => {
      if (on) n.removeClass("layer-off");
      else    n.addClass("layer-off");
    });
  });
}
applyLayerVisibility("k8s", false);

document.querySelectorAll(".layer-toggles input").forEach(cb => {
  cb.addEventListener("change", () => {
    applyLayerVisibility(cb.dataset.layer, cb.checked);
    const pill = cb.closest(".layer-toggle");
    if (pill) {
      pill.classList.toggle("active", cb.checked);
      pill.classList.toggle("inactive", !cb.checked);
    }
  });
});

// Layout switcher.
function runLayout(name) {
  let opts = { name, animate: false, fit: true };
  if (name === "fcose") {
    opts = { ...opts, quality: "default", randomize: true,
             nodeSeparation: 80, idealEdgeLength: 80, packComponents: true };
  } else if (name === "dagre") {
    opts = { ...opts, rankDir: "TB", nodeSep: 30, rankSep: 60 };
  } else if (name === "concentric") {
    opts = { ...opts, concentric: n => n.degree(), levelWidth: () => 1 };
  }
  cy.layout(opts).run();
}
document.getElementById("layout-select").addEventListener("change", e => {
  runLayout(e.target.value);
});

// Kick off the initial layout so all 1k+ nodes don't stack at (0,0).
runLayout("fcose");

// Search — fuzzy substring match on id + label.
const searchEl = document.getElementById("search");
searchEl.addEventListener("input", () => {
  const q = searchEl.value.trim().toLowerCase();
  cy.nodes().removeClass("match pulse");
  if (!q) return;
  const matches = cy.nodes().filter(n => {
    if (n.data("compound")) return false;
    const id = (n.id() || "").toLowerCase();
    const lbl = (n.data("label") || "").toLowerCase();
    return id.includes(q) || lbl.includes(q);
  });
  matches.addClass("match pulse");
});
searchEl.addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  const first = cy.nodes(".match").first();
  if (first && first.length) {
    cy.animate({ center: { eles: first }, zoom: 1.3 }, { duration: 250 });
  }
});

// Sidebar.
const sidebar = document.getElementById("sidebar");
const sidebarBody = document.getElementById("sidebar-body");

function renderSidebar(node) {
  const data = node.data();
  const layer = data.source_layer || "static";
  const incoming = cy.edges(`[target = "$${data.id}"]`);
  const outgoing = cy.edges(`[source = "$${data.id}"]`);
  const attrs = data.attrs || {};
  const attrEntries = Object.entries(attrs).filter(([k]) => k !== "label" && k !== "id");

  let attrHtml = "";
  if (attrEntries.length) {
    attrHtml = `<details $${attrEntries.length <= 4 ? "open" : ""}><summary>$${attrEntries.length} attribute$${attrEntries.length === 1 ? "" : "s"}</summary><div class="attrs">` +
      attrEntries.map(([k, v]) => {
        const vs = typeof v === "object" ? JSON.stringify(v) : String(v);
        return `<div><span class="k">$${k}:</span> <span class="v">$${escapeHtml(vs)}</span></div>`;
      }).join("") + `</div></details>`;
  }

  const inHtml = incoming.map(e => {
    const src = e.source().id();
    const rel = e.data("relation") || "";
    return `<a href="#" data-jump="$${src}">$${escapeHtml(src)}<span class="rel">[$${escapeHtml(rel)}]</span></a>`;
  }).join("") || `<div class="rel">none</div>`;
  const outHtml = outgoing.map(e => {
    const tgt = e.target().id();
    const rel = e.data("relation") || "";
    return `<a href="#" data-jump="$${tgt}">$${escapeHtml(tgt)}<span class="rel">[$${escapeHtml(rel)}]</span></a>`;
  }).join("") || `<div class="rel">none</div>`;

  sidebarBody.innerHTML = `
    <h2>$${escapeHtml(data.id)}</h2>
    <div class="chips">
      $${data.type ? `<span class="chip">$${escapeHtml(data.type)}</span>` : ""}
      <span class="chip layer-$${layer}">$${layer}</span>
    </div>
    $${attrHtml}
    <h3>Incoming ($${incoming.length})</h3>
    <div class="edges">$${inHtml}</div>
    <h3>Outgoing ($${outgoing.length})</h3>
    <div class="edges">$${outHtml}</div>
    <div class="actions">
      <button id="blast-btn" class="btn">Show blast radius</button>
      <button id="center-btn" class="btn ghost">Center</button>
    </div>
  `;
  sidebar.classList.add("open");

  sidebarBody.querySelectorAll("a[data-jump]").forEach(a => {
    a.addEventListener("click", ev => {
      ev.preventDefault();
      const target = cy.getElementById(a.dataset.jump);
      if (target && target.length) {
        cy.nodes().unselect();
        target.select();
        cy.animate({ center: { eles: target }, zoom: 1.3 }, { duration: 250 });
        renderSidebar(target);
      }
    });
  });
  document.getElementById("blast-btn").addEventListener("click", () => showBlast(node));
  document.getElementById("center-btn").addEventListener("click", () => {
    cy.animate({ center: { eles: node }, zoom: 1.3 }, { duration: 250 });
  });
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function showBlast(node) {
  cy.elements().addClass("dim");
  // Walk upstream (sources -> us) and downstream (us -> sinks).
  const upstream = node.predecessors("node");
  const downstream = node.successors("node");
  upstream.removeClass("dim").addClass("upstream");
  downstream.removeClass("dim").addClass("downstream");
  node.removeClass("dim");
  node.predecessors("edge").removeClass("dim").addClass("highlight");
  node.successors("edge").removeClass("dim").addClass("highlight");
}

function clearBlast() {
  cy.elements().removeClass("dim upstream downstream highlight");
}

cy.on("tap", "node", evt => {
  const n = evt.target;
  if (n.data("compound")) {
    // Click on compound: toggle child visibility (cheap collapse).
    const kids = n.children();
    if (kids.first().style("display") === "none") {
      kids.style("display", "element");
    } else {
      kids.style("display", "none");
    }
    return;
  }
  clearBlast();
  renderSidebar(n);
});

cy.on("tap", evt => {
  if (evt.target === cy) {
    sidebar.classList.remove("open");
    cy.nodes().unselect();
    clearBlast();
  }
});

document.getElementById("close-btn").addEventListener("click", () => {
  sidebar.classList.remove("open");
  cy.nodes().unselect();
  clearBlast();
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    sidebar.classList.remove("open");
    cy.nodes().unselect();
    clearBlast();
    cy.nodes().removeClass("match pulse");
    searchEl.value = "";
  }
});
</script>
</body>
</html>
""")


def write_graph_html(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    """Render the cytoscape-based interactive viz to <out_dir>/graph.html.

    Replaces the v0.22 force-graph viz. All four overlays (static,
    state, k8s, docs) are color-coded and compound-nested by env →
    namespace / layer. k8s layer is OFF by default (864 nodes is noisy);
    user toggles it on via the topbar checkbox.
    """
    data = graph.to_json()
    cy_nodes, cy_edges = _build_cytoscape_elements(data)
    html = _GRAPH_HTML_TEMPLATE.substitute(
        NODES_JSON=json.dumps(cy_nodes),
        EDGES_JSON=json.dumps(cy_edges),
    )
    path = out_dir / "graph.html"
    path.write_text(html)
    if verbose:
        print(f"wrote {path}")


def write_graph_report(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    stats = graph.compute_stats()
    drift = graph.cross_env_drift()

    lines = [
        "# kuberly-stack Knowledge Graph Report\n",
        f"**Nodes:** {stats['node_count']} | **Edges:** {stats['edge_count']}\n",
        "## Node Types\n",
        "| Type | Count |",
        "|------|-------|",
    ]
    for t, c in sorted(stats["type_counts"].items()):
        lines.append(f"| {t} | {c} |")

    lines.append("\n## Critical Nodes (most depended upon)\n")
    lines.append("| Node | In-degree | Out-degree |")
    lines.append("|------|-----------|------------|")
    for nid, ind, outd in stats["critical_nodes"]:
        if ind > 0:
            lines.append(f"| `{nid}` | {ind} | {outd} |")

    lines.append("\n## Longest Dependency Chains\n")
    for i, chain in enumerate(stats["longest_chains"], 1):
        readable = " -> ".join(c.replace("module:", "") for c in chain)
        lines.append(f"{i}. `{readable}`")

    lines.append("\n## Cross-Environment Drift\n")
    if not drift["components"] and not drift["applications"]:
        lines.append("No drift detected — all environments have the same components and apps.\n")
    else:
        if drift["components"]:
            lines.append("### Components missing by environment\n")
            for env, missing in sorted(drift["components"].items()):
                lines.append(f"- **{env}**: {', '.join(missing)}")
        if drift["applications"]:
            lines.append("\n### Applications missing by environment\n")
            for env, missing in sorted(drift["applications"].items()):
                lines.append(f"- **{env}**: {', '.join(missing)}")

    # Blast radius section
    lines.append("\n## Blast Radius: shared-infra\n")
    lines.append("Changing `shared-infra.json` in any environment affects these components:\n")
    for nid, node in sorted(graph.nodes.items()):
        if node["type"] == "shared-infra":
            env = node["environment"]
            affected = [e["target"] for e in graph.edges
                        if e["source"] == nid and e["relation"] == "configures"]
            lines.append(f"- **{env}**: {len(affected)} components — "
                         + ", ".join(a.split("/")[-1] for a in affected))

    lines.append("")
    path = out_dir / "GRAPH_REPORT.md"
    path.write_text("\n".join(lines))
    if verbose:
        print(f"wrote {path}")


def write_mermaid_dag(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    """Generate Mermaid diagrams: module DAG, per-env, and full overview."""

    def sanitize(nid: str) -> str:
        """Make node id safe for Mermaid."""
        return re.sub(r'[^a-zA-Z0-9_]', '_', nid)

    # --- 1. Module dependency DAG (most useful) ---
    lines = ["graph LR"]
    # Style classes
    lines.append("    classDef foundation fill:#FF9800,stroke:#E65100,color:#000")
    lines.append("    classDef k8s fill:#2196F3,stroke:#1565C0,color:#fff")
    lines.append("    classDef data fill:#4CAF50,stroke:#2E7D32,color:#fff")
    lines.append("    classDef observability fill:#9C27B0,stroke:#6A1B9A,color:#fff")
    lines.append("    classDef app fill:#FF5722,stroke:#BF360C,color:#fff")
    lines.append("")

    # Categorize modules for styling
    foundation = {"vpc", "eks", "gke", "aks", "vnet", "resource_group", "identity", "ecs_infra"}
    observability = {"prometheus", "grafana", "loki", "alloy", "tempo", "prom-label-proxy",
                     "cloudwatch", "cloudtrail"}
    data_stores = {"aurora", "rds", "redis", "mongodb", "clickhouse", "valkey", "mysql",
                   "cloudnative_pg", "nats", "temporal", "sqs_queue"}
    app_modules = {"ecs_app", "lambda_app", "bedrock_agentcore_app", "static_websites",
                   "lambda_infra"}

    module_nodes = {nid: n for nid, n in graph.nodes.items() if n["type"] == "module"}
    dep_edges = [e for e in graph.edges if e.get("relation") == "depends_on"]

    # Group by provider using subgraphs
    providers = defaultdict(list)
    for nid, n in module_nodes.items():
        providers[n.get("provider", "unknown")].append((nid, n))

    for provider, mods in sorted(providers.items()):
        lines.append(f"subgraph {provider}[{provider.upper()}]")
        for nid, n in mods:
            sid = sanitize(nid)
            label = n["label"]
            desc = n.get("description", "")
            display = f"{label}" + (f"<br/><small>{desc[:40]}</small>" if desc else "")
            lines.append(f'        {sid}["{display}"]')
        lines.append("    end")
        lines.append("")

    # Edges
    for e in dep_edges:
        s = sanitize(e["source"])
        t = sanitize(e["target"])
        lines.append(f"{s} --> {t}")

    # Apply styles
    lines.append("")
    for nid, n in module_nodes.items():
        sid = sanitize(nid)
        label = n["label"]
        if label in foundation:
            lines.append(f"class {sid} foundation")
        elif label in observability:
            lines.append(f"class {sid} observability")
        elif label in data_stores:
            lines.append(f"class {sid} data")
        elif label in app_modules:
            lines.append(f"class {sid} app")
        else:
            lines.append(f"class {sid} k8s")

    path = out_dir / "module_dag.mmd"
    path.write_text("\n".join(lines) + "\n")
    if verbose:
        print(f"wrote {path} (module dependency DAG)")

    # --- 2. Per-environment diagrams ---
    env_nodes = {nid: n for nid, n in graph.nodes.items()
                 if n.get("type") in ("environment", "component", "shared-infra", "application")}
    envs = {n["label"] for n in graph.nodes.values() if n["type"] == "environment"}

    for env in sorted(envs):
        elines = ["graph TD"]
        elines.append("    classDef sharedInfra fill:#FF9800,stroke:#E65100,color:#000")
        elines.append("    classDef component fill:#2196F3,stroke:#1565C0,color:#fff")
        elines.append("    classDef application fill:#9C27B0,stroke:#6A1B9A,color:#fff")
        elines.append("")

        env_sid = sanitize(f"env_{env}")
        elines.append(f'    {env_sid}{{{{{env}}}}}')

        for nid, n in env_nodes.items():
            if n.get("environment") != env:
                continue
            sid = sanitize(nid)
            label = n["label"]
            ntype = n["type"]
            if ntype == "shared-infra":
                acct = n.get("account_id", "")
                region = n.get("region", "")
                elines.append(f'    {sid}[["shared-infra<br/>{acct}<br/>{region}"]]')
                elines.append(f"class {sid} sharedInfra")
            elif ntype == "component":
                elines.append(f'    {sid}[{label}]')
                elines.append(f"class {sid} component")
            elif ntype == "application":
                port = n.get("port", "")
                elines.append(f'    {sid}([{label}:{port}])')
                elines.append(f"class {sid} application")

        # Edges within this env
        for e in graph.edges:
            src_node = graph.nodes.get(e["source"], {})
            tgt_node = graph.nodes.get(e["target"], {})
            if src_node.get("environment") == env or tgt_node.get("environment") == env:
                if e["source"] in env_nodes and e["target"] in env_nodes:
                    ss = sanitize(e["source"])
                    ts = sanitize(e["target"])
                    rel = e.get("relation", "")
                    elines.append(f"{ss} -->|{rel}| {ts}")

        env_path = out_dir / f"env_{env}.mmd"
        env_path.write_text("\n".join(elines) + "\n")
        if verbose:
            print(f"wrote {env_path} ({env} environment)")

    # --- 3. Blast radius diagram for shared-infra ---
    for nid, node in graph.nodes.items():
        if node["type"] != "shared-infra":
            continue
        env = node["environment"]
        br = graph.blast_radius(nid, direction="downstream", max_depth=3)
        if "error" in br:
            continue
        blines = ["graph TD"]
        blines.append("    classDef root fill:#F44336,stroke:#B71C1C,color:#fff")
        blines.append("    classDef d1 fill:#FF9800,stroke:#E65100,color:#000")
        blines.append("    classDef d2 fill:#FFC107,stroke:#FF8F00,color:#000")
        blines.append("    classDef d3 fill:#FFEB3B,stroke:#F9A825,color:#000")
        blines.append("")

        root_sid = sanitize(nid)
        blines.append(f'    {root_sid}[["shared-infra ({env})"]]')
        blines.append(f"class {root_sid} root")

        for dnid, info in br.get("downstream", {}).items():
            dsid = sanitize(dnid)
            dlabel = graph.nodes.get(dnid, {}).get("label", dnid)
            depth = info["depth"]
            blines.append(f'    {dsid}[{dlabel}]')
            blines.append(f"class {dsid} d{min(depth, 3)}")
            # Connect to parent (find edge)
            for e in graph.edges:
                if e["target"] == dnid and (e["source"] == nid or e["source"] in br.get("downstream", {})):
                    ps = sanitize(e["source"])
                    blines.append(f"{ps} --> {dsid}")
                    break

        br_path = out_dir / f"blast_{env}.mmd"
        br_path.write_text("\n".join(blines) + "\n")
        if verbose:
            print(f"wrote {br_path} (blast radius: {env})")


def format_blast_radius(result: dict) -> str:
    """Format blast radius result as human-readable text."""
    if "error" in result:
        return f"Error: {result['error']}"

    lines = [f"Blast radius for: {result['node']}"]
    node_info = result.get("node_info", {})
    lines.append(f"Type: {node_info.get('type', '?')} | Label: {node_info.get('label', '?')}")
    lines.append("")

    if "downstream" in result:
        lines.append(f"DOWNSTREAM (affected if this changes): {result['downstream_count']} nodes")
        by_depth = defaultdict(list)
        for nid, info in result["downstream"].items():
            by_depth[info["depth"]].append(f"{nid} ({info.get('type', '?')})")
        for d in sorted(by_depth):
            lines.append(f"Depth {d}:")
            for item in by_depth[d]:
                lines.append(f"{item}")

    if "upstream" in result:
        lines.append(f"\nUPSTREAM (changes here affect this node): {result['upstream_count']} nodes")
        by_depth = defaultdict(list)
        for nid, info in result["upstream"].items():
            by_depth[info["depth"]].append(f"{nid} ({info.get('type', '?')})")
        for d in sorted(by_depth):
            lines.append(f"Depth {d}:")
            for item in by_depth[d]:
                lines.append(f"{item}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_graph(repo_path: str) -> KuberlyPlatform:
    """Build a graph from the repo on disk (used by the `generate` CLI path).

    Walks the repo tree and produces a fresh in-memory graph. Expensive on
    repos with many modules — for the MCP server, prefer `load_graph_cached`
    which reads the pre-computed `.kuberly/graph.json`.
    """
    repo = Path(repo_path).resolve()
    if not (repo / "root.hcl").exists():
        print(f"Error: {repo} does not look like a kuberly-stack repo (no root.hcl)")
        sys.exit(1)
    g = KuberlyPlatform(str(repo))
    g.build()
    return g


def load_graph_cached(repo_path: str) -> KuberlyPlatform:
    """Hydrate a KuberlyPlatform from `.kuberly/graph.json` (MCP-startup path).

    The MCP server should NOT build the graph from the repo on every cold
    start — that's expensive and racy with concurrent edits. The pre-commit
    hook (post_apm_install.sh) regenerates `.kuberly/graph.json` on every
    commit, so the cached file is always current as of the latest commit.

    Raises SystemExit(1) with a clear message if the cache is missing — the
    consumer needs to either commit something (which fires the regen hook)
    or run `bash apm_modules/kuberly/kuberly-skills/scripts/post_apm_install.sh`
    once to bootstrap.
    """
    repo = Path(repo_path).resolve()
    cache = repo / ".kuberly" / "graph.json"
    if not cache.is_file():
        print(
            f"Error: {cache} not found. The MCP server reads this cached graph; "
            "regenerate it once with:\n"
            f"  python3 {Path(__file__).resolve()} generate {repo} -o {repo}/.kuberly\n"
            "After that, the pre-commit hook keeps it fresh on every commit.",
            file=sys.stderr,
        )
        sys.exit(1)
    g = KuberlyPlatform(str(repo))
    g.load_from_cache(cache)
    return g


def main():
    parser = argparse.ArgumentParser(
        description="kuberly-platform: knowledge graph for kuberly-stack")
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # --- generate ---
    gen = sub.add_parser("generate", help="Generate all graph outputs (default)")
    gen.add_argument("repo", nargs="?", default=".",
                     help="Path to kuberly-stack repo root")
    gen.add_argument("-o", "--output", default=".kuberly",
                     help="Output directory (default: .kuberly)")

    # --- blast ---
    bl = sub.add_parser("blast", help="Compute blast radius for a node")
    bl.add_argument("node", help="Node id or name (e.g. 'eks', 'module:aws/vpc', 'shared-infra')")
    bl.add_argument("--repo", default=".", help="Path to kuberly-stack repo root")
    bl.add_argument("--direction", choices=["upstream", "downstream", "both"],
                    default="both", help="Direction to traverse")
    bl.add_argument("--depth", type=int, default=20, help="Max traversal depth")
    bl.add_argument("--json", action="store_true", help="Output as JSON")

    # --- path ---
    pa = sub.add_parser("path", help="Find shortest path between two nodes")
    pa.add_argument("source", help="Source node id or name")
    pa.add_argument("target", help="Target node id or name")
    pa.add_argument("--repo", default=".", help="Path to kuberly-stack repo root")

    # --- query ---
    qu = sub.add_parser("query", help="Query/filter nodes")
    qu.add_argument("--repo", default=".", help="Path to kuberly-stack repo root")
    qu.add_argument("--type", dest="node_type", help="Filter by node type")
    qu.add_argument("--env", help="Filter by environment")
    qu.add_argument("--name", help="Filter by name substring")

    # --- mcp ---
    mc = sub.add_parser("mcp", help="Run as MCP server (stdio)")
    mc.add_argument("--repo", default=".", help="Path to kuberly-stack repo root")

    # --- script (v0.14.2: Code Mode for kuberly-platform) ---
    # Hydrates the cached graph from .kuberly/graph.json into a `g` variable, then
    # exec()s a user-supplied Python snippet against it. Lets sub-agents chain
    # 5+ graph queries inside a SINGLE Bash tool call instead of N MCP round-trips.
    # Inspired by Anthropic's Programmatic Tool Calling and Cloudflare Code Mode —
    # the same pattern, scoped to our platform module since MCP-connector tools
    # are not yet eligible for API-level PTC (per Anthropic's PTC spec).
    sc = sub.add_parser("script", help="Run a Python snippet against the cached graph (Code Mode)")
    sc.add_argument("--repo", default=".", help="Path to kuberly-stack repo root")
    sc.add_argument("-c", "--code", help="Python snippet (overrides stdin)")
    sc.add_argument("--json", action="store_true",
                    help="Wrap the snippet's last expression in json.dumps; helpful for terse output")

    args = parser.parse_args()

    # Default to generate if no subcommand
    if args.command is None or args.command == "generate":
        repo_path = getattr(args, "repo", ".") or "."
        out = Path(getattr(args, "output", ".kuberly") or ".kuberly").resolve()
        g = load_graph(repo_path)
        out.mkdir(parents=True, exist_ok=True)

        # Generate outputs (silent — banner emits the summary)
        write_graph_json(g, out)
        write_graph_html(g, out)
        write_graph_report(g, out)
        write_mermaid_dag(g, out)

        # SessionStart banner — terse, no decoration. Single line per fact.
        stats   = g.compute_stats()
        drift   = g.cross_env_drift()
        n_envs  = stats["type_counts"].get("environment", 0)
        n_mods  = stats["type_counts"].get("module", 0)
        n_apps  = stats["type_counts"].get("application", 0)
        n_crit  = sum(1 for _, ind, _ in stats["critical_nodes"] if ind >= 3)
        n_drift = len(drift["components"]) + len(drift["applications"])

        # Edge counts: total emitted vs serialized. Orphan edges (targets
        # not materialized as nodes — e.g. component_type:*, tool:*,
        # redacted resources) are kept in-memory for query semantics but
        # filtered from graph.json/graph.html so cytoscape can render.
        n_edges_total = len(g.edges)
        n_edges_out = len(g._serializable_edges())
        edges_str = (
            f"edges={n_edges_out}"
            if n_edges_out == n_edges_total
            else f"edges={n_edges_out} (+{n_edges_total - n_edges_out} orphan)"
        )
        print(
            f"kuberly-platform: nodes={len(g.nodes)} {edges_str} "
            f"envs={n_envs} modules={n_mods} apps={n_apps} "
            f"critical={n_crit} drift={n_drift}"
        )

    elif args.command == "blast":
        g = load_graph(args.repo)
        result = g.blast_radius(args.node, direction=args.direction, max_depth=args.depth)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(format_blast_radius(result))

    elif args.command == "path":
        g = load_graph(args.repo)
        result = g.shortest_path(args.source, args.target)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Path (length {result['length']}):")
            for i, nid in enumerate(result["path"]):
                prefix = "  " + ("-> " if i > 0 else "   ")
                node = g.nodes.get(nid, {})
                print(f"{prefix}{nid} ({node.get('type', '?')})")

    elif args.command == "query":
        g = load_graph(args.repo)
        results = g.query_nodes(node_type=args.node_type, environment=args.env,
                                name_contains=args.name)
        for n in results:
            print(f"{n['id']:50s}  type={n.get('type', '?'):15s}  label={n.get('label', '')}")
        print(f"\n{len(results)} nodes matched.")

    elif args.command == "mcp":
        # MCP server reads the cached `.kuberly/graph.json` rather than
        # rebuilding from the repo on every cold start. The pre-commit hook
        # (post_apm_install.sh in kuberly-skills) regenerates the cache on
        # every commit. Bootstrap path: run `generate` once after `apm install`.
        run_mcp_server(load_graph_cached(args.repo))

    elif args.command == "script":
        # Code Mode: hydrate the cached graph and exec a Python snippet against it.
        # Replaces N MCP round-trips with one Bash call. The snippet has these
        # names in scope:
        #   g    — KuberlyPlatform instance, loaded from .kuberly/graph.json
        #   json — module
        # Anything you `print()` is the result. Snippet may use any of g's
        # methods: query_nodes, get_neighbors, blast_radius, shortest_path,
        # cross_env_drift, compute_stats, _resolve_modules, scope_for_change.
        import json as _json_mod
        g = load_graph_cached(args.repo)
        code = args.code if args.code else sys.stdin.read()
        if not code or not code.strip():
            print("script: no code (pass via -c or stdin)", file=sys.stderr)
            sys.exit(2)
        # Sandbox is intentionally minimal — exec runs in this process. Sub-
        # agents calling this from Bash are already trusted by the harness.
        env = {"g": g, "json": _json_mod, "__name__": "__kuberly_script__"}
        try:
            exec(compile(code, "<kuberly-script>", "exec"), env)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"script-error: {type(exc).__name__}: {exc}", file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# Renderer layer: turn raw graph results into Markdown cards for MCP output.
#
# Every renderer is `_card_<tool>(result, args, graph) -> str`. The output is
# GitHub-Flavored Markdown that Claude Code renders inline — tables, headers,
# and emoji status badges. Keep these pure (no I/O) so the cards are
# reproducible and easy to test.
# ---------------------------------------------------------------------------

def _node_emoji(ntype: str | None) -> str:
    return EMOJI.get(ntype or "", "-")


def _truncate(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _sparkline(values: list[float], width: int = 16) -> str:
    """Unicode block-char sparkline. Returns '·…' for empty / all-equal."""
    if not values:
        return "·" * width
    blocks = "▁▂▃▄▅▆▇█"
    # Downsample to `width` buckets when needed so long series fit
    if len(values) > width:
        n = len(values)
        b = n / width
        bucketed = []
        for i in range(width):
            start = int(i * b)
            end = max(int((i + 1) * b), start + 1)
            chunk = values[start:end]
            bucketed.append(sum(chunk) / len(chunk))
        values = bucketed
    lo, hi = min(values), max(values)
    if hi == lo:
        return blocks[len(blocks) // 2] * len(values)
    span = hi - lo
    return "".join(blocks[min(len(blocks) - 1,
                              int((v - lo) / span * (len(blocks) - 1)))]
                   for v in values)


def _confidence_badge(c: str | None) -> str:
    return {"high": "ok", "medium": "warn", "low": "meh"}.get(c or "", "-") + f" {c or '—'}"


def _status_badge(s: str | None) -> str:
    return f"{EMOJI.get(s or '', '-')} {s or '—'}"


def _card_query_nodes(result: list[dict], args: dict, graph: KuberlyPlatform) -> str:
    if not result:
        return f"## query_nodes — 0 matches\n\n_No nodes match the filter._"

    by_type: dict[str, list[dict]] = defaultdict(list)
    for n in result:
        by_type[n.get("type", "unknown")].append(n)

    fparts = []
    if args.get("node_type"): fparts.append(f"`type={args['node_type']}`")
    if args.get("environment"): fparts.append(f"`env={args['environment']}`")
    if args.get("name_contains"): fparts.append(f"`name~{args['name_contains']}`")
    fstr = " · ".join(fparts) or "_(no filter)_"

    lines = [
        f"## query_nodes — **{len(result)} matches**",
        f"_filter: {fstr}_",
        "",
    ]
    for t in sorted(by_type):
        nodes = by_type[t]
        lines.append(f"### {t} · {len(nodes)}")
        lines.append("")
        lines.append("| ID | Label | Env |")
        lines.append("|---|---|---|")
        for n in sorted(nodes, key=lambda x: x.get("id", "")):
            lines.append(
                f"| `{_truncate(n.get('id', ''))}` "
                f"| {n.get('label', '—')} "
                f"| {n.get('environment', '—')} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


def _card_get_node(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err get_node — error\n\n`{result['error']}`"
    info = result.get("node_info", {})
    nid = result.get("node", "?")
    ntype = info.get("type", "?")
    inc = result.get("incoming", [])
    out = result.get("outgoing", [])

    lines = [
        f"## `{nid}`",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| type | `{ntype}` |",
        f"| label | {info.get('label', '—')} |",
    ]
    for k, v in info.items():
        if k in {"id", "type", "label"} or v in (None, "", [], {}):
            continue
        if isinstance(v, (list, dict)):
            v = json.dumps(v, default=str)
        lines.append(f"| {k} | `{_truncate(str(v), 80)}` |")

    lines.extend([
        "",
        f"### Edges",
        f"- **incoming:** {len(inc)}",
        f"- **outgoing:** {len(out)}",
    ])
    if inc:
        lines.append("")
        lines.append("**Incoming:**")
        for e in inc[:20]:
            lines.append(f"- `{_truncate(e['source'])}` —[{e.get('relation','')}]→")
        if len(inc) > 20:
            lines.append(f"- _… {len(inc) - 20} more_")
    if out:
        lines.append("")
        lines.append("**Outgoing:**")
        for e in out[:20]:
            lines.append(f"- →[{e.get('relation','')}]→ `{_truncate(e['target'])}`")
        if len(out) > 20:
            lines.append(f"- _… {len(out) - 20} more_")
    return "\n".join(lines)


def _card_get_neighbors(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    # Same shape as get_node — reuse the renderer
    return _card_get_node(result, args, graph).replace("get_node", "get_neighbors")


def _card_blast_radius(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err blast_radius — error\n\n`{result['error']}`"
    info = result.get("node_info", {})
    nid = result.get("node", "?")
    ds_count = result.get("downstream_count", 0)
    us_count = result.get("upstream_count", 0)

    severity = (
        "bad high"  if ds_count > 20
        else "meh medium" if ds_count > 5
        else "ok low"
    )

    lines = [
        f"## blast_radius — `{nid}`",
        f"_{_node_emoji(info.get('type'))} {info.get('type','?')} · "
        f"label: {info.get('label','—')}_",
        "",
        "| Direction | Count |",
        "|---|---:|",
        f"| down downstream (affected if changed) | **{ds_count}** |",
        f"| up upstream (affects this) | **{us_count}** |",
        f"| severity | {severity} |",
        "",
    ]

    def _by_depth(group: dict) -> dict[int, list[tuple[str, dict]]]:
        d: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        for k, v in group.items():
            d[v.get("depth", 0)].append((k, v))
        return dict(sorted(d.items()))

    if ds_count:
        lines.append("### down Downstream by depth")
        for depth, items in _by_depth(result.get("downstream", {})).items():
            lines.append(f"- **depth {depth}** · {len(items)} node(s)")
            for nid_, info_ in items[:8]:
                lines.append(f"- {_node_emoji(info_.get('type'))} `{_truncate(nid_)}`")
            if len(items) > 8:
                lines.append(f"- _… {len(items) - 8} more_")
        lines.append("")

    if us_count:
        lines.append("### up Upstream by depth")
        for depth, items in _by_depth(result.get("upstream", {})).items():
            lines.append(f"- **depth {depth}** · {len(items)} node(s)")
            for nid_, info_ in items[:8]:
                lines.append(f"- {_node_emoji(info_.get('type'))} `{_truncate(nid_)}`")
            if len(items) > 8:
                lines.append(f"- _… {len(items) - 8} more_")
    return "\n".join(lines).rstrip()


def _card_shortest_path(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err shortest_path — error\n\n`{result['error']}`"
    path = result.get("path", [])
    if not path:
        return f"## shortest_path — _empty path_"
    lines = [
        f"## shortest_path — length **{result['length']}**",
        "",
    ]
    for i, nid in enumerate(path):
        info = graph.nodes.get(nid, {})
        e = _node_emoji(info.get("type"))
        prefix = "  " if i == 0 else "↓ "
        lines.append(f"{prefix}{e} `{nid}` _({info.get('type','?')})_")
    return "\n".join(lines)


def _card_drift(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    comps = result.get("components", {})
    apps = result.get("applications", {})
    if not comps and not apps:
        return (f"## drift — **no drift**\n\n"
                "_All environments have the same components and applications._")

    # Build heatmap: env × component (1 if missing)
    all_envs = sorted(set(comps.keys()) | set(apps.keys()) |
                      {n["label"] for n in graph.nodes.values()
                       if n.get("type") == "environment"})
    all_comp_names = sorted({c for ms in comps.values() for c in ms})

    lines = [
        f"## drift — **{len(comps)} env(s) with component drift, "
        f"{len(apps)} env(s) with app drift**",
        "",
    ]

    if comps:
        lines.append("### Component drift heatmap")
        lines.append("")
        # Header
        header = ["env"] + [f"`{c[:10]}`" for c in all_comp_names]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for env in sorted(comps.keys()):
            row = [f"**{env}**"]
            missing = set(comps.get(env, []))
            for c in all_comp_names:
                row.append("bad" if c in missing else "ok")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    if apps:
        lines.append("### Application drift")
        for env in sorted(apps.keys()):
            lines.append(f"- **{env}**: missing `{', '.join(apps[env])}`")
        lines.append("")
    return "\n".join(lines).rstrip()


def _card_stats(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    types = result.get("type_counts", {})
    crit = result.get("critical_nodes", [])
    chains = result.get("longest_chains", [])

    # Per-type counts as a sorted table with bar visualization
    max_count = max(types.values()) if types else 1
    type_rows = []
    for t in sorted(types, key=lambda x: -types[x]):
        bar_width = int(round((types[t] / max_count) * 12))
        bar = "█" * bar_width + "░" * (12 - bar_width)
        type_rows.append(f"| `{t}` | {types[t]} | `{bar}` |")

    # Depth distribution from longest chains (for sparkline)
    chain_lengths = [len(c) for c in chains]
    spark = _sparkline(chain_lengths) if chain_lengths else ""

    lines = [
        f"## graph stats",
        "",
        f"**{result['node_count']}** nodes · **{result['edge_count']}** edges",
        "",
        "### Node mix",
        "",
        "| Type | Count | Distribution |",
        "|---|---:|---|",
        *type_rows,
        "",
        f"### Critical nodes (most depended upon)",
        "",
        "| Node | in° | out° |",
        "|---|---:|---:|",
    ]
    for nid, ind, outd in crit:
        if ind == 0:
            continue
        info = graph.nodes.get(nid, {})
        lines.append(f"| {_node_emoji(info.get('type'))} `{_truncate(nid)}` | "
                     f"**{ind}** | {outd} |")

    lines.extend([
        "",
        f"### Dependency chains (top {len(chains)})",
        "",
    ])
    if spark:
        lines.append(f"_chain-length distribution:_ `{spark}` "
                     f"(min {min(chain_lengths)} · max {max(chain_lengths)})")
        lines.append("")
    for i, chain in enumerate(chains, 1):
        readable = " → ".join(c.replace("module:", "") for c in chain)
        lines.append(f"{i}. `{readable}`")
    return "\n".join(lines).rstrip()


def _card_plan_persona_fanout(plan: dict, args: dict, graph: KuberlyPlatform) -> str:
    """Fanout briefing card — terse `key: value` lines, table for the DAG.

    Token-minimal as of v0.10.6: no emoji, no decorative headers, no
    multi-paragraph explanatory prose. Orchestrator reads this directly.
    """
    scope  = plan.get("scope", {})
    br     = scope.get("blast_radius", {})
    gates  = plan.get("gates", {})
    phases = plan.get("phases", [])
    branch = gates.get("branch", {})
    op     = gates.get("openspec", {})
    ps     = gates.get("personas_synced", {})

    if op.get("required") and not op.get("existing_change_folder"):
        op_label = "required"
    elif op.get("required"):
        op_label = f"reuse {op.get('existing_change_folder')}"
    else:
        op_label = "n/a"

    unresolved = plan.get("unresolved_modules") or []
    is_halt = plan.get("task_kind") == "stop-target-absent"

    lines = [f"fanout: {plan.get('session_slug', '?')}"]

    if is_halt:
        names = ", ".join(unresolved) or "?"
        lines += [
            f"STOP target-absent: {names} not in graph. DAG empty.",
            "Action: confirm name, retry with corrected named_modules, or surface absence to user.",
        ]
    elif unresolved:
        lines.append(f"partial: unresolved={','.join(unresolved)}")

    lines += [
        f"task_kind: {plan.get('task_kind', '?')} ({plan.get('confidence', '?')})",
    ]

    mods = scope.get("modules", [])
    if mods:
        lines.append(f"modules: {','.join(mods)}")
    if br.get("summary"):
        lines.append(f"blast: {br['summary']}")
    if br.get("upstream"):
        lines.append(f"upstream: {','.join(br['upstream'])}")
    files = scope.get("files_likely_changed", [])
    if files:
        head = files[:10]
        suffix = f" (+{len(files) - 10})" if len(files) > 10 else ""
        lines.append("files: " + ",".join(head) + suffix)
    if scope.get("drift_slice"):
        lines.append(f"drift: {json.dumps(scope['drift_slice'], default=str, separators=(',', ':'))}")

    lines += [
        f"gates: branch={branch.get('verdict', '?')}({branch.get('current') or '?'}) "
        f"openspec={op_label} personas={ps.get('found', 0)}/{ps.get('expected', 0)}",
    ]
    if ps.get("missing"):
        lines.append(f"missing-personas: {','.join(ps['missing'])}")
    if branch.get("verdict") == "block":
        lines.append(f"branch-blocked: {branch.get('reason', '')}")

    if phases:
        lines += ["", "| # | phase | personas | mode | approval |",
                  "|---|---|---|---|---|"]
        for i, ph in enumerate(phases, 1):
            mode = "par" if ph.get("parallel") else "seq"
            approval = "yes" if ph.get("needs_approval") else "no"
            personas = ",".join(ph.get("personas", []))
            lines.append(f"| {i} | {ph['id']} | {personas or '-'} | {mode} | {approval} |")

    branch_arg = branch.get("current") or "<feature-branch>"
    lines += [
        "",
        f"next: session_init(name=\"{plan.get('session_slug', '?')}\", "
        f"modules={json.dumps(mods)}, branch=\"{branch_arg}\")",
    ]
    if branch.get("verdict") == "block":
        lines.append(f"BLOCK: {branch.get('reason', '')}")
    return "\n".join(lines)


def _card_session_init(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err session_init — error\n\n`{result['error']}`"
    phases = result.get("phases", [])
    lines = [
        f"## session created",
        "",
        f"- **slug:** `{result.get('session_slug', '?')}`",
        f"- **dir:** `{result.get('session_dir', '?')}`",
        f"- **task_kind:** `{result.get('task_kind', '?')}` "
        f"({_confidence_badge(result.get('confidence'))})",
        f"- **files seeded:** {', '.join(f'`{f}`' for f in result.get('files', []))}",
    ]
    if phases:
        lines.extend([
            "",
            f"### Fanout queued",
            "",
            "| # | Phase | Personas | Status |",
            "|---|---|---|---|",
        ])
        for i, ph in enumerate(phases, 1):
            personas = ", ".join(f"`{p}`" for p in ph["personas"])
            lines.append(f"| {i} | **{ph['id']}** | {personas} | "
                         f"{EMOJI['queued']} queued |")
    return "\n".join(lines)


def _card_session_status(status: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in status:
        return f"## err session_status — error\n\n`{status['error']}`"

    name = status.get("session", "?")
    if status.get("_no_status_yet"):
        files = status.get("files", [])
        return (
            f"## session `{name}` — _no fanout started_\n\n"
            f"`status.json` not present yet.\n\n"
            f"Files in dir: {len(files)}"
        )

    phases = status.get("phases", [])
    personas = status.get("personas", {})
    files = status.get("files", [])

    # Roll-up phase status counts
    phase_counts = defaultdict(int)
    for ph in phases:
        phase_counts[ph.get("status", "queued")] += 1

    summary = " · ".join(f"{EMOJI.get(k, '-')} {v} {k}"
                         for k, v in phase_counts.items())

    lines = [
        f"# {EMOJI['session']} session status — `{name}`",
        f"_task_kind: `{status.get('task_kind', '?')}` · "
        f"updated: {status.get('updated_at', '?')}_",
        "",
        f"**Phases:** {summary}",
        "",
        f"## Phase progression",
        "",
        "| # | Phase | Personas | Mode | Status |",
        "|---|---|---|---|---|",
    ]
    for i, ph in enumerate(phases, 1):
        mode_parts = []
        if ph.get("parallel"):
            mode_parts.append(f"{EMOJI['parallel']} parallel")
        else:
            mode_parts.append(f"{EMOJI['sequential']} seq")
        if ph.get("needs_approval"):
            mode_parts.append(f"{EMOJI['approval']} approval")
        personas_str = ", ".join(
            f"{EMOJI.get(personas.get(p, {}).get('status', 'queued'), '-')} `{p}`"
            for p in ph["personas"]
        )
        lines.append(
            f"| {i} | **{ph['id']}** | {personas_str} | "
            f"{' · '.join(mode_parts)} | {_status_badge(ph.get('status'))} |"
        )

    # Per-persona timing detail (only those that ran)
    timed = [(p, info) for p, info in personas.items()
             if info.get("started_at") or info.get("ended_at")]
    if timed:
        lines.extend([
            "",
            "### Persona timing",
            "",
            "| Persona | Status | Started | Ended |",
            "|---|---|---|---|",
        ])
        for p, info in timed:
            lines.append(
                f"| `{p}` | {_status_badge(info.get('status'))} "
                f"| {info.get('started_at', '—')} "
                f"| {info.get('ended_at', '—')} |"
            )

    # File listing — annotate expected ones with status
    lines.extend([
        "",
        f"### Session files",
        "",
    ])
    if not files:
        lines.append("_(empty)_")
    else:
        for f in files:
            size_kb = f["bytes"] / 1024
            size = f"{size_kb:.1f} KB" if size_kb >= 1 else f"{f['bytes']} B"
            lines.append(f"- `{f['file']}` · {size} · {f['mtime']}")
    return "\n".join(lines)


def _card_session_set_status(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err session_set_status — error\n\n`{result['error']}`"
    return (
        f"## ok status updated\n\n"
        f"- **{result['kind']}:** `{result['target']}`\n"
        f"- **status:** {_status_badge(result['status'])}\n"
        f"- **at:** {result['updated_at']}"
    )


def _card_session_read(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err session_read — error\n\n`{result['error']}`"
    fname = result.get("file", "?")
    content = result.get("content", "")
    return (
        f"## `{fname}` · {result.get('bytes', 0)} bytes\n\n"
        f"```markdown\n{content}\n```"
    )


def _card_session_write(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err session_write — error\n\n`{result['error']}`"
    return (
        f"## ok wrote `{result.get('file', '?')}`\n\n"
        f"_{result.get('bytes', 0)} bytes_"
    )


def _card_session_list(result: dict, args: dict, graph: KuberlyPlatform) -> str:
    if "error" in result:
        return f"## err session_list — error\n\n`{result['error']}`"
    files = result.get("files", [])
    if not files:
        return f"## session `{result.get('session', '?')}` — _empty_"
    lines = [
        f"## session `{result.get('session', '?')}` "
        f"— **{len(files)} files**",
        "",
        "| File | Size | Modified |",
        "|---|---:|---|",
    ]
    for f in files:
        lines.append(f"| `{f['file']}` | {f['bytes']} B | {f['mtime']} |")
    return "\n".join(lines)


# Compact one-line summaries for chained calls (`format=compact`).
def _compact_summary(name: str, result, args: dict) -> str:
    """Compact: structured-but-decoration-free output for sub-agents.

    Trades the rich Markdown card for the actual data the caller will use
    next: node IDs, neighbor lists, drift envelopes — at roughly 1/10 the
    tokens. Default for MCP `tools/call` since v0.13.4. Pass `format: card`
    explicitly when the orchestrator wants human-readable Markdown.
    """
    if isinstance(result, dict) and "error" in result:
        return f"err {name}: {result['error']}"

    if name == "query_nodes":
        if not result:
            return "query_nodes: 0"
        # One id per line; preserve order. ~10 tok per id beats ~50 tok per row.
        ids = [n.get("id") or n.get("name") or "?" for n in result]
        return f"query_nodes: {len(result)}\n" + "\n".join(ids)

    if name == "query_resources":
        matches = result.get("matches", [])
        n = result.get("count", len(matches))
        head = f"query_resources: {n}"
        if result.get("truncated"):
            head += " (truncated to first 200; add filters)"
        if not matches:
            return head
        # type \t address [redacted] — compact and grep-friendly
        rows = []
        for m in matches:
            tag = " [redacted]" if m.get("redacted") else ""
            rt = m.get("resource_type") or "?"
            lbl = m.get("label") or m.get("id") or "?"
            mod = m.get("module") or "?"
            env = m.get("environment") or "?"
            rows.append(f"{rt}\t{env}/{mod}/{lbl}{tag}")
        return head + "\n" + "\n".join(rows)

    if name == "find_docs":
        matches = result.get("matches", [])
        head = f"find_docs: {result.get('count', len(matches))}"
        if result.get("semantic_used"):
            head += " (semantic)"
        if not matches:
            return head
        rows = []
        for m in matches:
            rows.append(f"{m.get('doc_kind','?')}\t{m.get('id','?')}\t{m.get('label','')[:80]}")
        return head + "\n" + "\n".join(rows)

    if name == "graph_index":
        nc = result.get("node_counts_by_type") or {}
        ec = result.get("edge_counts_by_relation") or {}
        bridges = result.get("cross_layer_bridges") or {}
        overlays = result.get("overlays_found") or []
        out = ["graph_index:"]
        out.append("  nodes: " + ", ".join(f"{k}={v}" for k, v in sorted(nc.items())))
        out.append("  edges: " + ", ".join(f"{k}={v}" for k, v in sorted(ec.items())))
        out.append("  bridges: " + ", ".join(f"{k}={v}" for k, v in bridges.items()))
        out.append("  overlays:")
        for o in overlays:
            out.append(f"    {o.get('file','?')} schema={o.get('schema_version','?')} at={o.get('generated_at','?')}")
        meta = result.get("docs_overlay_meta") or {}
        if meta:
            out.append(f"  docs: {meta.get('doc_count', 0)} indexed, embed_provider={meta.get('embed_provider', '') or '<none>'}")
        return "\n".join(out)

    if name == "query_k8s":
        matches = result.get("matches", [])
        n = result.get("count", len(matches))
        head = f"query_k8s: {n}"
        if result.get("truncated"):
            head += " (truncated to first 200; add filters)"
        if not matches:
            return head
        rows = []
        for m in matches:
            tag = " [redacted]" if m.get("redacted") else ""
            kind = m.get("k8s_kind") or "?"
            ns = m.get("k8s_namespace") or "-"
            nm = m.get("k8s_name") or "?"
            extra = ""
            if kind in ("Deployment", "StatefulSet"):
                rep = m.get("replicas")
                if rep is not None:
                    extra = f"  replicas={rep}"
            elif kind == "Service":
                ports = m.get("ports") or []
                extra = "  ports=" + ",".join(f"{p['port']}/{p['protocol']}" for p in ports[:4])
            elif kind == "ServiceAccount" and m.get("irsa_role_arn"):
                extra = "  irsa=" + m["irsa_role_arn"].rsplit("/", 1)[-1]
            elif kind in ("Secret", "ConfigMap"):
                keys = m.get("data_keys") or []
                extra = f"  keys={len(keys)}"
            rows.append(f"{kind}\t{ns}/{nm}{extra}{tag}")
        return head + "\n" + "\n".join(rows)

    if name in ("get_node", "get_neighbors"):
        info = result.get("node_info") or {}
        node = result.get("node") or "?"
        ntype = info.get("type") or "?"
        nlabel = info.get("label") or ""
        head = f"node: {node} ({ntype}{', '+nlabel if nlabel and nlabel!=node else ''})"
        # Edge entries: incoming uses 'source', outgoing uses 'target'.
        def _fmt(edges, peer_key, label):
            if not edges:
                return f"{label}: -"
            parts = []
            for e in edges[:30]:
                rel = e.get("relation") or ""
                peer = e.get(peer_key) or "?"
                parts.append(f"{peer}{f'({rel})' if rel else ''}")
            extra = "" if len(edges) <= 30 else f" +{len(edges)-30}"
            return f"{label}: " + ", ".join(parts) + extra
        return "\n".join([
            head,
            _fmt(result.get("incoming") or [], "source", "incoming"),
            _fmt(result.get("outgoing") or [], "target", "outgoing"),
        ])

    if name == "blast_radius":
        node = result.get("node", "?")
        head = (f"blast_radius: {node} "
                f"down={result.get('downstream_count',0)} "
                f"up={result.get('upstream_count',0)}")
        # downstream/upstream are usually dicts {id: {...}}; flatten to ids.
        def _ids(block):
            if not block: return ""
            if isinstance(block, dict):
                return ", ".join(list(block.keys())[:30])
            return ", ".join(str(x) for x in block[:30])
        ds = _ids(result.get("downstream"))
        us = _ids(result.get("upstream"))
        return head + (f"\ndownstream: {ds}" if ds else "") + (f"\nupstream: {us}" if us else "")

    if name == "shortest_path":
        path = result.get("path") or []
        return f"shortest_path: length={result.get('length','?')} via " + " -> ".join(path[:20])

    if name == "drift":
        comps = result.get("components", {}) or {}
        apps = result.get("applications", {}) or {}
        out = []
        for env, missing in comps.items():
            if missing:
                out.append(f"comp/{env}: missing {', '.join(missing[:20])}")
        for env, missing in apps.items():
            if missing:
                out.append(f"app/{env}: missing {', '.join(missing[:20])}")
        return "drift: " + ("none" if not out else "\n" + "\n".join(out))

    if name == "stats":
        crit = result.get("critical_nodes", []) or []
        crit_ids = ", ".join(c[0] for c in crit[:5] if isinstance(c, (list, tuple))) or ""
        return (f"stats: nodes={result.get('node_count',0)} edges={result.get('edge_count',0)}"
                + (f"\ncritical: {crit_ids}" if crit_ids else ""))

    if name == "quick_scope":
        # Return the scope.md body directly — that's the value the caller wants.
        # Recommendation header tells the orchestrator what to do next.
        rec = result.get("recommendation", "?")
        body = result.get("scope_md", "")
        return f"recommendation: {rec}\n\n{body}"

    if name == "plan_persona_fanout":
        phases = result.get("phases", []) or []
        line = (f"plan: kind={result.get('task_kind','?')} "
                f"({result.get('confidence','?')}) "
                f"slug={result.get('session_slug','?')}")
        ph_lines = []
        for i, ph in enumerate(phases, 1):
            mode = "par" if ph.get("parallel") else "seq"
            approval = "yes" if ph.get("needs_approval") else "no"
            personas = ",".join(ph.get("personas") or []) or "-"
            ph_lines.append(f"  {i}. {ph.get('id','?')}: [{personas}] mode={mode} approval={approval}")
        return line + (("\n" + "\n".join(ph_lines)) if ph_lines else "")

    if name == "session_init":
        return (f"session_init: slug={result.get('session_slug','?')} "
                f"kind={result.get('task_kind','?')} "
                f"dir={result.get('session_dir','?')}")

    if name == "session_status":
        if result.get("_no_status_yet"):
            return f"session_status: {result.get('session','?')} no fanout"
        ps = result.get("phases", []) or []
        rows = [f"  {p.get('id','?')}: {p.get('status','?')}" for p in ps]
        return f"session_status: {result.get('session','?')}\n" + "\n".join(rows)

    if name == "session_set_status":
        return f"ok set_status: {result.get('kind','?')} {result.get('target','?')}={result.get('status','?')}"

    if name == "session_read":
        # The body is the value the caller actually wants. Return it verbatim.
        return result.get("content") or f"read {result.get('file','?')}: {result.get('bytes',0)} B (no content)"

    if name == "session_write":
        return f"ok write: {result.get('file','?')} {result.get('bytes',0)} B"

    if name == "session_list":
        files = result.get("files", []) or []
        return f"session_list: {result.get('session','?')} {len(files)} files\n" + "\n".join(files)

    return f"{name}: ok"


_RENDERERS = {
    "query_nodes":          _card_query_nodes,
    "get_node":             _card_get_node,
    "get_neighbors":        _card_get_neighbors,
    "blast_radius":         _card_blast_radius,
    "shortest_path":        _card_shortest_path,
    "drift":                _card_drift,
    "stats":                _card_stats,
    "plan_persona_fanout":  _card_plan_persona_fanout,
    "session_init":         _card_session_init,
    "session_status":       _card_session_status,
    "session_set_status":   _card_session_set_status,
    "session_read":         _card_session_read,
    "session_write":        _card_session_write,
    "session_list":         _card_session_list,
}


def render_tool_result(name: str, result, args: dict, graph: KuberlyPlatform,
                       fmt: str = "card") -> str:
    """Format a raw tool result. fmt: card | json | compact."""
    if fmt == "json":
        return json.dumps(result, indent=2, default=str)
    if fmt == "compact":
        return _compact_summary(name, result, args)
    # card (default)
    fn = _RENDERERS.get(name)
    if fn is None:
        return json.dumps(result, indent=2, default=str)
    try:
        return fn(result, args, graph)
    except Exception as exc:  # never let a render bug break the MCP call
        return (f"## render error — falling back to JSON\n\n"
                f"`{type(exc).__name__}: {exc}`\n\n"
                f"```json\n{json.dumps(result, indent=2, default=str)}\n```")


# ---------------------------------------------------------------------------
# MCP Server (stdio JSON-RPC)
# ---------------------------------------------------------------------------

def _emit_telemetry(graph, tool_name, fmt, tool_args, output_text,
                    duration_ms, error):
    """Append one JSONL line per MCP tool call to .claude/mcp-telemetry.jsonl.

    v0.15.0: opt-in via KUBERLY_MCP_TELEMETRY=1. Off by default — never
    breaks the call path on disk-write errors. Used to identify which
    tools dominate token cost so the next optimization pass is data-driven.
    """
    if os.environ.get("KUBERLY_MCP_TELEMETRY") != "1":
        return
    try:
        path = graph.repo / ".claude" / "mcp-telemetry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool_name,
            "format": fmt,
            "input_size": len(json.dumps(tool_args, default=str)),
            "output_size": len(output_text or ""),
            "duration_ms": duration_ms,
        }
        if error:
            rec["error"] = error
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception:
        # Telemetry must NEVER break the MCP call path.
        pass


def run_mcp_server(graph: KuberlyPlatform):
    """Run an MCP server over stdio that exposes graph query tools."""
    import select as _select

    # All tools accept an optional `format` arg.
    # As of v0.13.4 the default is "compact" — structured-but-decoration-free
    # output (node ids, neighbor lists, drift envelopes) at ~10x lower token
    # cost than the rich Markdown card. Pass "card" explicitly when the
    # orchestrator wants human-readable rendering for the user-facing summary.
    _FORMAT_PROP = {
        "type": "string",
        "enum": ["compact", "json", "card"],
        "default": "compact",
        "description": "Output format. 'compact' (default, v0.13.4+) — structured plain text optimized for sub-agent token cost. 'json' — raw JSON dump. 'card' — rich Markdown for human display.",
    }

    TOOLS = [
        {
            "name": "query_nodes",
            "description": "Filter graph nodes by type (environment, component, shared-infra, application, module, cloud_provider, resource), environment name, and/or name substring.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "description": "Node type filter"},
                    "environment": {"type": "string", "description": "Environment name filter"},
                    "name_contains": {"type": "string", "description": "Substring to match in node name/id"},
                    "format": _FORMAT_PROP,
                },
            },
        },
        {
            "name": "query_resources",
            "defer_loading": True,
            "description": "Filter `resource:` nodes synthesized from the schema 2 state overlay (e.g. helm_release, aws_iam_role, kubernetes_namespace). Resource attribute VALUES are never in the graph — sensitive types (secrets, passwords, TLS keys) are tagged `redacted: true` so the existence is visible but the payload was suppressed at producer time.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "environment":   {"type": "string", "description": "env filter, e.g. 'prod'"},
                    "module":        {"type": "string", "description": "module filter, e.g. 'loki'"},
                    "resource_type": {"type": "string", "description": "Terraform resource type filter, e.g. 'helm_release', 'aws_iam_role'"},
                    "name_contains": {"type": "string", "description": "Substring match against resource address / id"},
                    "include_redacted": {"type": "boolean", "default": True, "description": "Include resources of sensitive types (existence only, never values)"},
                    "format": _FORMAT_PROP,
                },
            },
        },
        {
            "name": "find_docs",
            "defer_loading": True,
            "description": "Search the docs overlay (skills, agents, READMEs, OpenSpec changes, prompts). Always does keyword scoring against title/description/headings. If embeddings are present (KUBERLY_DOCS_EMBED was set when the overlay was generated), also computes semantic cosine similarity and combines the two scores 0.4 keyword + 0.6 semantic. Use to answer 'where is the skill that explains X' / 'what skill mentions module Y'.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query":    {"type": "string", "description": "Free-text query (will be tokenized for keyword + embedded for semantic)"},
                    "kind":     {"type": "string", "description": "Filter: skill / agent / doc / openspec / reference / prompt"},
                    "semantic": {"type": "boolean", "default": True, "description": "Use embedding similarity if available"},
                    "limit":    {"type": "integer", "default": 20, "description": "Max results"},
                    "format":   _FORMAT_PROP,
                },
            },
        },
        {
            "name": "graph_index",
            "defer_loading": True,
            "description": "Meta-tool. Returns a summary of every graph layer that's loaded (static, state, k8s, docs), node counts by type, edge counts by relation, cross-layer bridges that fired (IRSA, configures_module, depends_on, mentions), and overlay file freshness timestamps. Use at the start of a session to know what data you have.",
            "inputSchema": {"type": "object", "properties": {"format": _FORMAT_PROP}},
        },
        {
            "name": "query_k8s",
            "defer_loading": True,
            "description": "Filter `k8s_resource:` nodes synthesized from the live-cluster overlay (`.kuberly/k8s_overlay_*.json`, produced by `k8s_graph.py`). Knows Deployments, StatefulSets, Services, Ingresses, ConfigMaps, Secrets, ServiceAccounts, HPAs, NetworkPolicies. Secret/ConfigMap nodes carry `redacted: true` and `data_keys[]` only — values are NEVER in the graph. ServiceAccounts with IRSA annotations are bridged (edge `irsa_bound`) to the matching `resource:*/aws_iam_role.<n>` node from the state overlay.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "environment":     {"type": "string", "description": "env filter, e.g. 'prod'"},
                    "namespace":       {"type": "string", "description": "k8s namespace filter, e.g. 'monitoring'"},
                    "kind":            {"type": "string", "description": "k8s kind filter, e.g. 'Deployment', 'Service', 'Secret'"},
                    "name_contains":   {"type": "string", "description": "Substring match against resource name"},
                    "label_selector":  {"type": "object", "description": "{key: value} pairs that ALL must match the resource's labels"},
                    "include_redacted": {"type": "boolean", "default": True, "description": "Include Secret / ConfigMap nodes (existence only — `data_keys` shown, never values)"},
                    "format": _FORMAT_PROP,
                },
            },
        },
        {
            "name": "get_node",
            "description": "Get full details for a specific node by id or name.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node id or name"},
                    "format": _FORMAT_PROP,
                },
                "required": ["node"],
            },
        },
        {
            "name": "get_neighbors",
            "description": "Get immediate incoming and outgoing neighbors of a node.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node id or name"},
                    "format": _FORMAT_PROP,
                },
                "required": ["node"],
            },
        },
        {
            "name": "blast_radius",
            "description": "Compute the blast radius of a node: what it affects downstream (if changed) and what affects it upstream. Useful for impact analysis.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node id or name (e.g. 'eks', 'module:aws/vpc')"},
                    "direction": {"type": "string", "enum": ["upstream", "downstream", "both"], "default": "both"},
                    "max_depth": {"type": "integer", "default": 20},
                    "format": _FORMAT_PROP,
                },
                "required": ["node"],
            },
        },
        {
            "name": "shortest_path",
            "defer_loading": True,
            "description": "Find the shortest path between two nodes in the graph.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Source node id or name"},
                    "target": {"type": "string", "description": "Target node id or name"},
                    "format": _FORMAT_PROP,
                },
                "required": ["source", "target"],
            },
        },
        {
            "name": "drift",
            "defer_loading": True,
            "description": "Show cross-environment drift: components and applications that exist in some environments but not others.",
            "inputSchema": {
                "type": "object",
                "properties": {"format": _FORMAT_PROP},
            },
        },
        {
            "name": "stats",
            "defer_loading": True,
            "description": "Get graph statistics: node/edge counts, critical nodes (most depended upon), and longest dependency chains.",
            "inputSchema": {
                "type": "object",
                "properties": {"format": _FORMAT_PROP},
            },
        },
        {
            "name": "plan_persona_fanout",
            "description": "Orchestration plan for a kuberly-stack infra task. Classifies task_kind, computes blast-radius/drift scope, runs branch + OpenSpec + personas-synced gates, returns a persona DAG (with per-phase parallel/needs_approval flags) and a ready-to-paste context.md body. Call this first in agent-orchestrator mode; then use session_init to materialize a session dir.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task":           {"type": "string", "description": "Free-form task description from the user."},
                    "named_modules":  {"type": "array", "items": {"type": "string"}, "description": "Optional: module names hinted by the user (e.g. ['loki'])."},
                    "target_envs":    {"type": "array", "items": {"type": "string"}, "description": "Optional: target environments. Drift slice is computed only when set."},
                    "current_branch": {"type": "string", "description": "Result of `git rev-parse --abbrev-ref HEAD` — enables the branch gate."},
                    "session_name":   {"type": "string", "description": "Optional override for the session slug; defaults to slugified task."},
                    "task_kind":      {"type": "string", "enum": ["resource-bump", "incident", "new-application", "new-database", "new-module", "drift-fix", "cicd", "cleanup", "plan-review", "unknown", "stop-target-absent", "stop-no-instance"], "description": "Override task_kind inference. Note: `stop-target-absent` and `stop-no-instance` are normally set automatically — the orchestrator should not pass them, the planner emits them."},
                    "with_review":    {"type": "boolean", "default": False, "description": "Append a final `review` phase running the merged `pr-reviewer` (single agent, diff-only, ~5-8k tokens). v0.14.0+: review is OFF by default to save tokens — CI runs terraform_validate/tflint and the human PR review covers normal cases. Set true for high-risk changes (shared-infra blast, security/IAM). Auto-enabled when the task description literally contains the word 'review'."},
                    "format":         _FORMAT_PROP,
                },
                "required": ["task"],
            },
        },
        {
            "name": "quick_scope",
            "description": "Server-side scope.md generation. v0.15.0+: replaces the `agent-planner` agent for typical 'bump X', 'add Y', 'increase Z' tasks. The orchestrator calls this and writes the returned `scope_md` directly to `.agents/prompts/<session>/scope.md` — no agent dispatch, no 18k-token round-trip. Includes the v0.15.0 actionability check: returns `recommendation: 'stop-no-instance'` when a named module exists but has no component invoker. Fall back to dispatching `agent-planner` only when this returns `recommendation: 'fall-back-to-scope-planner'`.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task":          {"type": "string", "description": "Free-form task description from the user."},
                    "named_modules": {"type": "array", "items": {"type": "string"}, "description": "Module names hinted by the user (e.g. ['loki']). Without this, returns recommendation='fall-back-to-scope-planner'."},
                    "target_envs":   {"type": "array", "items": {"type": "string"}, "description": "Optional: target environments. Drift slice computed only when set."},
                    "format":        _FORMAT_PROP,
                },
                "required": ["task"],
            },
        },
        {
            "name": "session_init",
            "description": "Create .agents/prompts/<slug>/ with context.md (seeded from plan_persona_fanout), findings/, tasks/, and status.json (fanout dashboard). Mirrors apm_modules' init_agent_session.py layout so MCP and CLI produce identical session dirs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name":           {"type": "string", "description": "Session name; will be slugified."},
                    "task":           {"type": "string", "description": "One-line task description for context.md."},
                    "modules":        {"type": "array", "items": {"type": "string"}, "description": "Optional: module names to prefill into the graph snapshot."},
                    "current_branch": {"type": "string", "description": "Optional: current branch — recorded in context.md if it triggers the branch gate."},
                    "format":         _FORMAT_PROP,
                },
                "required": ["name"],
            },
        },
        {
            "name": "session_read",
            "description": "Read a file from a session dir under .agents/prompts/<slug>/. Path-validated — refuses reads outside the session dir.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Session name."},
                    "file": {"type": "string", "description": "Relative path within the session dir (e.g. 'scope.md', 'findings/cold.md')."},
                    "format": _FORMAT_PROP,
                },
                "required": ["name", "file"],
            },
        },
        {
            "name": "session_write",
            "description": "Write content to a file inside a session dir. Path-validated. Use this for context.md, decisions.md, tasks/<NN>-<slug>.md.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name":    {"type": "string", "description": "Session name."},
                    "file":    {"type": "string", "description": "Relative path within the session dir."},
                    "content": {"type": "string", "description": "Full file content."},
                    "format":  _FORMAT_PROP,
                },
                "required": ["name", "file", "content"],
            },
        },
        {
            "name": "session_list",
            "description": "List all files in a session dir with their sizes and mtimes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Session name."},
                    "format": _FORMAT_PROP,
                },
                "required": ["name"],
            },
        },
        {
            "name": "session_status",
            "description": "Live fanout dashboard for a session: phase progression with per-persona status badges (queued/running/done/blocked), persona timing, and file listing. Read this between Agent() calls in the orchestrator to render the current state of the fanout.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Session name."},
                    "format": _FORMAT_PROP,
                },
                "required": ["name"],
            },
        },
        {
            "name": "session_set_status",
            "defer_loading": True,
            "description": "Mutate status.json: mark a persona or phase as queued/running/done/blocked/skipped. Auto-detects whether `target` is a persona or phase id; phase status auto-rolls-up from its personas. Call this immediately before launching an Agent() (status='running') and immediately after it returns (status='done' or 'blocked').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string", "description": "Session name."},
                    "target": {"type": "string", "description": "Persona name (e.g. 'agent-infra-ops') or phase id (e.g. 'implement')."},
                    "status": {"type": "string", "enum": ["queued", "running", "done", "blocked", "skipped"]},
                    "kind":   {"type": "string", "enum": ["persona", "phase"], "description": "Optional override; auto-detected from `target`."},
                    "format": _FORMAT_PROP,
                },
                "required": ["name", "target", "status"],
            },
        },
    ]

    def handle_request(req: dict) -> dict:
        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "kuberly-platform",
                        "version": "1.3.0",
                    },
                },
            }

        if method == "notifications/initialized":
            return None  # No response for notifications

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            fmt = tool_args.get("format", "compact")
            t0 = time.monotonic()
            try:
                result = dispatch_tool(graph, tool_name, tool_args)
                text = render_tool_result(tool_name, result, tool_args, graph, fmt=fmt)
                _emit_telemetry(graph, tool_name, fmt, tool_args, text,
                                duration_ms=int((time.monotonic() - t0) * 1000),
                                error=None)
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                    },
                }
            except Exception as exc:
                _emit_telemetry(graph, tool_name, fmt, tool_args, "",
                                duration_ms=int((time.monotonic() - t0) * 1000),
                                error=f"{type(exc).__name__}: {exc}")
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }

        # Unknown method
        return {
            "jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }

    def dispatch_tool(g: KuberlyPlatform, name: str, args: dict):
        if name == "query_nodes":
            return g.query_nodes(
                node_type=args.get("node_type"),
                environment=args.get("environment"),
                name_contains=args.get("name_contains"),
            )
        elif name == "query_resources":
            return g.query_resources(
                environment=args.get("environment"),
                module=args.get("module"),
                resource_type=args.get("resource_type"),
                name_contains=args.get("name_contains"),
                include_redacted=args.get("include_redacted", True),
            )
        elif name == "find_docs":
            return g.find_docs(
                query=args.get("query", ""),
                kind=args.get("kind"),
                semantic=args.get("semantic", True),
                limit=args.get("limit", 20),
            )
        elif name == "graph_index":
            return g.graph_index()
        elif name == "query_k8s":
            return g.query_k8s(
                environment=args.get("environment"),
                namespace=args.get("namespace"),
                kind=args.get("kind"),
                name_contains=args.get("name_contains"),
                label_selector=args.get("label_selector"),
                include_redacted=args.get("include_redacted", True),
            )
        elif name == "get_node":
            return g.get_neighbors(args["node"])  # includes node_info
        elif name == "get_neighbors":
            return g.get_neighbors(args["node"])
        elif name == "blast_radius":
            return g.blast_radius(
                args["node"],
                direction=args.get("direction", "both"),
                max_depth=args.get("max_depth", 20),
            )
        elif name == "shortest_path":
            return g.shortest_path(args["source"], args["target"])
        elif name == "drift":
            return g.cross_env_drift()
        elif name == "stats":
            return g.compute_stats()
        elif name == "plan_persona_fanout":
            return g.plan_persona_fanout(
                task=args["task"],
                named_modules=args.get("named_modules"),
                target_envs=args.get("target_envs"),
                current_branch=args.get("current_branch"),
                session_name=args.get("session_name"),
                task_kind=args.get("task_kind"),
                with_review=bool(args.get("with_review", False)),
            )
        elif name == "quick_scope":
            return g.quick_scope(
                task=args["task"],
                named_modules=args.get("named_modules"),
                target_envs=args.get("target_envs"),
            )
        elif name == "session_init":
            return g.session_init(
                name=args["name"],
                task=args.get("task"),
                modules=args.get("modules"),
                current_branch=args.get("current_branch"),
            )
        elif name == "session_read":
            return g.session_read(args["name"], args["file"])
        elif name == "session_write":
            return g.session_write(args["name"], args["file"], args["content"])
        elif name == "session_list":
            return g.session_list(args["name"])
        elif name == "session_status":
            return g.session_status(args["name"])
        elif name == "session_set_status":
            return g.session_set_status(
                name=args["name"],
                target=args["target"],
                status=args["status"],
                kind=args.get("kind"),
            )
        else:
            raise ValueError(f"Unknown tool: {name}")

    # stdio JSON-RPC loop
    sys.stderr.write(f"kuberly-platform MCP server started ({len(graph.nodes)} nodes, {len(graph.edges)} edges)\n")
    sys.stderr.flush()

    buf = ""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            buf += line
            # Try to parse complete JSON messages
            # MCP uses Content-Length headers or newline-delimited JSON
            buf = buf.strip()
            if not buf:
                continue
            try:
                req = json.loads(buf)
                buf = ""
                resp = handle_request(req)
                if resp is not None:
                    out = json.dumps(resp)
                    sys.stdout.write(out + "\n")
                    sys.stdout.flush()
            except json.JSONDecodeError:
                continue  # Incomplete message, keep buffering
        except KeyboardInterrupt:
            break
        except Exception as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
