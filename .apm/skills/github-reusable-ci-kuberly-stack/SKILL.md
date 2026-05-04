---
name: github-reusable-ci-kuberly-stack
description: >-
  Add or extend GitHub Actions in an application repo (e.g. backend) by calling reusable workflows shipped in
  kuberly-stack: GitOps flat-env entrypoint, secrets forwarding, OIDC vs static keys, and pointers to the full example.
---

# GitHub reusable CI — kuberly-stack → app repo

Use this skill when an **application repository** (Node/Java/Go **backend**, worker, etc.) needs CI that **builds a container**, **pushes to ECR**, and participates in **GitOps** using **reusable workflows** from **kuberly-stack** (or your org’s **fork / mirror** of that repo — same paths under **`.github/workflows/`**).

## Source of truth in the monorepo

| Workflow | Role |
|----------|------|
| **`reusable-gitops-flat-env.yml`** | **Preferred entry** for app repos: resolves environment / flat GitOps inputs (logic inlined — **no infra git checkout**), then calls the nested build workflow. |
| **`reusable-gitops-build-push-update-infra.yml`** | **Lower-level**: expects **`gitops_environments_json`** already built. Most teams should **not** call this directly from app repos. |

Paths in **kuberly-stack**:

- `.github/workflows/reusable-gitops-flat-env.yml`
- `.github/workflows/reusable-gitops-build-push-update-infra.yml`

**Worked example** (tests + matrix + GitOps job calling the reusable): **`.github/examples/merge-flow.yml`**. Copy patterns from there into **`.github/workflows/<your>.yml`** in the app repo.

## Caller workflow skeleton (`uses:`)

Pin a **tag or branch** on the infra repo that ships the workflow (same ref for **`uses:`** and document **`infra_ref`** input if you set it — comments in the reusable explain alignment).

```yaml
name: Release

on:
  push:
    branches: [main, dev]
  workflow_dispatch:

permissions:
  id-token: write   # OIDC to AWS — required when using role assumption
  contents: read
  pull-requests: read   # optional: deployment / release metadata patterns in the example

jobs:
  gitops-deploy:
    uses: YOUR_ORG/kuberly-stack/.github/workflows/reusable-gitops-flat-env.yml@main
    secrets:
      GITOPS_ENV_OIDC_ARNS_JSON: ${{ secrets.GITOPS_ENV_OIDC_ARNS_JSON }}
      AWS_ROLE_ARN: ${{ secrets.AWS_ROLE_ARN }}
      PROD_AWS_ROLE_ARN: ${{ secrets.PROD_AWS_ROLE_ARN }}
      AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
      # Add the paired static-key secret here (GitHub secret name varies per org — see merge-flow example).
      KUBERLY_WEBHOOK_SECRET: ${{ secrets.KUBERLY_WEBHOOK_SECRET }}
    with:
      app_name: backend
      aws_region: ${{ vars.PROD_AWS_REGION || 'eu-central-1' }}
      gitops_environment: prod
      infra_repo: ${{ vars.GITOPS_INFRA_REPO || 'YOUR_ORG/customer-infra' }}
      infra_ref: main
      gitops_branches: main, dev
      docker_context: .
      runs_on: ubuntu-latest
      image_builder: buildah
      kuberly_webhook_url: ${{ vars.KUBERLY_WEBHOOK_URL }}
      kuberly_build_id: ${{ vars.KUBERLY_BUILD_ID }}
```

Replace **`YOUR_ORG/kuberly-stack`** with the GitHub repo that **hosts** these YAML files (upstream stack, or a long-lived fork). Replace **`infra_repo`** with the repo that holds **`applications/<env>/<app>.json`** if that differs from the stack repo (many customers use a dedicated **infra** repo name).

## Inputs and secrets you must understand

- **`app_name`** (required): must match the application key under **`applications/<env>/`** in the infra repo (see **`components-vs-applications`** and **`applications/README.md`** in the infra checkout).
- **Nested reusables do not see the caller repo’s `vars.*`** for some Kuberly fields — **forward** **`kuberly_webhook_url`**, **`kuberly_build_id`**, **`kuberly_registry_tag_slug`** via **`with:`** when the reusable expects them (see comments in **`reusable-gitops-flat-env.yml`**).
- **OIDC**: **`GITOPS_ENV_OIDC_ARNS_JSON`** maps env slugs to IAM role ARNs; optional **`PROD_` / `DEV_` / … `_AWS_ROLE_ARN`** secrets for built-in slugs. **Static IAM user keys**: forward access key id and the matching **secret key** as separate GitHub Actions secrets — see **`merge-flow.yml`** for **`PROD_*`** naming patterns.
- **`gitops_env_branch_map`**: optional string to map **branches → env** for role selection; omit if you only use static keys (example documents this).
- **`permissions: id-token: write`** when using OIDC.

Do **not** paste real **ARNs**, **webhook URLs**, or **HMAC secrets** into public skills or upstream PRs — configure them as **GitHub Actions secrets / variables** on the **app** repo.

## Agent workflow when authoring CI

1. Read **`.github/examples/merge-flow.yml`** in **kuberly-stack** (or the same path on the fork you pin).
2. In the **app** repo, add **`.github/workflows/…`** with **`jobs.*.uses:`** pointing at the **same** reusable file and **ref**.
3. List **only** secrets the reusable **`secrets:`** block accepts; add **`permissions`** to match OIDC needs.
4. Add **tests / lint** jobs in the **caller** repo (the reusable focuses on **build + ECR + GitOps hooks** — your backend still owns unit tests).
5. Open a PR using **`infra-change-git-pr-workflow`** and **`git-pr-templates`**; if workflow YAML lives only in the app repo, use the **infra fork** or **app** template as appropriate.

## Related skills

- **`components-vs-applications`** — where **`app_name`** and **`applications/<env>/`** fit.
- **`infra-change-git-pr-workflow`**, **`git-pr-templates`** — PR hygiene.
- **`kuberly-stack-context`** — monorepo map; reusable YAML lives under **`.github/workflows/`** in the stack (or fork).

## Extra copy-paste

See **`references/minimal-gitops-caller.yml`** in this skill directory for a shorter commented template.
