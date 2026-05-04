---
name: terragrunt-local-workflow
description: >-
  Run OpenTofu via Terragrunt locally against kuberly-stack: CLUSTER_NAME, KUBERLY_ROLE, jq,
  --iam-assume-role, plan-only defaults for agents, and optional kuberly-cli-customer.bash helper.
---

# Terragrunt locally (kuberly-stack)

## Prereqs

- **OpenTofu** (`tofu`), **Terragrunt**, **AWS CLI v2**, **jq**.
- AWS credentials that may **`sts:AssumeRole`** into **`KUBERLY_ROLE`** from **`shared-infra.json`**.

## Environment (every shell)

```bash
export CLUSTER_NAME=<folder-under-components>
export KUBERLY_ROLE=$(jq -r '.["shared-infra"].target.cluster.role_arn' "components/${CLUSTER_NAME}/shared-infra.json")
export TF_VAR_kuberly_cf_role="$KUBERLY_ROLE"   # needed for some EKS-related modules
# Region (optional but recommended)
export AWS_REGION=$(jq -r '.["shared-infra"].target.region' "components/${CLUSTER_NAME}/shared-infra.json")
```

Sanity: **`aws sts get-caller-identity`**. If assume fails, stop — fix IAM trust / **`sts:AssumeRole`** on **`KUBERLY_ROLE`** with your admin.

## Plan a module (canonical path)

```bash
CLUSTER_NAME="$CLUSTER_NAME" terragrunt run plan \
  --non-interactive \
  --source-update \
  --working-dir './clouds/aws/modules/<module>/' \
  --iam-assume-role "$KUBERLY_ROLE" \
  --dependency-fetch-output-from-state \
  --use-partial-parse-config-cache
```

**Do not** set **`COMPONENT_DIR=components/...`** — use **`CLUSTER_NAME`** only (see **`AGENTS.md`**).

## Optional helper script (customer fork)

From repo root:

```bash
source ./scripts/kuberly-cli-customer.bash
kuberly sso <your-aws-config-profile>
kuberly env
kuberly plan vpc
```

The helper wraps **`terragrunt run … --iam-assume-role "$KUBERLY_ROLE"`** without a maintainer-only controller hop. Full multi-hop **`kuberly-cli.bash`** lives in the **kuberly** monorepo for internal use.

## Agents (Cursor / Claude / Codex)

- **Plan-only** for autonomous agents: **`terragrunt run plan`**, **`validate`**, fmt/lint — no **`apply`** / **`destroy`** unless a human explicitly ordered it. See **`AGENTS.md`** and **`.cursor/rules/terragrunt-plan-only.mdc`** in the checkout.

**Production `apply`:** typically runs on the **Kuberly** side from **GitOps** (per commit / branch), not from the IDE. A branch may map to **many** **`components/<cluster>/`** folders and **many** **`applications/<env>/`** — runtime env vars choose the target for each run — see **`kuberly-gitops-execution-model`**.

## Deeper reference

- **`README.md`**, **`INFRASTRUCTURE_CONFIGURATION_GUIDE.md`** (command tables), **`kuberly-stack`** Cursor skill in upstream **`.cursor/skills/`** (if present in your fork).
