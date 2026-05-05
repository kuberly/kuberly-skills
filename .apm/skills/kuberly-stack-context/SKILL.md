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

The kuberly-platform MCP exposes three graph layers, each backed by the consumer's commit history. **Prefer querying the graph over scanning HCL/JSON** — it's faster and the answers are richer.

| Layer | Source | What's in it | Tools |
|---|---|---|---|
| **Static** | `clouds/`, `components/`, `applications/` HCL+JSON | Modules, components, applications, terragrunt deps | `query_nodes`, `get_neighbors`, `blast_radius`, `drift` |
| **State overlay** | `.kuberly/state_overlay_<env>.json` (from `state_graph.py`) | Deployed module list + per-resource graph (type, name, depends_on; **no values**) — bridges with the static graph via component nodes | `query_resources` |
| **K8s overlay** | `.kuberly/k8s_overlay_<env>.json` (from `k8s_graph.py`) | Live-cluster Deployments / Services / SAs / Secrets (key names) / etc. + selector edges + IRSA bridge to state IAM roles | `query_k8s` |
| **Docs overlay** | `.kuberly/docs_overlay.json` (from `docs_graph.py`) | Skills, agents, docs, OpenSpec changes — title/description/headings + link/mention edges; optional semantic embeddings | `find_docs` |
| **Meta-index** | derived from all of the above | Layer summary, counts, freshness, cross-layer bridges | `graph_index` |

If the answer is "what kind of resources does module X manage", reach for `query_resources(module="X")`. "What workloads in the cluster?" → `query_k8s(kind="Deployment")`. "What does this Service select?" → `get_neighbors(node="k8s:prod/ns/Service/foo")`. The IRSA bridge means you can ask "what cluster workload uses IAM role Y" by walking from the `aws_iam_role` resource node through `irsa_bound` edges.

Sensitive resources (Secrets, ConfigMaps, helm_release values) appear with `redacted: true` — existence is in the graph, values were never extracted.

## Customer forks

If `AGENTS.md` in the fork mentions maintainer-only paths under `~/.cursor/`, treat those as **workstation-only** — do not paste org ARNs or internal-only URLs into upstream-facing PRs.

Extend this skill with **`references/`** and runtime skills (**`ecs-*`**, **`eks-*`**, …) alongside universal skills under **`.apm/skills/`** — see **`docs/RUNTIME_SKILLS.md`** in the **skills** repo.
