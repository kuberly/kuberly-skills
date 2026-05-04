---
name: kuberly-gitops-execution-model
description: >-
  How Kuberly runs Terragrunt plan/apply from Git (GitOps): one branch can touch many clusters under
  components/ and many application envs; shared-infra.json per cluster; runtime env vars pick the deploy target.
---

# Kuberly GitOps — Terragrunt execution vs repo layout

Use this skill when agents or humans need to reason about **where** **`terragrunt plan` / `apply` actually run** (on the **Kuberly** side from **GitOps**), versus **what** a single **git branch** can contain in the infra repo.

## Who runs apply?

- **Kuberly platform** drives **Terragrunt `plan` and `apply`** from **git** (commit on a branch, webhooks / pipelines, org automation) — **not** “every developer runs `apply` from a laptop” as the default production path.
- **Local / CI preview:** developers and agents still use **`terragrunt run plan`** (and validate / fmt) for feedback — see **`terragrunt-local-workflow`** and **`AGENTS.md`** (**plan-only** for autonomous agents).

Treat **Git** as the contract: merged or integrated commits are what the platform eventually reconciles.

## Repo layout (two dimensions)

| Dimension | Path pattern | Role |
|-----------|--------------|------|
| **Cluster / platform** | **`components/<cluster>/`** | One **logical cluster** (or stack slice) per folder name — e.g. **`dev`**, **`prod`**, **`stage`**, customer-specific names. |
| **Cluster anchor file** | **`components/<cluster>/shared-infra.json`** | **Exactly one per cluster folder** — holds **`KUBERLY_ROLE`**, region, org labels, and other **shared** metadata for that cluster. |
| **Application workloads** | **`applications/<env>/`** | **Per-environment** app definitions; **many JSON files** per env (one app per file). |

This is **orthogonal** to git branching:

- **`components/*`** answers “**which AWS / K8s platform** are we configuring?”
- **`applications/*`** answers “**which app image / service config** for which **env slug**?”

See **`components-vs-applications`** for edit routing and JSON shapes.

## Many-to-many (the important part)

A **single git branch** is **not** limited to one cluster or one application env. The same branch may:

- Contain updates under **several** **`components/<cluster>/`** directories (multiple clusters in one MR), and
- Contain updates under **several** **`applications/<env>/`** trees (multiple env slugs, many apps).

**Kuberly** (or your wrapper) then chooses **what to run** for a given pipeline execution using **environment variables** (and/or job matrix), for example:

- **`CLUSTER_NAME`** (or equivalent) → selects **`components/<CLUSTER_NAME>/`** and the right **`shared-infra.json`** / **`KUBERLY_ROLE`**.
- **`APPLICATION_DIR`** + **`APPLICATION_NAME`** (for **`ecs_app`**, **`lambda_app`**, …) → selects **`applications/<dir>/<name>.json`**.
- Other flags your org uses for **module working dir**, **feature flags**, or **dry-run**.

So: **branch = association with “what changed in git”**; **runtime env = association with “which Terragrunt target this run applies to”**. One commit can therefore trigger **multiple** Terragrunt runs (matrix or sequential jobs), each with a **different** env combination.

## Implications for agents and PRs

1. **Blast radius:** a PR might affect **more than one** cluster and **more than one** app env — state this explicitly in **OpenSpec** **`CHANGELOG.md`** (**`## Customer impact`**) and the PR body (**`infra-change-git-pr-workflow`**).
2. **Do not assume** “branch name === `CLUSTER_NAME`” or “one env per branch” unless your **fork’s** automation document says so.
3. **Local plan:** pick **one** **`CLUSTER_NAME`** (and app selectors if planning an app module) at a time; repeat for other targets if you need confidence across the MR.

## Related skills

- **`components-vs-applications`** — platform vs app trees.
- **`terragrunt-local-workflow`**, **`kuberly-cli-customer`** — local **`CLUSTER_NAME`** / **`KUBERLY_ROLE`** / plan commands.
- **`github-reusable-ci-kuberly-stack`** — app-repo CI that bumps images toward **`applications/`** (GitOps side).
- **`openspec-changelog-audit`** — audit trail when many targets change together.
