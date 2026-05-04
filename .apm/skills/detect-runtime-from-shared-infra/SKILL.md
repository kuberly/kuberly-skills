---
name: detect-runtime-from-shared-infra
description: >-
  Infer whether a kuberly-stack cluster is EKS-heavy, ECS-focused, or Lambda-oriented from
  components/<CLUSTER>/shared-infra.json and sibling JSON — before choosing troubleshooting skills.
---

# Detect runtime from `shared-infra.json`

Always start from **`components/<CLUSTER_NAME>/shared-infra.json`** (or your fork’s equivalent path). **`CLUSTER_NAME`** is the folder name under **`components/`**.

## Quick signals

| Signal | Meaning |
|--------|---------|
| **`shared-infra.target.eks`** is an object with **non-empty** keys (for example `capacity_type`, `topology`, `additional_access`) | EKS is part of this stack — use Kubernetes + AWS control-plane tooling for many issues. |
| **`shared-infra.target.eks`** is **`{}`** or only placeholders | EKS block present but may be unused; cross-check **`eks.json`** / applied modules under **`clouds/aws/modules/`**. |
| **`shared-infra.target.ecs`** non-empty or **`ecs.json`** exists | ECS workloads — prioritize **CloudWatch Logs**, **ECS service events**, **Service Connect / ALB**, **X-Ray** if enabled. |
| **`shared-infra.target.lambda`** | Serverless — **CloudWatch Logs**, **X-Ray**, **Lambda metrics/throttles**, **DLQs**. |
| **`shared-infra.labels["kuberly.io/org_id"]`** | **Tenant/org UUID** in Kuberly metadata — **not** the Grafana stack “org id” (see EKS observability skill). |

## jq recipes (read-only)

```bash
CLUSTER_NAME=<name>
F="components/${CLUSTER_NAME}/shared-infra.json"

# Terragrunt state role (always useful)
jq -r '.["shared-infra"].target.cluster.role_arn' "$F"

# Region
jq -r '.["shared-infra"].target.region' "$F"

# Inspect EKS / ECS blocks (empty `{}` vs populated objects)
jq '.["shared-infra"].target.eks' "$F"
jq '.["shared-infra"].target.ecs' "$F"
```

If **EKS** is active, continue with **`eks-observability-stack`** + **`troubleshooting-aws-observability`**. If **ECS-only**, emphasize **`ecs-observability-troubleshooting`**. When in doubt, **ask the human** which surfaces they use in prod.
