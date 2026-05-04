---
name: kuberly-cli-customer
description: >-
  Use scripts/kuberly-cli-customer.bash in kuberly-stack: SSO, env from shared-infra, terragrunt plan
  with --iam-assume-role — single-hop for customer IAM without maintainer controller roles.
---

# kuberly-cli-customer (kuberly-stack)

## Purpose

**`scripts/kuberly-cli-customer.bash`** is a **small** bash helper for **customer** clones:

- **`kuberly sso <profile>`** — runs **`aws sso login`**
- **`kuberly env`** — exports **`CLUSTER_NAME`**, **`KUBERLY_ROLE`**, **`AWS_REGION`**, **`TF_VAR_kuberly_cf_role`** from **`shared-infra.json`**
- **`kuberly plan|validate|init|apply <module>`** — runs **`terragrunt run … --iam-assume-role "$KUBERLY_ROLE"`**

It **does not** embed Kuberly maintainer **controller** role ARNs. Your human IAM principal must already be allowed to assume **`KUBERLY_ROLE`** (or you use another supported pattern your org documents).

## Usage

```bash
cd /path/to/kuberly-stack-fork
source ./scripts/kuberly-cli-customer.bash
kuberly sso my-company-sso
kuberly env
kuberly plan vpc
```

Optional repo file **`.kuberly-cli.env`**:

```bash
KUBERLY_AWS_PROFILE=my-company-sso
```

## vs full `kuberly-cli.bash`

The full **two-step** script (**`repos/kuberly/scripts/kuberly-cli.bash`**) is for **Kuberly maintainers** (SSO → controller role → **`KUBERLY_ROLE`**). Customers on a **single-hop** trust model should prefer **`kuberly-cli-customer.bash`** to reduce confusion and leaked internal ARNs.

## Agents

Treat **`kuberly apply`** like any **`terragrunt apply`**: **humans / CI only**, not autonomous coding agents unless the user explicitly overrides policy.
