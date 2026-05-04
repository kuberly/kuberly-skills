#!/usr/bin/env python3
"""
kuberly-platform: Knowledge graph generator for kuberly-stack Terragrunt/OpenTofu monorepos.

Parses components, applications, modules, and their dependencies to produce:
  - graph.json  — queryable graph structure
  - graph.html  — interactive vis.js visualization
  - GRAPH_REPORT.md — summary with critical nodes, dependency chains, and cross-env drift
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


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
    "infra-scope-planner",
    "iac-developer",
    "troubleshooter",
    "app-cicd-engineer",
    "pr-reviewer-in-context",
    "pr-reviewer-cold",
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
_REVIEW_RECONCILE = [
    {"id": "review",    "personas": ["pr-reviewer-in-context", "pr-reviewer-cold"],
     "parallel": True,  "needs_approval": False},
    {"id": "reconcile", "personas": ["findings-reconciler"],
     "parallel": False, "needs_approval": False},
]

PERSONA_DAGS = {
    "resource-bump": [
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "incident": [
        # Diagnose + scope in parallel: troubleshooter looks at observability,
        # planner pins the codebase scope. Both feed decisions.md.
        {"id": "diagnose",  "personas": ["troubleshooter", "infra-scope-planner"],
         "parallel": True,  "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "new-application": [
        # Adding a new application is a CUE / applications/ JSON change
        # against existing cluster modules. Same shape as new-module but
        # the implementer touches applications/, not clouds/.
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "new-database": [
        # New DB is usually a new components/<env>/<db>.json + module reference.
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "new-module": [
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "drift-fix": [
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "cicd": [
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["app-cicd-engineer"],   "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
    ],
    "cleanup": [
        {"id": "scope",     "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
        {"id": "implement", "personas": ["iac-developer"],       "parallel": False, "needs_approval": True},
        *_REVIEW_RECONCILE,
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
        {"id": "scope", "personas": ["infra-scope-planner"], "parallel": False, "needs_approval": False},
    ],
    # Pre-flight halt: caller named modules that don't exist as graph nodes.
    "stop-target-absent": [
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
    """Extract component JSON references like include.root.locals.components.X"""
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
    return list(set(refs))


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
                if data:
                    dep = data.get("deployment", {})
                    meta["port"] = dep.get("port")
                    meta["replicas"] = dep.get("replicas")
                    container = dep.get("container", {})
                    img = container.get("image", {})
                    meta["image"] = img.get("repository", "")
                    # count secrets
                    env_block = container.get("env", {})
                    meta["secret_count"] = len(env_block.get("secrets", []))
                    meta["env_var_count"] = len(env_block.get("env_vars", {}))

                self.add_node(nid, type="application", label=app_name,
                              environment=env_name, **meta)
                self.add_edge(env_nid, nid, relation="deploys")

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

                # Read kuberly.json metadata if present
                meta = {}
                kj = mod_dir / "kuberly.json"
                if kj.exists():
                    kdata = load_json_safe(kj)
                    if kdata:
                        meta["description"] = kdata.get("description", "")
                        meta["version"] = kdata.get("version", "")
                        meta["types"] = kdata.get("types", [])
                        meta["author"] = kdata.get("author", "")

                self.add_node(nid, type="module", label=mod_name,
                              provider=provider, path=str(mod_dir.relative_to(self.repo)), **meta)
                self.add_edge(provider_nid, nid, relation="provides")

                # Parse terragrunt.hcl for dependencies
                tg = mod_dir / "terragrunt.hcl"
                if tg.exists():
                    for dep in parse_hcl_dependencies(tg):
                        dep_nid = f"module:{provider}/{dep}"
                        self.add_edge(nid, dep_nid, relation="depends_on")

                    # Parse component JSON references
                    for comp_ref in parse_hcl_component_refs(tg):
                        self.add_edge(nid, f"component_type:{comp_ref}",
                                      relation="reads_config")

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
        module_names = {n["label"] for n in self.nodes.values() if n["type"] == "module"}
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
                            task_kind:     str | None = None) -> dict:
        """One-shot orchestration plan: classify task, build scope slice, run gates,
        emit persona DAG with parallelism markers, and produce a ready-to-paste
        context.md body."""
        if task_kind:
            confidence = "high"  # caller-overridden
        else:
            task_kind, confidence = self.infer_task_kind(task)

        scope = self.scope_for_change(named_modules, target_envs)
        gates = self.gate_check(named_modules, current_branch, task)

        # Existence pre-flight. If the caller named modules but NONE of them
        # resolve to a graph node, override the DAG to a no-persona halt so
        # the orchestrator can't fan out personas that would just re-discover
        # the absence. This is the v0.10.2 root-cause guard for the "Loki
        # not deployed but planner+troubleshooter both spawned" pattern.
        unresolved_modules: list[str] = []
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

        recommended = self.recommend_personas(task_kind)

        slug = _slugify(session_name or task)
        context_md = self._build_context_md(
            session=slug, task=task, scope=scope, gates=gates,
            unresolved_modules=unresolved_modules,
        )

        return {
            "task_kind":  task_kind,
            "confidence": confidence,
            "scope":      scope,
            "gates":      gates,
            "phases":     recommended["phases"],
            "session_slug": slug,
            "context_md":  context_md,
            "unresolved_modules": unresolved_modules,
        }

    def _build_context_md(self, session: str, task: str,
                          scope: dict, gates: dict,
                          unresolved_modules: list[str] | None = None) -> str:
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
                f"kuberly-platform. The fanout DAG was overridden to "
                f"`stop-target-absent` (zero personas). Confirm with the user "
                f"before any persona dispatch — likely the target is not "
                f"deployed in this fork, the user means a different name, or "
                f"the graph is stale.\n"
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
                            f"delegating to `iac-developer`.\n")

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

        target: persona name (e.g. 'iac-developer') or phase id (e.g. 'implement')
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
        self.scan_catalog()
        self.link_components_to_modules()

    # -- export --
    def to_json(self) -> dict:
        return {
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
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


def write_graph_html(graph: KuberlyPlatform, out_dir: Path, *, verbose: bool = False):
    data = graph.to_json()

    # Color scheme by node type
    type_colors = {
        "environment": "#4CAF50",
        "shared-infra": "#FF9800",
        "component": "#2196F3",
        "application": "#9C27B0",
        "module": "#607D8B",
        "cloud_provider": "#795548",
    }
    type_shapes = {
        "environment": "diamond",
        "shared-infra": "star",
        "component": "dot",
        "application": "square",
        "module": "triangle",
        "cloud_provider": "hexagon",
    }
    edge_colors = {
        "depends_on": "#F44336",
        "contains": "#4CAF50",
        "configures": "#FF9800",
        "configures_module": "#2196F3",
        "deploys": "#9C27B0",
        "provides": "#795548",
        "reads_config": "#00BCD4",
    }

    vis_nodes = []
    for n in data["nodes"]:
        ntype = n.get("type", "")
        title_parts = [f"<b>{n['id']}</b>", f"Type: {ntype}"]
        for k, v in n.items():
            if k not in ("id", "type", "label") and v:
                title_parts.append(f"{k}: {v}")
        vis_nodes.append({
            "id": n["id"],
            "label": n.get("label", n["id"]),
            "color": type_colors.get(ntype, "#999"),
            "shape": type_shapes.get(ntype, "dot"),
            "title": "<br>".join(title_parts),
            "group": ntype,
            "size": 20 if ntype in ("environment", "shared-infra", "cloud_provider") else 12,
        })

    vis_edges = []
    for e in data["edges"]:
        rel = e.get("relation", "")
        vis_edges.append({
            "from": e["source"],
            "to": e["target"],
            "label": rel,
            "color": {"color": edge_colors.get(rel, "#999"), "opacity": 0.7},
            "arrows": "to",
            "font": {"size": 8, "color": "#666"},
            "smooth": {"type": "cubicBezier"},
        })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>kuberly-stack Knowledge Graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; }}
  #controls {{ padding: 12px 16px; background: #16213e; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  #controls label {{ font-size: 13px; }}
  #controls select, #controls input {{ background: #0f3460; color: #eee; border: 1px solid #444; padding: 4px 8px; border-radius: 4px; font-size: 13px; }}
  #controls button {{ background: #e94560; color: #fff; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }}
  #controls button:hover {{ background: #c73e54; }}
  #graph {{ width: 100vw; height: calc(100vh - 100px); }}
  #legend {{ display: flex; gap: 16px; padding: 8px 16px; background: #16213e; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 12px; }}
  .legend-dot {{ width: 12px; height: 12px; border-radius: 50%; }}
  #info {{ position: fixed; bottom: 16px; right: 16px; background: #16213e; padding: 12px; border-radius: 8px; max-width: 350px; font-size: 12px; display: none; border: 1px solid #333; max-height: 50vh; overflow-y: auto; }}
</style>
</head>
<body>

<div id="controls">
  <label>Filter type:
    <select id="typeFilter">
      <option value="all">All</option>
      {"".join(f'<option value="{t}">{t}</option>' for t in type_colors)}
    </select>
  </label>
  <label>Filter env:
    <select id="envFilter">
      <option value="all">All</option>
    </select>
  </label>
  <label>Search: <input id="search" placeholder="node name..." /></label>
  <button onclick="resetView()">Reset</button>
  <span style="margin-left:auto;font-size:12px;opacity:0.6">{len(data['nodes'])} nodes &middot; {len(data['edges'])} edges</span>
</div>

<div id="legend">
  {"".join(f'<div class="legend-item"><div class="legend-dot" style="background:{c}"></div>{t}</div>' for t, c in type_colors.items())}
</div>

<div id="graph"></div>
<div id="info"></div>

<script>
const allNodes = {json.dumps(vis_nodes)};
const allEdges = {json.dumps(vis_edges)};

const nodes = new vis.DataSet(allNodes);
const edges = new vis.DataSet(allEdges);

const container = document.getElementById('graph');
const network = new vis.Network(container, {{ nodes, edges }}, {{
  physics: {{
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{ gravitationalConstant: -80, centralGravity: 0.01, springLength: 120 }},
    stabilization: {{ enabled: true, iterations: 600, fit: true, updateInterval: 25 }},
  }},
  interaction: {{ hover: true, tooltipDelay: 100, navigationButtons: true, dragNodes: true }},
  layout: {{ improvedLayout: true }},
}});

// Freeze the layout once initial stabilization is done so the graph stops moving.
network.once('stabilizationIterationsDone', () => {{
  network.setOptions({{ physics: {{ enabled: false }} }});
}});

// Populate env filter
const envs = new Set();
allNodes.forEach(n => {{ if (n.group === 'environment') envs.add(n.label); }});
const envSel = document.getElementById('envFilter');
envs.forEach(e => {{ const o = document.createElement('option'); o.value = e; o.text = e; envSel.add(o); }});

function applyFilters() {{
  const typeVal = document.getElementById('typeFilter').value;
  const envVal = document.getElementById('envFilter').value;
  const searchVal = document.getElementById('search').value.toLowerCase();

  const visibleIds = new Set();
  allNodes.forEach(n => {{
    let show = true;
    if (typeVal !== 'all' && n.group !== typeVal) show = false;
    if (envVal !== 'all') {{
      // Show node if it belongs to this env or is a module/provider (always show)
      const isGlobal = ['module', 'cloud_provider'].includes(n.group);
      const matchesEnv = n.id.includes(envVal + '/') || n.id === 'env:' + envVal;
      if (!isGlobal && !matchesEnv) show = false;
    }}
    if (searchVal && !n.label.toLowerCase().includes(searchVal) && !n.id.toLowerCase().includes(searchVal)) show = false;
    if (show) visibleIds.add(n.id);
  }});

  nodes.update(allNodes.map(n => ({{ id: n.id, hidden: !visibleIds.has(n.id) }})));
  edges.update(allEdges.map((e, i) => ({{ id: i, hidden: !visibleIds.has(e.from) || !visibleIds.has(e.to) }})));
}}

document.getElementById('typeFilter').onchange = applyFilters;
document.getElementById('envFilter').onchange = applyFilters;
document.getElementById('search').oninput = applyFilters;

function resetView() {{
  document.getElementById('typeFilter').value = 'all';
  document.getElementById('envFilter').value = 'all';
  document.getElementById('search').value = '';
  applyFilters();
  network.fit();
}}

// Click info panel
network.on('click', params => {{
  const info = document.getElementById('info');
  if (params.nodes.length > 0) {{
    const nid = params.nodes[0];
    const node = allNodes.find(n => n.id === nid);
    const incoming = allEdges.filter(e => e.to === nid);
    const outgoing = allEdges.filter(e => e.from === nid);
    info.style.display = 'block';
    info.innerHTML = '<b>' + node.id + '</b><br>Type: ' + node.group +
      '<br><br><b>Incoming (' + incoming.length + '):</b><br>' +
      incoming.map(e => '  ' + e.from + ' [' + e.label + ']').join('<br>') +
      '<br><br><b>Outgoing (' + outgoing.length + '):</b><br>' +
      outgoing.map(e => '  ' + e.to + ' [' + e.label + ']').join('<br>');
  }} else {{
    info.style.display = 'none';
  }}
}});
</script>
</body>
</html>"""

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
    path.write_text("\n".join(lines))
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
        env_path.write_text("\n".join(elines))
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
        br_path.write_text("\n".join(blines))
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
    """Load and build a graph from a repo path."""
    repo = Path(repo_path).resolve()
    if not (repo / "root.hcl").exists():
        print(f"Error: {repo} does not look like a kuberly-stack repo (no root.hcl)")
        sys.exit(1)
    g = KuberlyPlatform(str(repo))
    g.build()
    return g


def main():
    parser = argparse.ArgumentParser(
        description="kuberly-platform: knowledge graph for kuberly-stack")
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # --- generate ---
    gen = sub.add_parser("generate", help="Generate all graph outputs (default)")
    gen.add_argument("repo", nargs="?", default=".",
                     help="Path to kuberly-stack repo root")
    gen.add_argument("-o", "--output", default=".",
                     help="Output directory")

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

    args = parser.parse_args()

    # Default to generate if no subcommand
    if args.command is None or args.command == "generate":
        repo_path = getattr(args, "repo", ".") or "."
        out = Path(getattr(args, "output", ".") or ".").resolve()
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

        print(
            f"kuberly-platform: nodes={len(g.nodes)} edges={len(g.edges)} "
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
        run_mcp_server(load_graph(args.repo))


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
    if isinstance(result, dict) and "error" in result:
        return f"err {name}: {result['error']}"
    if name == "query_nodes":
        return f" query_nodes: {len(result)} matches"
    if name in ("get_node", "get_neighbors"):
        return (f" {name}: `{result.get('node','?')}` "
                f"· in={len(result.get('incoming',[]))} "
                f"· out={len(result.get('outgoing',[]))}")
    if name == "blast_radius":
        return (f" blast_radius `{result.get('node','?')}`: "
                f"down={result.get('downstream_count',0)} "
                f"up={result.get('upstream_count',0)}")
    if name == "shortest_path":
        return f" shortest_path: length {result.get('length','?')}"
    if name == "drift":
        comps = len(result.get("components", {}))
        apps = len(result.get("applications", {}))
        return f" drift: components in {comps} env(s), apps in {apps} env(s)"
    if name == "stats":
        return (f" stats: {result.get('node_count',0)} nodes "
                f"· {result.get('edge_count',0)} edges "
                f"· {len(result.get('critical_nodes',[]))} critical")
    if name == "plan_persona_fanout":
        return (f" plan: kind=`{result.get('task_kind','?')}` "
                f"({result.get('confidence','?')}) "
                f"· {len(result.get('phases',[]))} phases")
    if name == "session_init":
        return (f" session `{result.get('session_slug','?')}` created "
                f"({result.get('task_kind','?')})")
    if name == "session_status":
        if result.get("_no_status_yet"):
            return f" session `{result.get('session','?')}`: no fanout"
        ps = result.get("phases", [])
        done = sum(1 for p in ps if p.get("status") == "done")
        return f" session `{result.get('session','?')}`: {done}/{len(ps)} phases done"
    if name == "session_set_status":
        return f"ok {result.get('kind','?')} `{result.get('target','?')}`: {result.get('status','?')}"
    if name == "session_read":
        return f" read `{result.get('file','?')}`: {result.get('bytes',0)} B"
    if name == "session_write":
        return f"ok wrote `{result.get('file','?')}`: {result.get('bytes',0)} B"
    if name == "session_list":
        return f" session `{result.get('session','?')}`: {len(result.get('files',[]))} files"
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

def run_mcp_server(graph: KuberlyPlatform):
    """Run an MCP server over stdio that exposes graph query tools."""
    import select as _select

    # All tools accept an optional `format` arg: "card" (default — rendered
    # Markdown with status badges and tables), "json" (raw), or "compact"
    # (one-line summary for chained calls).
    _FORMAT_PROP = {
        "type": "string",
        "enum": ["card", "json", "compact"],
        "default": "card",
        "description": "Output format: card (default — rich Markdown with emoji status badges), json (raw JSON), or compact (one-line summary).",
    }

    TOOLS = [
        {
            "name": "query_nodes",
            "description": "Filter graph nodes by type (environment, component, shared-infra, application, module, cloud_provider), environment name, and/or name substring.",
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
            "description": "Show cross-environment drift: components and applications that exist in some environments but not others.",
            "inputSchema": {
                "type": "object",
                "properties": {"format": _FORMAT_PROP},
            },
        },
        {
            "name": "stats",
            "description": "Get graph statistics: node/edge counts, critical nodes (most depended upon), and longest dependency chains.",
            "inputSchema": {
                "type": "object",
                "properties": {"format": _FORMAT_PROP},
            },
        },
        {
            "name": "plan_persona_fanout",
            "description": "Orchestration plan for a kuberly-stack infra task. Classifies task_kind, computes blast-radius/drift scope, runs branch + OpenSpec + personas-synced gates, returns a persona DAG (with per-phase parallel/needs_approval flags) and a ready-to-paste context.md body. Call this first in infra-orchestrator mode; then use session_init to materialize a session dir.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task":           {"type": "string", "description": "Free-form task description from the user."},
                    "named_modules":  {"type": "array", "items": {"type": "string"}, "description": "Optional: module names hinted by the user (e.g. ['loki'])."},
                    "target_envs":    {"type": "array", "items": {"type": "string"}, "description": "Optional: target environments. Drift slice is computed only when set."},
                    "current_branch": {"type": "string", "description": "Result of `git rev-parse --abbrev-ref HEAD` — enables the branch gate."},
                    "session_name":   {"type": "string", "description": "Optional override for the session slug; defaults to slugified task."},
                    "task_kind":      {"type": "string", "enum": ["resource-bump", "incident", "new-application", "new-database", "new-module", "drift-fix", "cicd", "cleanup", "plan-review", "unknown", "stop-target-absent"], "description": "Override task_kind inference. Note: `stop-target-absent` is normally set automatically when `named_modules` are supplied but none resolve to graph nodes — the orchestrator should not pass this value, the planner emits it."},
                    "format":         _FORMAT_PROP,
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
            "description": "Mutate status.json: mark a persona or phase as queued/running/done/blocked/skipped. Auto-detects whether `target` is a persona or phase id; phase status auto-rolls-up from its personas. Call this immediately before launching an Agent() (status='running') and immediately after it returns (status='done' or 'blocked').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name":   {"type": "string", "description": "Session name."},
                    "target": {"type": "string", "description": "Persona name (e.g. 'iac-developer') or phase id (e.g. 'implement')."},
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
            fmt = tool_args.get("format", "card")
            try:
                result = dispatch_tool(graph, tool_name, tool_args)
                text = render_tool_result(tool_name, result, tool_args, graph, fmt=fmt)
                return {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                    },
                }
            except Exception as exc:
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
