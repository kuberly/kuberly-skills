---
name: troubleshooting-aws-observability
description: >-
  Route infra/app incidents to the right AWS signals: ECS vs EKS vs Lambda, using shared-infra.json
  and kuberly-stack module layout; CloudWatch, CloudTrail, and EKS in-cluster observability.
---

# Troubleshooting — AWS observability (kuberly-stack)

## 0. Classify the footprint

1. Run **`detect-runtime-from-shared-infra`** logic on **`components/<CLUSTER>/shared-infra.json`**.
2. Confirm which **Terragrunt modules** are actually in use (repo under **`clouds/aws/modules/`**): `eks`, `ecs` apps, `lambda`, etc.

## 1. ECS-focused (no meaningful EKS)

- **CloudWatch Logs**: log groups for tasks / FireLens / sidecars; filter by **task id** and **time window**.
- **CloudWatch metrics / Container Insights**: CPU, memory, running/pending task count, ALB target health.
- **ECS console**: service **Events** tab (scheduling, image pull, health checks).
- **CloudTrail**: API failures (`RunTask`, `UpdateService`, IAM denied); who changed security groups / target groups.
- **X-Ray** (if enabled on service): trace segments for latency between tasks and dependencies.
- **VPC**: private subnet routing, NAT, security groups referenced by service/task SGs.

## 2. EKS present

Split work between **AWS control plane** and **in-cluster observability**:

- **AWS**: `aws eks describe-cluster`, nodegroup / Fargate issues, IAM (`AccessDenied` on STS), load balancers, **`Describe*` throttling** in CloudTrail.
- **In-cluster**: use **`eks-observability-stack`** for Grafana / Prometheus / Loki / Tempo namespaces, secrets, and UI org configuration.

## 3. Lambda

- **CloudWatch Logs** per function; **Errors / Throttles / Duration** metrics; **DLQ** depth; **X-Ray** traces; **CloudTrail** for `lambda:UpdateFunctionCode` and IAM changes.

## 4. Cross-cutting

- **CloudTrail** org or account trail for “who changed IAM / SG / route / WAF”. For **CLI: last ~1 hour across all regions**, use **`cloudtrail-last-hour-all-regions`** (or Athena / Lake for org-wide S3 trails).
- **VPC Flow Logs** grouped by **source / destination**: **`vpc-flow-logs-source-destination-grouping`** (Insights, Athena, or exports).
- **GuardDuty / Security Hub** (if enabled) for suspicious API patterns — link only when customer has it on.
- **KMS / Secrets Manager** decrypt failures often show as application errors — correlate timestamp with CloudTrail **`Decrypt`**.

## 5. Future: MCP wrapper

Your team’s **MCP** layer should eventually hide host-specific URLs and unify queries. Until it ships to this customer, apply this skill’s routing manually and keep commands copy-pasteable from **`terragrunt-local-workflow`** + AWS CLI docs.
