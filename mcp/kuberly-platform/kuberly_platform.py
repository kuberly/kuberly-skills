#!/usr/bin/env python3
"""
kuberly-platform: Knowledge graph generator for kuberly-stack Terragrunt/OpenTofu monorepos.

Parses components, applications, modules, and their dependencies to produce:
  - graph.json  — queryable graph structure
  - graph.html  — operator dashboard (default) + cytoscape graph tab
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

from graph_html_template import GRAPH_HTML_TEMPLATE_RAW

_GRAPH_HTML_TEMPLATE = string.Template(GRAPH_HTML_TEMPLATE_RAW)


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
            if not data or data.get("schema_version") not in (1, 2, 3):
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
                # Schema v3: forward whitelisted essentials onto the node so
                # the dashboard category aggregator can read them without
                # touching the raw overlay file.
                ess = r.get("essentials")
                if isinstance(ess, list) and ess:
                    attrs["essentials"] = ess
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
        # v0.36.0: surface CUE schemas + GitHub workflows as graph nodes
        # too — they used to live only on the dashboard payload, but the
        # 3D Graph view is now the single home for browsable nodes.
        self.scan_cue_schema_nodes()
        self.scan_workflow_nodes()

    def scan_cue_schema_nodes(self) -> None:
        """v0.36.0: emit `schema:cue/<file>` nodes for every cue/*.cue file.

        Each schema node carries its package + top-level field count as
        attributes. No edges to other nodes yet — the dashboard's node
        spotlight + the 3D Graph view both index by id, so the user can
        find any schema and click into it.
        """
        cue_dir = self.repo / "cue"
        if not cue_dir.is_dir():
            return
        out = _scan_cue_schemas(self.repo)
        for f in out.get("files", []):
            nid = f"schema:{f['file']}"
            if nid in self.nodes:
                continue
            self.add_node(
                nid, type="cue_schema",
                label=f["file"],
                package=f.get("package", ""),
                field_count=f.get("field_count", 0),
                source_layer="docs",   # treat schemas as "docs-like"
            )

    def scan_workflow_nodes(self) -> None:
        """v0.36.0: emit `workflow:<file>` nodes for every
        `.github/workflows/*.yml` plus `references` edges from each
        workflow to the modules / components it touches.

        This makes the 3D Graph view answer "which CI/CD job deploys
        this module" by clicking the module and following inbound
        `references` edges.
        """
        out = _scan_workflow_origins(self.repo)
        for w in out.get("workflows", []):
            wid = f"workflow:{w['file']}"
            if wid not in self.nodes:
                self.add_node(
                    wid, type="workflow",
                    label=w["file"],
                    triggers=list(w.get("triggers") or [])[:8],
                    source_layer="docs",
                )
            # Edges to module + component nodes that exist in the graph.
            for m in w.get("module_refs", []) or []:
                tgt = f"module:aws/{m}"  # match module node id shape
                if tgt in self.nodes:
                    self.add_edge(wid, tgt, relation="references")
            for c in w.get("component_refs", []) or []:
                env = c.get("env"); name = c.get("name")
                if env and name:
                    tgt = f"component:{env}/{name}"
                    if tgt in self.nodes:
                        self.add_edge(wid, tgt, relation="references")

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
    """Classify a node into one of {static, state, k8s, docs}.

    The graph builder doesn't always set a `source` attr explicitly, so
    we derive it from type / id prefix. Used by the dashboard for the
    layer-distribution chart and previously by the cytoscape viz for
    color encoding.
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


def _read_kuberly_skills_version(repo: Path) -> str:
    """Best-effort read of the kuberly-skills version for the dashboard
    eyebrow chip.

    Two cases: (a) generating against a consumer repo — version lives at
    `apm_modules/kuberly/kuberly-skills/apm.yml`; (b) generating against
    the kuberly-skills repo itself — version lives at the repo's own
    `apm.yml`. Returns "" if neither is found / parseable.
    """
    candidates = [
        repo / "apm_modules" / "kuberly" / "kuberly-skills" / "apm.yml",
        repo / "apm.yml",
    ]
    for p in candidates:
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("version:"):
                    v = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if v and v[0].isdigit():
                        return f"v{v}"
        except OSError:
            continue
    return ""


def _read_state_overlay_snapshot_times(repo: Path) -> dict[str, str]:
    """Map environment name -> `generated_at` from `.kuberly/state_overlay_*.json`."""
    out: dict[str, str] = {}
    kd = repo / ".kuberly"
    if not kd.is_dir():
        return out
    for p in sorted(kd.glob("state_overlay_*.json")):
        if not p.stem.startswith("state_overlay_"):
            continue
        env = p.stem[len("state_overlay_") :]
        data = load_json_safe(p)
        ga = (data or {}).get("generated_at")
        if isinstance(ga, str) and ga:
            out[env] = ga
    return out


def _collect_blast_mermaid_files(out_dir: Path) -> list[dict[str, str]]:
    """Load `blast_<env>.mmd` written by `write_mermaid_dag` (shared-infra blast)."""
    diagrams: list[dict[str, str]] = []
    for p in sorted(out_dir.glob("blast_*.mmd")):
        env = p.stem.replace("blast_", "", 1)
        try:
            src = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if src:
            diagrams.append({"env": env, "source": src})
    return diagrams


def _openspec_change_folder_count(repo: Path) -> int:
    changes = repo / "openspec" / "changes"
    if not changes.is_dir():
        return 0
    return sum(1 for e in changes.iterdir() if e.is_dir() and not e.name.startswith("."))


def _json_for_inline_script(obj: object) -> str:
    """Serialize JSON for embedding inside `<script type=\"application/json\">`."""
    return (
        json.dumps(obj, default=str, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


# ----- v0.34.0: per-category essentials aggregation ------------------
# Maps a Terraform resource_type to a (category, kind_label) tuple. Order
# of the categories below is the order they appear on the dashboard.
_CATEGORY_RULES: list[tuple[str, str, set[str]]] = [
    ("compute", "EKS Cluster",     {"aws_eks_cluster"}),
    ("compute", "EKS Node Group",  {"aws_eks_node_group"}),
    ("compute", "Fargate Profile", {"aws_eks_fargate_profile"}),
    ("compute", "EKS Addon",       {"aws_eks_addon"}),
    ("compute", "Lambda",          {"aws_lambda_function"}),
    ("data",    "RDS / Aurora",    {"aws_rds_cluster", "aws_rds_cluster_instance",
                                    "aws_db_instance", "aws_db_subnet_group"}),
    ("data",    "ElastiCache",     {"aws_elasticache_replication_group",
                                    "aws_elasticache_user", "aws_elasticache_user_group",
                                    "aws_elasticache_subnet_group"}),
    ("data",    "EBS Volume",      {"aws_ebs_volume"}),
    ("data",    "EFS",             {"aws_efs_file_system", "aws_efs_mount_target"}),
    ("data",    "S3 Bucket",       {"aws_s3_bucket"}),
    ("identity","IAM Role",        {"aws_iam_role"}),
    ("identity","IAM Policy",      {"aws_iam_policy"}),
    ("identity","Role Attachment", {"aws_iam_role_policy_attachment",
                                    "aws_iam_role_policy"}),
    ("identity","OIDC Provider",   {"aws_iam_openid_connect_provider"}),
    ("networking","VPC",           {"aws_vpc"}),
    ("networking","Subnet",        {"aws_subnet"}),
    ("networking","NAT Gateway",   {"aws_nat_gateway"}),
    ("networking","IGW",           {"aws_internet_gateway",
                                    "aws_egress_only_internet_gateway"}),
    ("networking","Security Group",{"aws_security_group"}),
    ("networking","SG Rule",       {"aws_security_group_rule"}),
    ("networking","VPC Endpoint",  {"aws_vpc_endpoint"}),
    ("networking","CloudFront",    {"aws_cloudfront_distribution"}),
    ("secrets", "Secrets Manager", {"aws_secretsmanager_secret",
                                    "aws_secretsmanager_secret_version"}),
    ("secrets", "KMS Key",         {"aws_kms_key", "aws_kms_alias"}),
    ("registries","ECR Repository",{"aws_ecr_repository", "aws_ecr_repository_policy",
                                    "aws_ecr_lifecycle_policy"}),
    ("queues",  "SQS Queue",       {"aws_sqs_queue", "aws_sqs_queue_policy"}),
    ("queues",  "CloudWatch Logs", {"aws_cloudwatch_log_group"}),
    ("queues",  "EventBridge",     {"aws_cloudwatch_event_rule",
                                    "aws_cloudwatch_event_target"}),
    ("k8s",     "Helm Release",    {"helm_release"}),
    ("k8s",     "k8s Namespace",   {"kubernetes_namespace_v1", "kubernetes_namespace"}),
    ("k8s",     "ClusterRoleBinding",{"kubernetes_cluster_role_binding"}),
    ("k8s",     "kubectl Manifest",{"kubectl_manifest"}),
]

# Headline color per category (matches the JS palette in renderDashboard).
_CATEGORY_TITLES: dict[str, dict[str, str]] = {
    "compute":    {"title": "Compute",    "icon": "⚙",  "color": "#1677ff"},
    "data":       {"title": "Data",       "icon": "◆",  "color": "#3c89e8"},
    "identity":   {"title": "Identity",   "icon": "◐",  "color": "#ff9900"},
    "networking": {"title": "Networking", "icon": "▦",  "color": "#d89614"},
    "secrets":    {"title": "Secrets / KMS","icon": "✱","color": "#a266ff"},
    "registries": {"title": "Registries", "icon": "▣",  "color": "#7c5cff"},
    "queues":     {"title": "Queues / Logs","icon": "≋","color": "#22a1c4"},
    "k8s":        {"title": "Kubernetes", "icon": "❯",  "color": "#39c47a"},
}


# v0.34.6: per-resource-type → (architecture_layer, service_label, iconify_icon)
# for the AWS-style architecture diagram on the dashboard. Layers stack
# top-to-bottom: edge → compute → data → identity → secrets → registries →
# observability → k8s. Resources without a mapping fall to a generic tile.
_ARCH_RULES: dict[str, tuple[str, str, str]] = {
    # Edge / CDN
    "aws_cloudfront_distribution":     ("edge",   "CloudFront",       "logos:aws-cloudfront"),
    # Compute
    "aws_eks_cluster":                 ("compute","EKS Cluster",      "logos:aws-eks"),
    "aws_eks_node_group":              ("compute","EKS Node Group",   "logos:aws-eks"),
    "aws_eks_addon":                   ("compute","EKS Addon",        "logos:aws-eks"),
    "aws_eks_fargate_profile":         ("compute","Fargate Profile",  "logos:aws-fargate"),
    "aws_lambda_function":             ("compute","Lambda",           "logos:aws-lambda"),
    # Data
    "aws_rds_cluster":                 ("data",   "RDS / Aurora",     "logos:aws-rds"),
    "aws_rds_cluster_instance":        ("data",   "RDS Instance",     "logos:aws-rds"),
    "aws_db_instance":                 ("data",   "RDS Instance",     "logos:aws-rds"),
    "aws_db_subnet_group":             ("data",   "DB Subnet Group",  "logos:aws-rds"),
    "aws_elasticache_replication_group":("data",  "ElastiCache",      "logos:aws-elasticache"),
    "aws_elasticache_user":            ("data",   "ElastiCache User", "logos:aws-elasticache"),
    "aws_elasticache_user_group":      ("data",   "ElastiCache Group","logos:aws-elasticache"),
    "aws_elasticache_subnet_group":    ("data",   "ElastiCache Subnet","logos:aws-elasticache"),
    "aws_ebs_volume":                  ("data",   "EBS Volume",       "logos:aws-ec2"),
    "aws_efs_file_system":             ("data",   "EFS",              "mdi:database"),
    "aws_efs_mount_target":            ("data",   "EFS Mount",        "mdi:database"),
    "aws_s3_bucket":                   ("data",   "S3 Bucket",        "logos:aws-s3"),
    # Network
    "aws_vpc":                         ("network","VPC",              "logos:aws-vpc"),
    "aws_subnet":                      ("network","Subnet",           "logos:aws-vpc"),
    "aws_nat_gateway":                 ("network","NAT Gateway",      "logos:aws-vpc"),
    "aws_internet_gateway":            ("network","Internet Gateway", "logos:aws-vpc"),
    "aws_egress_only_internet_gateway":("network","Egress-only IGW",  "logos:aws-vpc"),
    "aws_security_group":              ("network","Security Group",   "logos:aws-vpc"),
    "aws_security_group_rule":         ("network","SG Rule",          "logos:aws-vpc"),
    "aws_vpc_endpoint":                ("network","VPC Endpoint",     "logos:aws-vpc"),
    # Identity
    "aws_iam_role":                    ("identity","IAM Role",        "logos:aws-iam"),
    "aws_iam_policy":                  ("identity","IAM Policy",      "logos:aws-iam"),
    "aws_iam_role_policy_attachment":  ("identity","IAM Attachment",  "logos:aws-iam"),
    "aws_iam_role_policy":             ("identity","IAM Inline Policy","logos:aws-iam"),
    "aws_iam_openid_connect_provider": ("identity","OIDC Provider",   "logos:aws-iam"),
    # Secrets / KMS
    "aws_secretsmanager_secret":       ("secrets","Secrets Manager",  "logos:aws-secrets-manager"),
    "aws_secretsmanager_secret_version":("secrets","Secret Version",  "logos:aws-secrets-manager"),
    "aws_kms_key":                     ("secrets","KMS Key",          "logos:aws-kms"),
    "aws_kms_alias":                   ("secrets","KMS Alias",        "logos:aws-kms"),
    # Registries
    "aws_ecr_repository":              ("registries","ECR Repository","mdi:package-variant"),
    "aws_ecr_repository_policy":       ("registries","ECR Policy",    "mdi:package-variant"),
    "aws_ecr_lifecycle_policy":        ("registries","ECR Lifecycle", "mdi:package-variant"),
    # Queues / Logs / Events
    "aws_sqs_queue":                   ("ops",    "SQS Queue",        "logos:aws-sqs"),
    "aws_sqs_queue_policy":            ("ops",    "SQS Policy",       "logos:aws-sqs"),
    "aws_cloudwatch_log_group":        ("ops",    "CloudWatch Logs",  "logos:aws-cloudwatch"),
    "aws_cloudwatch_event_rule":       ("ops",    "EventBridge Rule", "logos:aws-cloudwatch"),
    "aws_cloudwatch_event_target":     ("ops",    "EventBridge Target","logos:aws-cloudwatch"),
    # Kubernetes (in-cluster, owned by Terraform providers)
    "helm_release":                    ("k8s",    "Helm Release",     "logos:helm"),
    "kubernetes_namespace_v1":         ("k8s",    "Namespace",        "logos:kubernetes"),
    "kubernetes_namespace":            ("k8s",    "Namespace",        "logos:kubernetes"),
    "kubernetes_cluster_role":         ("k8s",    "ClusterRole",      "logos:kubernetes"),
    "kubernetes_cluster_role_binding": ("k8s",    "ClusterRoleBinding","logos:kubernetes"),
    "kubectl_manifest":                ("k8s",    "kubectl Manifest", "logos:kubernetes"),
}

_ARCH_LAYERS: list[tuple[str, str]] = [
    ("edge",       "Edge / CDN"),
    ("compute",    "Compute"),
    ("data",       "Data & Storage"),
    ("network",    "Networking"),
    ("identity",   "Identity & Access"),
    ("secrets",    "Secrets / KMS"),
    ("registries", "Container Registry"),
    ("ops",        "Observability & Events"),
    ("k8s",        "Kubernetes (in-cluster)"),
]


def _compute_architecture(resource_nodes: list[dict]) -> dict:
    """AWS-style architecture diagram payload — services grouped per layer.

    Output:
      {
        "layers": [
          {"key": "compute", "title": "Compute", "service_count": 4, "total": 8,
           "services": [
             {"label": "EKS Cluster", "icon": "logos:aws-eks", "count": 1,
              "rtype": "aws_eks_cluster",
              "items": [{"address":..., "module":..., "env":..., "details": {...}}]},
             ...
           ]},
          ...
        ],
        "total_services": int,
        "total_resources": int,
      }
    """
    by_layer_service: dict[tuple[str, str], dict] = {}
    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        rule = _ARCH_RULES.get(rtype)
        if not rule:
            continue
        layer, label, icon = rule
        key = (layer, label)
        bucket = by_layer_service.get(key)
        if bucket is None:
            bucket = {
                "label": label,
                "icon": icon,
                "rtype": rtype,
                "count": 0,
                "items": [],
            }
            by_layer_service[key] = bucket
        bucket["count"] += 1
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        bucket["items"].append({
            "address": n.get("label") or "",
            "module": n.get("module") or "",
            "env": n.get("environment") or "",
            "details": first,
        })

    layers_out = []
    for layer_key, layer_title in _ARCH_LAYERS:
        services = [v for (lk, _), v in by_layer_service.items() if lk == layer_key]
        if not services:
            continue
        services.sort(key=lambda s: -s["count"])
        for s in services:
            s["items"].sort(key=lambda x: (x["env"], x["address"]))
            s["items"] = s["items"][:50]  # cap per service
        layers_out.append({
            "key": layer_key,
            "title": layer_title,
            "service_count": len(services),
            "total": sum(s["count"] for s in services),
            "services": services,
        })
    return {
        "layers": layers_out,
        "total_services": sum(L["service_count"] for L in layers_out),
        "total_resources": sum(L["total"] for L in layers_out),
    }


def _resource_category(rtype: str) -> tuple[str, str] | None:
    for cat, kind, types in _CATEGORY_RULES:
        if rtype in types:
            return (cat, kind)
    return None


def _flag_findings(rtype: str, ess: dict) -> list[str]:
    """Return human-readable findings for an essentials block — currently
    only flags `0.0.0.0/0` ingress on aws_security_group_rule. Returned
    list is rendered as red chips on the dashboard card."""
    findings: list[str] = []
    if rtype == "aws_security_group_rule":
        cidrs = ess.get("cidr_blocks") or []
        if isinstance(cidrs, list) and any(c == "0.0.0.0/0" for c in cidrs if isinstance(c, str)):
            kind = ess.get("type") or "rule"
            ports = (
                f"{ess.get('from_port')}-{ess.get('to_port')}"
                if ess.get("from_port") != ess.get("to_port")
                else f"{ess.get('from_port')}"
            )
            findings.append(f"open to 0.0.0.0/0 ({kind} {ports}/{ess.get('protocol','?')})")
    return findings


def _compute_categories(resource_nodes: list[dict]) -> dict:
    """Bucket resource nodes by category and produce the per-card payload
    consumed by JS renderDashboard. See _CATEGORY_RULES for the resource_type
    → category mapping. Resources without a category map are dropped from
    this aggregation (they still appear on the legacy state-overlay card)."""
    by_cat: dict[str, dict] = {}
    for cat in _CATEGORY_TITLES:
        meta = _CATEGORY_TITLES[cat]
        by_cat[cat] = {
            "name": cat,
            "title": meta["title"],
            "icon": meta["icon"],
            "color": meta["color"],
            "kind_counts": defaultdict(int),
            "items": [],
            "totals": {},
            "findings": [],
        }
    # Aggregators that need a running tally across resources.
    ebs_total_gb = 0
    iam_principal_kinds: dict[str, int] = defaultdict(int)
    helm_charts: list[str] = []
    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        cat_kind = _resource_category(rtype)
        if not cat_kind:
            continue
        cat, kind = cat_kind
        bucket = by_cat[cat]
        bucket["kind_counts"][kind] += 1
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        item = {
            "address": n.get("label") or "",
            "env": n.get("environment") or "",
            "module": n.get("module") or "",
            "kind": kind,
            "rtype": rtype,
            "details": first,  # raw essentials for drill-down
        }
        # Per-resource enrichments.
        if rtype == "aws_ebs_volume":
            sz = first.get("size")
            if isinstance(sz, (int, float)):
                ebs_total_gb += int(sz)
                item["display"] = f"{int(sz)} GB · {first.get('type','?')}"
        elif rtype == "aws_iam_role":
            principals = first.get("principals") or []
            if isinstance(principals, list):
                for p in principals:
                    if isinstance(p, str) and ":" in p:
                        kind_part = p.split(":", 1)[0]
                        iam_principal_kinds[kind_part] += 1
                item["display"] = f"{len(principals)} trust principal{'s' if len(principals) != 1 else ''}"
                item["principals"] = principals
        elif rtype in ("aws_db_instance", "aws_rds_cluster_instance"):
            ic = first.get("instance_class") or ""
            ev = first.get("engine_version") or first.get("engine") or ""
            item["display"] = " · ".join(x for x in (ic, ev) if x)
        elif rtype == "aws_elasticache_replication_group":
            nt = first.get("node_type") or ""
            ng = first.get("num_node_groups")
            ev = first.get("engine_version") or ""
            sub = []
            if nt: sub.append(nt)
            if isinstance(ng, int): sub.append(f"{ng} shard{'s' if ng != 1 else ''}")
            if ev: sub.append(f"v{ev}")
            item["display"] = " · ".join(sub)
        elif rtype == "aws_eks_cluster":
            v = first.get("version") or ""
            item["display"] = f"k8s {v}" if v else ""
        elif rtype == "aws_eks_node_group":
            it = first.get("instance_types") or []
            sc = first.get("scaling_config") or {}
            ds = first.get("disk_size")
            sub = []
            if isinstance(it, list) and it: sub.append(",".join(it[:3]))
            if isinstance(sc, dict):
                desired = sc.get("desired_size"); mn = sc.get("min_size"); mx = sc.get("max_size")
                if desired is not None: sub.append(f"{mn}-{desired}-{mx} nodes")
            if ds: sub.append(f"{ds} GB disk")
            item["display"] = " · ".join(sub)
        elif rtype == "aws_lambda_function":
            rt = first.get("runtime") or ""
            mem = first.get("memory_size")
            sub = []
            if rt: sub.append(rt)
            if mem: sub.append(f"{mem} MB")
            item["display"] = " · ".join(sub)
        elif rtype == "helm_release":
            chart = n.get("resource_name") or ""
            if chart:
                helm_charts.append(chart)
        # Surface findings (e.g. 0.0.0.0/0 SG rules) on both the item AND the bucket.
        finds = _flag_findings(rtype, first)
        if finds:
            item["findings"] = finds
            bucket["findings"].extend(finds)
        bucket["items"].append(item)
    # Per-category totals — sub-line rendered under the title.
    by_cat["data"]["totals"]["ebs_total_gb"] = ebs_total_gb
    by_cat["identity"]["totals"]["principal_kinds"] = dict(iam_principal_kinds)
    by_cat["k8s"]["totals"]["helm_charts"] = sorted(set(helm_charts))[:32]
    # Coerce defaultdicts to dicts for JSON output.
    for cat in by_cat.values():
        cat["kind_counts"] = dict(cat["kind_counts"])
        cat["items"].sort(key=lambda x: (x["kind"], x["env"], x["address"]))
        cat["count"] = sum(cat["kind_counts"].values())
        # Headline string: "N items · top kinds"
        top_kinds = sorted(cat["kind_counts"].items(), key=lambda x: -x[1])[:3]
        cat["headline"] = " · ".join(
            f"{c} {k}{'s' if c != 1 and not k.endswith('s') else ''}" for k, c in top_kinds
        )
    return by_cat


# v0.35.0 (Tier 2): repo-walking signal helpers.
def _scan_secret_references(repo: Path,
                            resource_nodes: list[dict]) -> dict:
    """Walk components/<env>/*.json, collect every value at a key whose name
    contains "secret"/"password"/"api_key"/"token". Cross-reference with
    aws_secretsmanager_secret addresses extracted from state.

    Output:
      {
        "secrets": [
          {"address": "aws_secretsmanager_secret.x", "name": "...", "module": "...",
           "env": "...", "referenced_by": [{"file": "...", "key": "...", "value": "..."}]},
          ...
        ],
        "orphan_refs": [{"file": "...", "key": "...", "value": "..."}]
      }
    Heuristic: a JSON value matches a known secret name → that secret is
    referenced from that component file. Otherwise the reference is
    "orphan" (may be a creation-time intent or external secret).
    """
    secret_re_keys = ("secret", "password", "api_key", "apikey", "token", "credential")
    components_dir = repo / "components"
    refs_by_value: dict[str, list[dict]] = {}
    if components_dir.is_dir():
        for env_dir in sorted(components_dir.iterdir()):
            if not env_dir.is_dir() or env_dir.name.startswith("."):
                continue
            for jp in sorted(env_dir.glob("*.json")):
                try:
                    data = json.loads(jp.read_text())
                except (ValueError, OSError):
                    continue
                rel = str(jp.relative_to(repo))
                # Walk recursively up to a sensible depth.
                stack: list[tuple[object, list[str]]] = [(data, [])]
                visited = 0
                while stack and visited < 5000:
                    visited += 1
                    obj, path = stack.pop()
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            kl = str(k).lower()
                            if isinstance(v, (str,)) and len(v) <= 256 \
                               and any(tok in kl for tok in secret_re_keys):
                                refs_by_value.setdefault(v, []).append({
                                    "file": rel, "key": ".".join(path + [str(k)]),
                                    "value": v,
                                })
                            elif isinstance(v, (dict, list)):
                                stack.append((v, path + [str(k)]))
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            if isinstance(item, (dict, list)):
                                stack.append((item, path + [str(i)]))
    # Cross-ref with aws_secretsmanager_secret resources.
    secrets_out: list[dict] = []
    referenced_values: set[str] = set()
    for n in resource_nodes:
        if n.get("resource_type") != "aws_secretsmanager_secret":
            continue
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        sec_name = first.get("name") or n.get("resource_name") or ""
        addr = n.get("label") or ""
        # Match value heuristically: secret name appears verbatim OR ends with
        # the resource_name component.
        matches: list[dict] = []
        for val, refs in refs_by_value.items():
            if val == sec_name or val.endswith(n.get("resource_name") or ""):
                matches.extend(refs)
                referenced_values.add(val)
        secrets_out.append({
            "address": addr,
            "name": sec_name,
            "module": n.get("module") or "",
            "env": n.get("environment") or "",
            "referenced_by": matches[:10],
        })
    secrets_out.sort(key=lambda x: (-len(x["referenced_by"]), x["env"], x["name"]))
    orphans = []
    for val, refs in refs_by_value.items():
        if val in referenced_values:
            continue
        for r in refs[:3]:
            orphans.append(r)
    orphans = orphans[:50]
    return {"secrets": secrets_out[:200], "orphan_refs": orphans}


def _scan_cue_schemas(repo: Path) -> dict:
    """Lightweight CUE schema field extractor. We don't have the cue binary
    requirement here — instead we walk `cue/**/*.cue`, pluck top-level
    `Field: type` declarations using a regex, and report a flat list of
    schema files + their declared fields. Best-effort.
    """
    cue_dir = repo / "cue"
    out_files: list[dict] = []
    if not cue_dir.is_dir():
        return {"files": [], "field_count": 0}
    field_re = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$')
    pkg_re = re.compile(r'^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)\s*$')
    for cf in sorted(cue_dir.rglob("*.cue")):
        if "cue.mod" in cf.parts:
            continue
        rel = str(cf.relative_to(repo))
        try:
            text = cf.read_text(errors="replace")
        except OSError:
            continue
        package = ""
        fields: list[dict] = []
        for line in text.splitlines():
            ls = line.strip()
            if not ls or ls.startswith("//") or ls.startswith("import"):
                continue
            m = pkg_re.match(line)
            if m:
                package = m.group(1)
                continue
            indent = len(line) - len(line.lstrip(" ")) - len(line.lstrip(" \t"))
            if indent > 0:
                continue
            m = field_re.match(line)
            if not m:
                continue
            name, ftype = m.group(1), m.group(2)
            # Trim long type expressions for the dashboard.
            if len(ftype) > 80:
                ftype = ftype[:80] + "…"
            fields.append({"name": name, "type": ftype})
        if not fields:
            continue
        out_files.append({
            "file": rel,
            "package": package,
            "field_count": len(fields),
            "fields": fields[:60],
        })
    out_files.sort(key=lambda x: (-x["field_count"], x["file"]))
    return {
        "files": out_files,
        "file_count": len(out_files),
        "field_count": sum(f["field_count"] for f in out_files),
    }


def _load_rendered_apps(repo: Path) -> dict:
    """Auto-load any `.kuberly/rendered_apps_<env>.json` that the manual
    render_apps.py script has produced. Returns {} if none — the dashboard
    section degrades to a "run render_apps.py" hint."""
    out_dir = repo / ".kuberly"
    if not out_dir.is_dir():
        return {}
    by_env: dict[str, dict] = {}
    for p in sorted(out_dir.glob("rendered_apps_*.json")):
        env = p.stem[len("rendered_apps_"):]
        try:
            data = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        # Strip per-resource detail to keep the dashboard payload small —
        # the dashboard only needs counts + kind breakdowns, not every line.
        apps_summary = []
        for a in data.get("apps", []):
            apps_summary.append({
                "app": a.get("app", ""),
                "ok": a.get("ok", False),
                "resource_count": a.get("resource_count", 0),
                "kind_counts": a.get("kind_counts", {}),
                "error": a.get("error"),
            })
        by_env[env] = {
            "env": env,
            "generated_at": data.get("generated_at", ""),
            "app_count": data.get("app_count", len(apps_summary)),
            "resource_count": data.get("resource_count", 0),
            "apps": apps_summary,
        }
    return by_env


def _load_app_drift(repo: Path) -> dict:
    """Auto-load any `.kuberly/app_drift_<env>.json` that the manual
    diff_apps.py script has produced. Returns {} if none."""
    out_dir = repo / ".kuberly"
    if not out_dir.is_dir():
        return {}
    by_env: dict[str, dict] = {}
    for p in sorted(out_dir.glob("app_drift_*.json")):
        env = p.stem[len("app_drift_"):]
        try:
            data = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        apps_lite = []
        for a in data.get("apps", []):
            apps_lite.append({
                "app": a.get("app", ""),
                "summary": a.get("summary", {}),
                "missing": [{"kind": r.get("kind", ""), "name": r.get("name", "")}
                            for r in (a.get("missing_in_cluster") or [])[:8]],
                "extra":   [{"kind": r.get("kind", ""), "name": r.get("name", "")}
                            for r in (a.get("extra_in_cluster") or [])[:8]],
            })
        by_env[env] = {
            "env": env,
            "generated_at": data.get("generated_at", ""),
            "apps": apps_lite,
        }
    return by_env


def _scan_workflow_origins(repo: Path) -> dict:
    """Map `.github/workflows/*.yml` jobs to the module dirs they reference.

    Heuristic: scan the YAML text for module-style strings like
    `clouds/aws/modules/<m>` or `components/<env>/<m>.json`. Returns:
      {
        "workflows": [
          {"file": ".github/workflows/x.yml", "module_refs": [...],
           "component_refs": [...], "trigger": "..."}
        ]
      }
    No PyYAML dep — line-based scan is enough for our heuristic.
    """
    wf_dir = repo / ".github" / "workflows"
    out = []
    if not wf_dir.is_dir():
        return {"workflows": []}
    mod_re = re.compile(r"clouds/aws/modules/([a-z0-9_]+)")
    comp_re = re.compile(r"components/([a-z0-9_-]+)/([a-z0-9_-]+)\.json")
    trigger_re = re.compile(r'^\s*on:\s*(\S.*)?$')
    for wf in sorted(wf_dir.glob("*.y*ml")):
        try:
            text = wf.read_text(errors="replace")
        except OSError:
            continue
        modules = sorted(set(mod_re.findall(text)))
        comps = sorted(set(comp_re.findall(text)))
        # Try to pluck the trigger types.
        triggers = []
        for line in text.splitlines():
            m = trigger_re.match(line)
            if m and m.group(1):
                triggers.append(m.group(1).strip("[]{}, "))
                break
        # Look for `on:` block lines: push / pull_request / workflow_call / schedule
        for line in text.splitlines():
            ls = line.strip()
            if ls in ("push:", "pull_request:", "workflow_call:",
                      "workflow_dispatch:", "schedule:"):
                triggers.append(ls.rstrip(":"))
        triggers = sorted(set(triggers))
        out.append({
            "file": str(wf.relative_to(repo)),
            "module_refs": modules,
            "component_refs": [{"env": e, "name": n} for e, n in comps],
            "triggers": triggers,
        })
    return {"workflows": out}


# v0.35.0: customer-facing dashboard signals — replaces Modules/Components
# meta with what an operator actually wants to know about their stack.
def _compute_security_findings(resource_nodes: list[dict]) -> dict:
    """Aggregate security findings from schema-v3 essentials.

    Severities (informational only — we don't grade compliance frameworks):
      high   — public/world ingress, public S3, unencrypted at rest
      medium — cross-account IAM trust, missing encryption hints
      low    — informational notes worth surfacing but not urgent
    """
    findings: dict[str, list[dict]] = {"high": [], "medium": [], "low": []}
    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        ess_list = n.get("essentials") or []
        if not ess_list:
            continue
        ess = ess_list[0] if isinstance(ess_list[0], dict) else {}
        addr = n.get("label") or ""
        env = n.get("environment") or ""
        mod = n.get("module") or ""
        # 0.0.0.0/0 ingress
        if rtype == "aws_security_group_rule":
            cidrs = ess.get("cidr_blocks") or []
            if isinstance(cidrs, list) and any(c == "0.0.0.0/0" for c in cidrs if isinstance(c, str)):
                kind = ess.get("type") or "rule"
                ports = (
                    f"{ess.get('from_port')}-{ess.get('to_port')}"
                    if ess.get("from_port") != ess.get("to_port")
                    else f"{ess.get('from_port')}"
                )
                findings["high"].append({
                    "rule": "open-to-internet",
                    "address": addr, "module": mod, "env": env,
                    "detail": f"{kind} {ports}/{ess.get('protocol','?')} from 0.0.0.0/0",
                })
        # Unencrypted EBS
        if rtype == "aws_ebs_volume" and ess.get("encrypted") is False:
            findings["high"].append({
                "rule": "unencrypted-ebs",
                "address": addr, "module": mod, "env": env,
                "detail": f"{ess.get('size','?')} GB {ess.get('type','?')} not encrypted",
            })
        # Unencrypted EFS
        if rtype == "aws_efs_file_system" and ess.get("encrypted") is False:
            findings["high"].append({
                "rule": "unencrypted-efs",
                "address": addr, "module": mod, "env": env,
                "detail": "EFS file system not encrypted at rest",
            })
        # IAM cross-account trust
        if rtype == "aws_iam_role":
            principals = ess.get("principals") or []
            if isinstance(principals, list):
                for p in principals:
                    if not isinstance(p, str) or not p.startswith("aws:"):
                        continue
                    # aws:arn:aws:iam::<acct>:root or :role/X — flag if acct
                    # is not the cluster's own account. We don't know the cluster
                    # account here at this layer, so flag any external-looking
                    # arn that isn't an AWS service principal.
                    if ":root" in p or ":role/" in p:
                        findings["medium"].append({
                            "rule": "iam-cross-account-trust",
                            "address": addr, "module": mod, "env": env,
                            "detail": f"role can be assumed by {p}",
                        })
                        break  # one finding per role
        # Federated trust (OIDC) — informational
        if rtype == "aws_iam_role":
            principals = ess.get("principals") or []
            if isinstance(principals, list):
                for p in principals:
                    if isinstance(p, str) and p.startswith("federated:"):
                        findings["low"].append({
                            "rule": "iam-federated-trust",
                            "address": addr, "module": mod, "env": env,
                            "detail": p[10:][:80],
                        })
                        break
        # Publicly-accessible RDS instance
        if rtype == "aws_rds_cluster_instance" and ess.get("publicly_accessible") is True:
            findings["high"].append({
                "rule": "rds-publicly-accessible",
                "address": addr, "module": mod, "env": env,
                "detail": f"{ess.get('instance_class','?')} {ess.get('engine','?')} reachable from internet",
            })
        # CloudWatch log group with no retention (logs forever — cost finding,
        # not security, but still worth surfacing).
        if rtype == "aws_cloudwatch_log_group":
            ret = ess.get("retention_in_days")
            if ret is None or ret == 0:
                findings["low"].append({
                    "rule": "cw-log-no-retention",
                    "address": addr, "module": mod, "env": env,
                    "detail": "log group has no retention (kept forever)",
                })
    summary = {sev: len(v) for sev, v in findings.items()}
    summary["total"] = sum(summary.values())
    return {"summary": summary, "items": findings}


def _compute_module_age(repo: Path, snap_times: dict[str, str],
                        resource_nodes: list[dict]) -> dict:
    """Module age summary — how recently each module's state was applied,
    plus the env(s) where it's deployed.

    Output:
      [{"module": "eks", "envs": [...], "snapshot_at": "...", "resources": int,
        "age_seconds": float | None}, ...]
    """
    from datetime import datetime, timezone
    by_mod: dict[str, dict] = {}
    for n in resource_nodes:
        m = n.get("module") or ""
        if not m:
            continue
        env = n.get("environment") or ""
        if m not in by_mod:
            by_mod[m] = {"module": m, "envs": set(), "resources": 0,
                         "snapshot_at": "", "age_seconds": None}
        by_mod[m]["envs"].add(env)
        by_mod[m]["resources"] += 1
        # Use the most-recent env snapshot we have a timestamp for.
        sa = snap_times.get(env, "")
        if sa and sa > by_mod[m]["snapshot_at"]:
            by_mod[m]["snapshot_at"] = sa
    now = datetime.now(timezone.utc)
    rows = []
    for m, d in by_mod.items():
        sa = d["snapshot_at"]
        age = None
        if sa:
            try:
                t = datetime.fromisoformat(sa.replace("Z", "+00:00"))
                age = (now - t).total_seconds()
            except ValueError:
                pass
        rows.append({
            "module": m,
            "envs": sorted(d["envs"]),
            "resources": d["resources"],
            "snapshot_at": sa,
            "age_seconds": age,
        })
    rows.sort(key=lambda r: (-(r["age_seconds"] or 0), r["module"]))
    return rows


def _compute_app_secret_iam(graph: "KuberlyPlatform",
                            resource_nodes: list[dict]) -> dict:
    """End-to-end "what AWS secrets can each app read" path.

    Walks: k8s ServiceAccount → irsa_bound edge → IAM role → role's policy
    attachments → policy resource ARNs that look like Secrets Manager arns.
    Without schema-v3 IAM policy `policy` body essentials, we can only count
    the role's policy attachments — actual secret ARNs need a follow-up
    schema bump.

    Output:
      [{"app": "backend", "ns": "stage5-prod", "env": "prod",
        "service_account": "backend", "iam_role": "...",
        "policy_attachments": int, "inline_policies": int,
        "irsa_arn": "arn:..."}, ...]
    """
    rows: list[dict] = []
    nodes = graph.nodes
    # Map role-name → counts of attachments / inline policies.
    attach_per_role: dict[str, int] = {}
    inline_per_role: dict[str, int] = {}
    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        rname = first.get("role")
        if not rname:
            continue
        if rtype == "aws_iam_role_policy_attachment":
            attach_per_role[rname] = attach_per_role.get(rname, 0) + 1
        elif rtype == "aws_iam_role_policy":
            inline_per_role[rname] = inline_per_role.get(rname, 0) + 1

    for e in graph.edges:
        if e.get("relation") != "irsa_bound":
            continue
        sa = nodes.get(e["source"], {})
        role = nodes.get(e["target"], {})
        if sa.get("k8s_kind") != "ServiceAccount":
            continue
        # Map SA → workload via "uses_sa" edge (workload → SA).
        sa_id = e["source"]
        workloads = []
        for e2 in graph.edges:
            if e2.get("relation") != "uses_sa" or e2.get("target") != sa_id:
                continue
            wn = nodes.get(e2["source"], {})
            if not wn:
                continue
            workloads.append({
                "kind": wn.get("k8s_kind") or "?",
                "name": wn.get("k8s_name") or wn.get("label") or "?",
                "ns": wn.get("k8s_namespace") or "",
            })
        # Role essentials: figure out the role's name for policy lookup.
        role_label = role.get("label") or ""
        # Conventional address shape: "[module.x.]aws_iam_role.name"
        role_name = role_label.rsplit(".", 1)[-1] if role_label else ""
        rows.append({
            "service_account": sa.get("k8s_name") or sa.get("label") or "?",
            "ns": sa.get("k8s_namespace") or "",
            "env": sa.get("environment") or "",
            "iam_role": role_label,
            "iam_role_name": role_name,
            "policy_attachments": attach_per_role.get(role_name, 0),
            "inline_policies": inline_per_role.get(role_name, 0),
            "workloads": workloads,
        })
    rows.sort(key=lambda r: (r["env"], r["ns"], r["service_account"]))
    return rows


def _compute_network_reachability(resource_nodes: list[dict]) -> dict:
    """Per-SG: who can reach what. SG rules (ingress/egress) with cidr_blocks
    or source_security_group_id from schema-v3 essentials, grouped by SG.

    Output:
      [{"sg": "addr", "module": "...", "env": "...",
        "name": "...", "description": "...",
        "ingress": [{"from": "0.0.0.0/0", "ports": "443", "proto": "tcp"}, ...],
        "egress":  [...]}]
    """
    sg_by_addr: dict[str, dict] = {}
    rules_by_sg: dict[str, list] = {}
    # First pass — collect SG metadata.
    for n in resource_nodes:
        if n.get("resource_type") != "aws_security_group":
            continue
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        addr = n.get("label") or ""
        sg_by_addr[addr] = {
            "addr": addr,
            "module": n.get("module") or "",
            "env": n.get("environment") or "",
            "name": first.get("name") or "",
            "description": first.get("description") or "",
        }
        rules_by_sg[addr] = []
    # Second pass — attach rules to SGs by depends_on (rule resource depends on its SG).
    for n in resource_nodes:
        if n.get("resource_type") != "aws_security_group_rule":
            continue
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        cidrs = first.get("cidr_blocks") or []
        src_sg = first.get("source_security_group_id") or ""
        from_port = first.get("from_port")
        to_port = first.get("to_port")
        ports = f"{from_port}-{to_port}" if from_port != to_port else f"{from_port}"
        rule_kind = first.get("type") or ""
        # Heuristic: a rule depends on its parent SG. We can't easily resolve
        # the parent SG without parsing depends_on addresses — for now, attach
        # to all SGs in the same module (best-effort summary).
        mod = n.get("module") or ""
        sources = list(cidrs) if isinstance(cidrs, list) else []
        if src_sg:
            sources.append(f"sg:{src_sg}")
        rule_row = {
            "ports": ports,
            "proto": first.get("protocol") or "?",
            "sources": sources,
            "open_to_world": "0.0.0.0/0" in (cidrs or []),
        }
        for sg in sg_by_addr.values():
            if sg["module"] == mod:
                rules_by_sg[sg["addr"]].append({**rule_row, "kind": rule_kind})
    out = []
    for addr, sg in sorted(sg_by_addr.items()):
        rules = rules_by_sg.get(addr, [])
        ingress = [r for r in rules if r["kind"] == "ingress"]
        egress  = [r for r in rules if r["kind"] == "egress"]
        out.append({
            **sg,
            "ingress": ingress,
            "egress": egress,
            "open_to_world": any(r["open_to_world"] for r in ingress),
        })
    out.sort(key=lambda x: (not x["open_to_world"], x["env"], x["module"], x["name"]))
    return out


def _compute_app_health(graph: "KuberlyPlatform") -> dict:
    """Roll-up of app health from k8s overlay: declared replicas vs ready.

    Returns:
      {"by_app": [{"app": "backend", "ns": "...", "env": "...",
                   "replicas": 2, "ready": 2, "healthy": True}, ...],
       "summary": {"total": int, "healthy": int, "unhealthy": int}}
    """
    by_app = []
    healthy = unhealthy = 0
    for nid, n in graph.nodes.items():
        if n.get("type") != "k8s_resource":
            continue
        kind = n.get("k8s_kind") or ""
        if kind not in ("Deployment", "StatefulSet"):
            continue
        replicas = n.get("k8s_replicas")
        ready = n.get("k8s_ready_replicas")
        if not isinstance(replicas, int):
            replicas = 0
        if not isinstance(ready, int):
            ready = 0
        is_healthy = replicas > 0 and ready >= replicas
        if is_healthy:
            healthy += 1
        else:
            unhealthy += 1
        by_app.append({
            "app": n.get("k8s_name") or n.get("label") or nid,
            "kind": kind,
            "ns": n.get("k8s_namespace") or "",
            "env": n.get("environment") or "",
            "replicas": replicas,
            "ready": ready,
            "healthy": is_healthy,
        })
    by_app.sort(key=lambda r: (r["healthy"], r["env"], r["ns"], r["app"]))
    return {"by_app": by_app[:200],
            "summary": {"total": healthy + unhealthy,
                        "healthy": healthy, "unhealthy": unhealthy}}


def _compute_iam_view(resource_nodes: list[dict], edges: list[dict],
                      all_nodes: dict[str, dict]) -> dict:
    """Aggregate every IAM role into a per-module grouped payload for the
    dedicated IAM dashboard section.

    Output:
      {
        "has_essentials": bool — true if any role carries schema-v3 principals
        "total_roles": int,
        "principals_total": int,
        "principal_kinds": {service: 8, aws: 2, federated: 5, ...},
        "groups": [
          {
            "module": "alb_controller",
            "envs": ["prod"],
            "roles": [
              {"address": "...", "name": "...", "env": "...",
               "principals": [...], "attached_policies": int, "policies_inline": int}
            ],
          },
          ...
        ],
        "oidc_providers": [{"address": "...", "url": "...", "module": "..."}],
        "irsa_bindings": copied from k8s view for cross-link convenience,
      }

    Even WITHOUT schema-v3 essentials, every role still shows up — just
    without principals. The dashboard surfaces a "regen state for trust
    principals" hint when has_essentials is False.
    """
    by_module: dict[str, dict] = defaultdict(lambda: {"roles": [], "envs": set()})
    has_essentials = False
    principal_kinds: dict[str, int] = defaultdict(int)
    principals_total = 0
    oidc_providers: list[dict] = []

    # Count how many `aws_iam_role_policy_attachment` rows reference each
    # role (by source-edge target) so we can surface "X attached policies"
    # per role even without schema v3.
    attach_per_role: dict[str, int] = defaultdict(int)
    inline_per_role: dict[str, int] = defaultdict(int)
    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        if rtype == "aws_iam_role_policy_attachment":
            ess_list = n.get("essentials") or []
            role_name = ""
            if ess_list and isinstance(ess_list[0], dict):
                role_name = (ess_list[0].get("role") or "")
            # Without essentials we can only count, not link.
            attach_per_role[role_name] += 1 if role_name else 0
        elif rtype == "aws_iam_role_policy":
            ess_list = n.get("essentials") or []
            role_name = ""
            if ess_list and isinstance(ess_list[0], dict):
                role_name = (ess_list[0].get("role") or "")
            inline_per_role[role_name] += 1 if role_name else 0

    for n in resource_nodes:
        rtype = n.get("resource_type") or ""
        if rtype == "aws_iam_openid_connect_provider":
            ess_list = n.get("essentials") or []
            first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
            oidc_providers.append({
                "address": n.get("label") or "",
                "url": first.get("url") or "",
                "client_id_list": first.get("client_id_list") or [],
                "module": n.get("module") or "",
                "env": n.get("environment") or "",
            })
            continue
        if rtype != "aws_iam_role":
            continue
        ess_list = n.get("essentials") or []
        first = ess_list[0] if ess_list and isinstance(ess_list[0], dict) else {}
        role_name = first.get("name") or n.get("resource_name") or ""
        principals = first.get("principals") or []
        if isinstance(principals, list):
            for p in principals:
                if not isinstance(p, str) or ":" not in p:
                    continue
                principal_kinds[p.split(":", 1)[0]] += 1
                principals_total += 1
        if principals:
            has_essentials = True
        module = n.get("module") or ""
        env = n.get("environment") or ""
        bucket = by_module[module]
        bucket["envs"].add(env)
        bucket["roles"].append({
            "address": n.get("label") or "",
            "name": role_name,
            "env": env,
            "principals": principals if isinstance(principals, list) else [],
            "max_session_duration": first.get("max_session_duration"),
            "attached_policies": attach_per_role.get(role_name, 0),
            "policies_inline": inline_per_role.get(role_name, 0),
        })

    groups = []
    for mod in sorted(by_module):
        b = by_module[mod]
        b["roles"].sort(key=lambda r: (r["env"], r["name"]))
        groups.append({
            "module": mod,
            "envs": sorted(b["envs"]),
            "role_count": len(b["roles"]),
            "principal_count": sum(len(r["principals"]) for r in b["roles"]),
            "roles": b["roles"],
        })
    groups.sort(key=lambda g: -g["role_count"])

    irsa_subset = []
    for e in edges:
        if e.get("relation") != "irsa_bound":
            continue
        sa = all_nodes.get(e["source"], {})
        role = all_nodes.get(e["target"], {})
        irsa_subset.append({
            "sa": sa.get("k8s_name") or sa.get("label") or e["source"],
            "ns": sa.get("k8s_namespace") or "",
            "env": sa.get("environment") or "",
            "role": role.get("label") or e["target"],
        })
    irsa_subset.sort(key=lambda x: (x["env"], x["ns"], x["sa"]))

    return {
        "has_essentials": has_essentials,
        "total_roles": sum(g["role_count"] for g in groups),
        "principals_total": principals_total,
        "principal_kinds": dict(principal_kinds),
        "groups": groups,
        "oidc_providers": oidc_providers,
        "irsa_bindings": irsa_subset[:200],
    }


def _compute_dashboard_data(
    graph: KuberlyPlatform, out_dir: Path | None = None
) -> dict:
    """Build the dashboard JSON payload — read-only against the graph.

    Reuses `compute_stats`, `cross_env_drift`, and `_node_source_layer`.
    No new scanners; everything here is a projection over already-loaded
    nodes / edges / overlay metadata.
    """
    nodes = graph.nodes
    edges = graph.edges
    stats = graph.compute_stats()
    drift = graph.cross_env_drift()

    # In/out degree (re-computed from edges to avoid recomputing in
    # `compute_stats` and to give all nodes a degree even if they're
    # outside the top-10 critical list).
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for e in edges:
        out_deg[e["source"]] += 1
        in_deg[e["target"]] += 1

    by_type: dict[str, list] = defaultdict(list)
    for n in nodes.values():
        by_type[n.get("type", "")].append(n)

    envs = sorted(by_type.get("environment", []), key=lambda x: x.get("label", ""))
    modules = by_type.get("module", [])
    components = by_type.get("component", [])
    apps = by_type.get("application", [])
    shared_infras = by_type.get("shared-infra", [])
    docs_nodes = by_type.get("doc", [])
    k8s_nodes = by_type.get("k8s_resource", [])

    # Per-env summaries.
    env_data = []
    for env in envs:
        ename = env["label"]
        si = next((s for s in shared_infras if s.get("environment") == ename), {})
        env_components = [c for c in components if c.get("environment") == ename]
        env_apps = [a for a in apps if a.get("environment") == ename]
        env_k8s = [k for k in k8s_nodes if k.get("environment") == ename]
        env_namespaces = sorted({k.get("k8s_namespace", "") for k in env_k8s
                                 if k.get("k8s_namespace")})
        env_pods = [k for k in env_k8s if k.get("k8s_kind") == "Pod"]
        env_deployments = [k for k in env_k8s if k.get("k8s_kind") == "Deployment"]
        env_data.append({
            "name": ename,
            "account_id": si.get("account_id", ""),
            "region": si.get("region", ""),
            "cluster_name": si.get("cluster_name", ""),
            "components": len(env_components),
            "applications": len(env_apps),
            "k8s_namespaces": len(env_namespaces),
            "k8s_pods": len(env_pods),
            "k8s_deployments": len(env_deployments),
            "drift_components": sorted(drift.get("components", {}).get(ename, [])),
            "drift_apps": sorted(drift.get("applications", {}).get(ename, [])),
        })

    # Top critical nodes by in-degree (top 20 — richer than `compute_stats`'s 10).
    ranked_deg = sorted(
        ((nid, in_deg.get(nid, 0), out_deg.get(nid, 0)) for nid in nodes),
        key=lambda x: x[1],
        reverse=True,
    )
    critical = []
    for nid, ind, outd in ranked_deg[:20]:
        n = nodes.get(nid, {})
        critical.append({
            "id": nid,
            "label": n.get("label", nid),
            "type": n.get("type", ""),
            "provider": n.get("provider", ""),
            "in_degree": ind,
            "out_degree": outd,
        })

    # Provider distribution (modules only).
    provider_counts: dict[str, int] = defaultdict(int)
    for m in modules:
        provider_counts[m.get("provider") or "unknown"] += 1
    providers = [{"name": p, "modules": c}
                 for p, c in sorted(provider_counts.items(), key=lambda x: (-x[1], x[0]))]

    # Module catalog: deps / dependents / which envs use it.
    mod_deps_count: dict[str, int] = defaultdict(int)
    mod_dependents_count: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.get("relation") == "depends_on":
            if e["source"].startswith("module:"):
                mod_deps_count[e["source"]] += 1
            if e["target"].startswith("module:"):
                mod_dependents_count[e["target"]] += 1
    mod_envs: dict[str, set] = defaultdict(set)
    for e in edges:
        if e.get("relation") == "configures_module":
            src = nodes.get(e["source"], {})
            if src.get("environment"):
                mod_envs[e["target"]].add(src["environment"])
    module_table = []
    for m in sorted(modules, key=lambda x: (x.get("provider") or "", x.get("label") or "")):
        nid = m["id"]
        desc = (m.get("description") or "").strip()
        if len(desc) > 140:
            desc = desc[:137] + "…"
        module_table.append({
            "id": nid,
            "provider": m.get("provider") or "",
            "name": m.get("label") or "",
            "description": desc,
            "deps": mod_deps_count.get(nid, 0),
            "dependents": mod_dependents_count.get(nid, 0),
            "envs": sorted(mod_envs.get(nid, set())),
        })

    doc_mentions_count: dict[str, int] = defaultdict(int)
    for e in edges:
        if e.get("relation") == "mentions" and str(e.get("target", "")).startswith(
            "module:"
        ):
            doc_mentions_count[e["target"]] += 1
    for row in module_table:
        row["doc_mentions"] = doc_mentions_count.get(row["id"], 0)

    # Applications: env, runtime, modules used.
    app_uses: dict[str, list] = defaultdict(list)
    for e in edges:
        if e.get("relation") == "uses_module":
            tgt = nodes.get(e["target"], {})
            if tgt.get("label"):
                app_uses[e["source"]].append(tgt["label"])
    app_table = []
    for a in sorted(apps, key=lambda x: (x.get("environment") or "", x.get("label") or "")):
        app_table.append({
            "id": a["id"],
            "env": a.get("environment") or "",
            "name": a.get("label") or "",
            "runtime": a.get("runtime") or "",
            "namespace": a.get("namespace") or "",
            "modules_used": sorted(set(app_uses.get(a["id"], []))),
            "image": a.get("image") or "",
            "cluster": a.get("cluster") or "",
        })

    # Source-layer breakdown (over all nodes).
    layer_counts: dict[str, int] = defaultdict(int)
    for n in nodes.values():
        layer_counts[_node_source_layer(n)] += 1

    # Top edge relations.
    rel_counts: dict[str, int] = defaultdict(int)
    for e in edges:
        rel_counts[e.get("relation") or ""] += 1
    edge_relations = [{"relation": r, "count": c}
                      for r, c in sorted(rel_counts.items(), key=lambda x: (-x[1], x[0]))[:10]]

    # K8s footprint.
    k8s_kinds: dict[str, int] = defaultdict(int)
    for k in k8s_nodes:
        k8s_kinds[k.get("k8s_kind") or "?"] += 1
    irsa_bindings = []
    for e in edges:
        if e.get("relation") != "irsa_bound":
            continue
        sa = nodes.get(e["source"], {})
        role = nodes.get(e["target"], {})
        irsa_bindings.append({
            "sa": sa.get("k8s_name") or sa.get("label") or e["source"],
            "ns": sa.get("k8s_namespace") or "",
            "env": sa.get("environment") or "",
            "role": role.get("label") or e["target"],
        })
    irsa_bindings.sort(key=lambda x: (x["env"], x["ns"], x["sa"]))

    # Doc kinds.
    doc_kinds: dict[str, int] = defaultdict(int)
    for d in docs_nodes:
        doc_kinds[d.get("doc_kind") or "?"] += 1

    si_by_env: dict[str, dict] = {}
    for _nid, sn in nodes.items():
        if sn.get("type") == "shared-infra" and sn.get("environment"):
            si_by_env[sn["environment"]] = sn

    comp_to_mods: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.get("relation") != "configures_module":
            continue
        srcn = nodes.get(e["source"], {})
        if srcn.get("type") != "component":
            continue
        tgtn = nodes.get(e["target"], {})
        lab = tgtn.get("label") or e["target"]
        comp_to_mods[e["source"]].append(lab)

    snap_times = _read_state_overlay_snapshot_times(graph.repo)

    components_table = []
    for nid, cn in sorted(
        nodes.items(),
        key=lambda x: (x[1].get("environment") or "", x[1].get("label") or ""),
    ):
        if cn.get("type") != "component":
            continue
        env_nm = cn.get("environment") or ""
        si = si_by_env.get(env_nm, {})
        components_table.append({
            "id": nid,
            "env": env_nm,
            "name": cn.get("label") or "",
            "modules": sorted(set(comp_to_mods.get(nid, []))),
            "cluster_target": si.get("cluster_name", ""),
            "account": si.get("account_id", ""),
            "region": si.get("region", ""),
            "in_state": bool(cn.get("also_in_state"))
            or cn.get("source") == "state",
            "resource_count": cn.get("resource_count"),
            "state_snapshot_at": snap_times.get(env_nm, ""),
        })

    rollup_map: dict[str, dict] = {}
    for row in app_table:
        name = row["name"]
        bucket = rollup_map.setdefault(
            name,
            {"envs": [], "runtimes": set(), "modules_used": set(), "images": set()},
        )
        bucket["envs"].append(row["env"])
        if row.get("runtime"):
            bucket["runtimes"].add(row["runtime"])
        for m in row.get("modules_used") or []:
            bucket["modules_used"].add(m)
        if row.get("image"):
            bucket["images"].add(row["image"])
    applications_rollup = []
    for name in sorted(rollup_map):
        b = rollup_map[name]
        applications_rollup.append({
            "name": name,
            "envs": sorted(set(b["envs"])),
            "runtimes": sorted(b["runtimes"]),
            "modules_used": sorted(b["modules_used"]),
            "images": sorted(b["images"]),
        })

    repo = graph.repo
    coverage = {
        "openspec_present": (repo / "openspec").is_dir(),
        "openspec_changes": _openspec_change_folder_count(repo),
        "modules_with_doc_mentions": sum(
            1 for row in module_table if row.get("doc_mentions", 0) > 0
        ),
        "modules_total": len(module_table),
        "state_overlay_envs": sorted(snap_times.keys()),
        "docs_overlay": dict(getattr(graph, "_docs_overlay_meta", {}) or {}),
    }

    blast_diagrams: list[dict[str, str]] = []
    if out_dir is not None:
        blast_diagrams = _collect_blast_mermaid_files(out_dir)

    # Runtime breakdown for the apps KPI subline.
    runtime_counts: dict[str, int] = defaultdict(int)
    for a in apps:
        runtime_counts[a.get("runtime") or "—"] += 1
    runtime_sub = ", ".join(f"{k}:{v}" for k, v in sorted(runtime_counts.items()))

    drift_comp_total = sum(len(v) for v in drift.get("components", {}).values())
    drift_app_total = sum(len(v) for v in drift.get("applications", {}).values())
    top_critical = next((c for c in critical if c["in_degree"] > 0), None) or (
        critical[0] if critical else None
    )
    has_state = layer_counts.get("state", 0) > 0
    has_k8s = layer_counts.get("k8s", 0) > 0
    has_docs = layer_counts.get("docs", 0) > 0

    # Terraform state overlay (from state_overlay_*.json + schema-2 resources).
    state_confirmed = sum(1 for c in components if c.get("also_in_state"))
    state_only_comp = sum(
        1 for c in components
        if c.get("source") == "state" and not c.get("also_in_state")
    )
    resource_nodes = [n for n in nodes.values() if n.get("type") == "resource"]
    resource_count = len(resource_nodes)
    rt_counts: dict[str, int] = defaultdict(int)
    for n in resource_nodes:
        rt = n.get("resource_type") or "?"
        rt_counts[rt] += 1
    top_resource_types = [
        {"type": t, "count": c}
        for t, c in sorted(rt_counts.items(), key=lambda x: (-x[1], x[0]))[:15]
    ]
    state_by_env: list[dict] = []
    for env_row in env_data:
        en = env_row["name"]
        comps_e = [c for c in components if c.get("environment") == en]
        state_by_env.append({
            "env": en,
            "snapshot_at": snap_times.get(en, ""),
            "components": len(comps_e),
            "static_confirmed_by_state": sum(1 for c in comps_e if c.get("also_in_state")),
            "state_only_components": sum(
                1 for c in comps_e
                if c.get("source") == "state" and not c.get("also_in_state")
            ),
            "resources": sum(
                1 for n in resource_nodes if n.get("environment") == en
            ),
        })
    state_summary = {
        "loaded": has_state,
        "layer_nodes": layer_counts.get("state", 0),
        "resource_nodes": resource_count,
        "components_state_confirmed": state_confirmed,
        "components_state_only": state_only_comp,
        "snapshot_envs": sorted(snap_times.keys()),
        "by_env": state_by_env,
        "top_resource_types": top_resource_types,
    }

    # v0.35.0: customer-focused KPI rework.
    sec_findings = _compute_security_findings(resource_nodes)
    module_age   = _compute_module_age(graph.repo, snap_times, resource_nodes)
    app_health   = _compute_app_health(graph)
    # State-age headline: youngest snapshot wins; suffix tells how stale.
    state_ages = [r for r in module_age if r.get("age_seconds") is not None]
    if state_ages:
        youngest_age = min(r["age_seconds"] for r in state_ages)
        oldest_age   = max(r["age_seconds"] for r in state_ages)
        def _fmt_age(s):
            s = int(s)
            if s < 90:        return f"{s}s"
            if s < 5400:      return f"{s//60}m"
            if s < 172800:    return f"{s//3600}h"
            return f"{s//86400}d"
        state_age_kpi = {
            "value": _fmt_age(youngest_age),
            "sub": f"oldest module: {_fmt_age(oldest_age)}",
        }
    else:
        state_age_kpi = {"value": "—", "sub": "no state overlay loaded"}
    # Findings headline: total + severity breakdown.
    sf = sec_findings["summary"]
    findings_sub = " · ".join(
        f"{sf[s]} {s}" for s in ("high", "medium", "low") if sf.get(s)
    ) or "all clear"
    # App health: percentage healthy.
    ah = app_health["summary"]
    if ah["total"]:
        pct = round(100.0 * ah["healthy"] / ah["total"])
        app_health_kpi = {"value": f"{ah['healthy']}/{ah['total']}",
                           "sub": f"{pct}% healthy{' · ' + str(ah['unhealthy']) + ' degraded' if ah['unhealthy'] else ''}"}
    else:
        app_health_kpi = {"value": "—", "sub": "no live cluster overlay"}
    # Resources actually deployed.
    resources_kpi = {
        "value": resource_count,
        "sub": f"{len(rt_counts)} types · {len(envs)} env{'s' if len(envs) != 1 else ''}",
    }
    # Cross-env diff (drift) — keep but reframe.
    drift_kpi = {
        "value": drift_comp_total + drift_app_total,
        "sub": (f"{drift_comp_total} component{'s' if drift_comp_total != 1 else ''}, "
                f"{drift_app_total} app{'s' if drift_app_total != 1 else ''}")
               if (drift_comp_total + drift_app_total) else "envs aligned",
    }
    # Apps shipped — only useful when value > 0; keep but trim.
    apps_kpi = {
        "value": len(apps),
        "sub": runtime_sub or ("no application sidecars" if not apps else "—"),
    }
    kpis = {
        "findings":  {"value": sf["total"], "sub": findings_sub},
        "resources": resources_kpi,
        "state_age": state_age_kpi,
        "app_health": app_health_kpi,
        "applications": apps_kpi,
        "drift": drift_kpi,
    }

    return {
        "meta": {
            "version": _read_kuberly_skills_version(graph.repo),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "env_count": len(envs),
            "module_count": len(modules),
            "component_count": len(components),
            "shared_infra_count": len(shared_infras),
            "app_count": len(apps),
            "doc_count": len(docs_nodes),
            "has_state": has_state,
            "has_k8s": has_k8s,
            "has_docs": has_docs,
        },
        "kpis": kpis,
        "environments": env_data,
        "critical_nodes": critical,
        "drift": {
            "components": {k: sorted(v) for k, v in drift.get("components", {}).items()},
            "applications": {k: sorted(v) for k, v in drift.get("applications", {}).items()},
        },
        "providers": providers,
        "modules": module_table,
        "components": components_table,
        "applications": app_table,
        "applications_rollup": applications_rollup,
        "coverage": coverage,
        "blast_diagrams": blast_diagrams,
        "source_layers": dict(layer_counts),
        "edge_relations": edge_relations,
        "k8s": {
            "loaded": has_k8s,
            "kinds": dict(k8s_kinds),
            "namespaces_total": len({k.get("k8s_namespace") for k in k8s_nodes if k.get("k8s_namespace")}),
            "irsa_bindings": irsa_bindings[:500],
        },
        "docs": {
            "loaded": has_docs,
            "kinds": dict(doc_kinds),
            "total": len(docs_nodes),
        },
        "state": state_summary,
        "categories": _compute_categories(resource_nodes),
        "architecture": _compute_architecture(resource_nodes),
        "iam": _compute_iam_view(resource_nodes, edges, nodes),
        # v0.35.0: customer-facing signal blocks.
        "findings":   sec_findings,
        "module_age": module_age,
        "app_health": app_health,
        "app_secret_iam": _compute_app_secret_iam(graph, resource_nodes),
        "network": _compute_network_reachability(resource_nodes),
        "secret_refs":   _scan_secret_references(graph.repo, resource_nodes),
        "cue_schemas":   _scan_cue_schemas(graph.repo),
        "workflows":     _scan_workflow_origins(graph.repo),
        # rendered_apps_<env>.json + app_drift_<env>.json from the manual
        # render_apps.py / diff_apps.py scripts. Auto-loaded if present.
        "rendered_apps": _load_rendered_apps(graph.repo),
        "app_drift":     _load_app_drift(graph.repo),
        "longest_chains": [list(c) for c in stats.get("longest_chains", [])][:5],
    }


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


def write_graph_html(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    """Render `graph.html`: operator dashboard (default) + cytoscape graph tab.

    Blast-radius Mermaid files (`blast_*.mmd`) must exist before this runs —
    `generate` calls `write_mermaid_dag` first so diagrams embed on the dashboard.
    """
    data = graph.to_json()
    cy_nodes, cy_edges = _build_cytoscape_elements(data)
    dash = _compute_dashboard_data(graph, out_dir=out_dir)
    ver = dash["meta"].get("version") or _read_kuberly_skills_version(graph.repo) or "dev"
    html = _GRAPH_HTML_TEMPLATE.substitute(
        NODES_JSON=json.dumps(cy_nodes),
        EDGES_JSON=json.dumps(cy_edges),
        VERSION_CHIP=ver,
    )
    html = html.replace("__DASHBOARD_JSON__", _json_for_inline_script(dash))
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

    def _mermaid_blast_label(text: object, *, max_len: int = 44) -> str:
        """Single-line label safe inside Mermaid ``[\"…\"]``."""
        s = str(text).replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")")
        if len(s) > max_len:
            return s[: max_len - 1] + "…"
        return s

    # --- 3. Blast radius diagram for shared-infra ---
    # Cap nodes: huge downstream sets exceed Mermaid's maxTextSize and make
    # the dashboard unusable; prefer shallow nodes, stable order, one edge each.
    BLAST_MMD_MAX_NODES = 96
    for nid, node in graph.nodes.items():
        if node["type"] != "shared-infra":
            continue
        env = node["environment"]
        br = graph.blast_radius(nid, direction="downstream", max_depth=3)
        if "error" in br:
            continue
        downstream = br.get("downstream", {}) or {}
        ranked = sorted(
            downstream.items(),
            key=lambda kv: (kv[1].get("depth", 99), kv[0]),
        )
        omitted = 0
        if len(ranked) > BLAST_MMD_MAX_NODES:
            omitted = len(ranked) - BLAST_MMD_MAX_NODES
            ranked = ranked[:BLAST_MMD_MAX_NODES]
        ds_ids = {nid, *(dnid for dnid, _ in ranked)}
        depth_of = {nid: 0}
        for dnid, info in ranked:
            depth_of[dnid] = int(info.get("depth", 1))

        blines = ["graph TD"]
        blines.append("    classDef root fill:#F44336,stroke:#B71C1C,color:#fff")
        blines.append("    classDef d1 fill:#FF9800,stroke:#E65100,color:#000")
        blines.append("    classDef d2 fill:#FFC107,stroke:#FF8F00,color:#000")
        blines.append("    classDef d3 fill:#FFEB3B,stroke:#F9A825,color:#000")
        blines.append("")

        root_sid = sanitize(nid)
        blines.append(f'    {root_sid}[["shared-infra ({_mermaid_blast_label(env, max_len=24)})"]]')
        blines.append(f"class {root_sid} root")

        for dnid, info in ranked:
            dsid = sanitize(dnid)
            raw_lbl = graph.nodes.get(dnid, {}).get("label", dnid)
            depth = int(info.get("depth", 1))
            lbl = _mermaid_blast_label(raw_lbl)
            blines.append(f'    {dsid}["{lbl}"]')
            blines.append(f"class {dsid} d{min(depth, 3)}")

        def _best_pred(tgt):
            best = None
            best_d = 9999
            for e in graph.edges:
                if e["target"] != tgt:
                    continue
                src = e["source"]
                if src not in ds_ids:
                    continue
                d_src = depth_of.get(src, 999)
                if d_src < best_d or (d_src == best_d and best is not None and src < best):
                    best_d = d_src
                    best = src
            return best

        edge_seen = set()
        for dnid, _ in ranked:
            if dnid == nid:
                continue
            src = _best_pred(dnid)
            if src is None:
                continue
            a, b = sanitize(src), sanitize(dnid)
            key = (a, b)
            if key in edge_seen:
                continue
            edge_seen.add(key)
            blines.append(f"{a} --> {b}")

        if omitted:
            trunc_sid = sanitize(f"blast_trunc_{env}")
            note = _mermaid_blast_label(f"+{omitted} more (open blast_{env}.mmd or MCP blast_radius)", max_len=60)
            blines.append(f'    {trunc_sid}["{note}"]')
            blines.append(f"class {trunc_sid} d3")
            blines.append(f"{root_sid} -.-> {trunc_sid}")

        br_path = out_dir / f"blast_{env}.mmd"
        br_path.write_text("\n".join(blines) + "\n")
        if verbose:
            print(f"wrote {br_path} (blast radius: {env}, shown={len(ranked)}, omitted={omitted})")


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
        write_mermaid_dag(g, out)
        write_graph_html(g, out)
        write_graph_report(g, out)

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
# MCP Server (stdio — official `mcp` SDK, see kuberly_mcp/)
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
    """Run the kuberly-platform MCP server over stdio (FastMCP on the official `mcp` SDK).

    Requires: ``pip install 'mcp>=1.10'`` in the same Python environment as
    ``python3 kuberly_platform.py mcp``.
    """
    try:
        from kuberly_mcp.stdio_app import run_stdio_server_blocking
    except ImportError as exc:
        sys.stderr.write(
            "kuberly-platform MCP requires the Python 'mcp' package. "
            "Install with: pip install 'mcp>=1.10'\n"
        )
        raise SystemExit(2) from exc
    ver = _read_kuberly_skills_version(graph.repo) or "0.33.0"
    if ver.startswith("v"):
        ver = ver[1:]
    run_stdio_server_blocking(
        graph,
        render_tool_result=render_tool_result,
        emit_telemetry=_emit_telemetry,
        server_version=ver,
    )



if __name__ == "__main__":
    main()
