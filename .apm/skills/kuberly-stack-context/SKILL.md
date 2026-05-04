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

## Customer forks

If `AGENTS.md` in the fork mentions maintainer-only paths under `~/.cursor/`, treat those as **workstation-only** — do not paste org ARNs or internal-only URLs into upstream-facing PRs.

Extend this skill with **`references/`** and runtime skills (**`ecs-*`**, **`eks-*`**, …) alongside universal skills under **`.apm/skills/`** — see **`docs/RUNTIME_SKILLS.md`** in the **skills** repo.
