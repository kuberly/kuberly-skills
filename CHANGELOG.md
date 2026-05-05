# Changelog

## v0.23.0 — 2026-05-05

- **NEW:** cytoscape.js compound-node graph viz replaces vis.js force-graph
  in `.claude/graph.html`.
  - Color-coded by source layer (static / state / k8s / docs)
  - Collapsible compound nesting by env -> namespace
  - Layer toggles, fuzzy search, layout switcher (fcose / dagre /
    concentric)
  - Click-to-sidebar with node details + edges + blast-radius highlight
  - k8s layer OFF by default (cuts initial render from 864 to ~100
    nodes)

## v0.22.0 — 2026-05-05

- **BREAKING:** persona rename — `iac-developer` → `agent-infra-ops`,
  `infra-scope-planner` → `agent-planner`, `troubleshooter` → `agent-sre`,
  `app-cicd-engineer` → `agent-cicd`. Skill rename: `infra-orchestrator` →
  `agent-orchestrator`. Consumer repos must update any hardcoded
  `subagent_type` strings or persona references after `apm install`.
- **NEW:** `agent-k8s-ops` persona — read-only live-cluster Kubernetes
  operator (distinct from `agent-sre`). Reports on running workloads, helm
  releases, ServiceAccount-to-IAM-role wiring via the k8s overlay graph and
  IRSA bindings. Writes `k8s-state.md`. Added to the `incident` DAG's
  `diagnose` phase alongside `agent-sre` and `agent-planner`.
- **FIX:** graph indexer false-positive — modules deployed directly via
  `terragrunt apply` (with state in `state_overlay.deployed_modules` but
  no `components/<env>/<x>.json` invoker) are no longer reported as
  `stop-no-instance`. The actionability predicate in `quick_scope` and
  `plan_persona_fanout` now recognizes `source="state"` component nodes
  even when `link_components_to_modules` cannot label-match them to the
  module.
