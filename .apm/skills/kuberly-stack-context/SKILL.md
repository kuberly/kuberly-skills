---
name: kuberly-stack-context
description: Orient agents to the kuberly-stack Terragrunt monorepo (components, clouds/, OpenSpec, plan-only).
---

# Kuberly stack context

Use this skill when the workspace is a **kuberly-stack** fork or upstream clone.

## Invariants

- **OpenTofu** (`tofu`), not Terraform branding in commands.
- **Terragrunt** drives modules under `clouds/{aws,gcp,azure}/modules/`.
- Cluster JSON lives under `components/<cluster>/` (**one `shared-infra.json` per cluster folder**). App JSON lives under `applications/<env>/` — see **`components-vs-applications`**. **GitOps / many-to-many** branch vs targets: **`kuberly-gitops-execution-model`**.
- **OpenSpec** governs meaningful changes under `clouds/`, `components/`, `applications/`, `cue/`, and behavioral `*.hcl` — see `openspec/UPSTREAM_AND_FORKS.md` in the checkout. Per-change **`CHANGELOG.md`** for audit / aggregation — **`openspec-changelog-audit`**.
- Agents **plan only**: do not run `terragrunt apply` / `destroy` unless a human explicitly asked for documented exceptions.

## First reads in the repo

| Goal | Location |
|------|----------|
| Agent rules (generic) | `AGENTS.md` |
| Architecture | `ARCHITECTURE.md`, `README.md` |
| Module authoring | `MODULE_CONVENTIONS.md` |
| Terragrunt + JSON | `INFRASTRUCTURE_CONFIGURATION_GUIDE.md` |

## Graph layers — query before reading files

The kuberly-platform MCP exposes **seven graph layers**. **Prefer querying the graph over scanning HCL / JSON / YAML** — it's faster and the answers are richer.

| Layer | Source | What's in it | Tools |
|---|---|---|---|
| **static** (IaC files) | `clouds/`, `components/`, `applications/` HCL+JSON | Modules, components, applications, terragrunt deps | `query_nodes`, `get_neighbors`, `blast_radius`, `drift` |
| **state** (TG / OpenTofu state) | `.kuberly/state_overlay_<env>.json` (from `state_graph.py --resources`) | Deployed module list + per-resource graph (type, name, depends_on; **no attribute values** — schema 2). Schema 3 adds a per-resource `essentials` whitelist (sizes, versions, IAM trust principals, SG CIDRs) | `query_resources` |
| **k8s** (live cluster) | `.kuberly/k8s_overlay_<env>.json` (from `k8s_graph.py`) | Live-cluster Deployments / Services / SAs / Secrets (key names) / etc. + selector edges + IRSA bridge to state IAM roles | `query_k8s` |
| **docs** | `.kuberly/docs_overlay.json` (from `docs_graph.py`) | Skills, agents, docs, OpenSpec changes — title/description/headings + link/mention edges; optional semantic embeddings | `find_docs` |
| **schema** (CUE) — *v0.36+* | `cue/**/*.cue` walked by the platform builder | One `cue_schema` node per `.cue` file with package + top-level field count | `query_nodes(node_type="cue_schema")` |
| **ci_cd** (workflows) — *v0.36+* | `.github/workflows/*.yml` walked by the platform builder | One `workflow` node per YAML, plus `references` edges to the `module:` / `component:` ids it deploys. Answers "which workflow deploys this module" | `query_nodes(node_type="workflow")` + `get_neighbors` |
| **rendered** (Applications) — *v0.38+* | `.kuberly/rendered_apps_<env>.json` (manual `scripts/render_apps.py`) | Per-app `app_render:<env>/<app>` umbrella + leaf `rendered_resource:<env>/<app>/<Kind>/<name>` for every k8s manifest produced by `cue cmd dump`. Edges: `application → app_render` (`rendered_into`), `app_render → rendered_resource` (`renders`) | `query_nodes(node_type="app_render")` / `query_nodes(node_type="rendered_resource")` |
| **Meta-index** | derived from all of the above | Layer summary, counts, freshness, cross-layer bridges | `graph_index` |

Common answer patterns:
- "what resources does module X manage" → `query_resources(module="X")`
- "what workloads in the cluster" → `query_k8s(kind="Deployment")`
- "which workflow deploys module X" → `get_neighbors("module:aws/X")` and look for inbound `references` from `workflow:...`
- "what does this app render into" → `get_neighbors("app:prod/backend")` and follow `rendered_into` → then `renders`
- "who can reach this RDS" → `get_neighbors("resource:prod/aurora/aws_rds_cluster.X")`, then walk SG rules
- "what cluster workload uses IAM role Y" — walk from the `aws_iam_role` resource through inbound `irsa_bound` edges to ServiceAccount

Sensitive resources (Secrets, ConfigMaps, helm_release values) appear with `redacted: true` — existence is in the graph, values were never extracted.

### Manual-only renderers

Two scripts are NOT auto-run by `kuberly_platform.py` or any pre-commit hook — operator runs them on demand:

- **`scripts/render_apps.py`** — invokes `cue import` + `cue cmd dump` per `applications/<env>/*.json`, writes `.kuberly/rendered_apps_<env>.json`. The next graph regen picks up the file and synthesizes `app_render` + `rendered_resource` nodes (the *rendered* layer above).
- **`scripts/diff_apps.py`** — diffs the rendered manifests against `.kuberly/k8s_overlay_<env>.json`, writes `.kuberly/app_drift_<env>.json` (declared / running / matched / missing / extra per app).

Run from the consumer repo root:
```
python3 apm_modules/kuberly/kuberly-skills/scripts/render_apps.py
python3 apm_modules/kuberly/kuberly-skills/scripts/diff_apps.py
python3 apm_modules/kuberly/kuberly-skills/mcp/kuberly-platform/kuberly_platform.py generate . -o .kuberly
```

## Customer forks

If `AGENTS.md` in the fork mentions maintainer-only paths under `~/.cursor/`, treat those as **workstation-only** — do not paste org ARNs or internal-only URLs into upstream-facing PRs.

Extend this skill with **`references/`** and runtime skills (**`ecs-*`**, **`eks-*`**, …) alongside universal skills under **`.apm/skills/`** — see **`docs/RUNTIME_SKILLS.md`** in the **skills** repo.
