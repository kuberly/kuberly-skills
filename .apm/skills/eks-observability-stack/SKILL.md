---
name: eks-observability-stack
description: >-
  Where Grafana, Prometheus, Loki, and Tempo typically live on Kuberly EKS stacks, how to reach
  credentials (secrets), and how tenant labels differ from Grafana org id.
---

# EKS — in-cluster observability (typical Kuberly layout)

**Convention (AWS stacks described in kuberly-stack docs):** modules such as **`grafana`**, **`prometheus`**, **`loki`**, **`tempo`** deploy into Kubernetes namespaces that match Helm / module defaults — commonly:

| Workload | Typical namespace | Notes |
|----------|-------------------|--------|
| **Grafana** | **`grafana`** (standalone Grafana module) *or* UI from **`kube-prometheus-stack`** depending on how the cluster was composed | Confirm with `kubectl get ns` and Helm releases in the live cluster. |
| **Prometheus / kube-state-metrics / alertmanager** | Often **`monitoring`** when Prometheus is installed as its own namespace per **INFRASTRUCTURE_CONFIGURATION_GUIDE** ordering | Some layouts co-locate Grafana here — **verify**, do not assume blindly. |
| **Loki** | **`loki`** | Log aggregation; check `kubectl -n loki get pods,svc`. |
| **Tempo** | **`tempo`** | Trace backend; correlate with Grafana datasources. |

## Credentials

- **Helm / operator secrets** (admin passwords, object storage credentials) usually live in the **same namespace** as the workload or in **`external-secrets`** / **`secrets`** patterns — list with `kubectl get secret -n <ns>`.
- **Never** paste secret values into tickets or agent logs — reference **secret name + key** only.

## “Org id” vs Kuberly labels

- **`shared-infra.json`** carries **`shared-infra.labels["kuberly.io/org_id"]`** — this is a **Kuberly tenant UUID** for platform metadata, **not** automatically the numeric **Grafana organization ID** inside Grafana’s database.
- Grafana’s default org is often **ID 1** for the first org, but **multi-org** setups differ. Have the user check **Grafana → Administration → Organizations** (or your SSO mapping) in the running instance.

## kubectl access

- User must have an **AWS identity** that can **`sts:AssumeRole`** into the **EKS cluster access** role (or use **`aws eks update-kubeconfig`** with the right profile). This is **separate** from **`KUBERLY_ROLE`** used for **Terragrunt** state — do not confuse the two in runbooks.

## When logs are not in-cluster

Still use **CloudWatch** for control plane (`/aws/eks/...`), VPC Flow Logs, and load balancers even when apps log to Loki.

## FinOps (CPU / memory vs requests & limits)

For **right-sizing** workloads with **Prometheus** over the last day and optional **Helm values** PRs, use **`kubernetes-finops-workloads`**.

## Writing alerts against Loki

For **LogQL alert rules and panels** (and the common "error alert fires on info logs" failure mode), use **`loki-logql-alert-patterns`** — covers `| json` parsing, `level` filtering, `line_format` previews, and label cardinality.
