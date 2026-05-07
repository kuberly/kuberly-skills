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

## Live cluster reads via the **`kuberly-ai-agent`** MCP

When the cluster has the **in-cluster `ai-agent-tool`** MCP installed (see **`mcp/ai-agent-tool/README.md`**), prefer its **structured K8s tools** for current-state questions instead of Prometheus / Loki:

| Tool | Use it for | Beats |
|---|---|---|
| **`pods_list_in_namespace`**, **`pods_get`** | "Is loki-ingester running? what's the restart count right now?" | `kubectl get pods` (no shell-out, returns JSON the agent can reason over) |
| **`pods_log`** (incl. **`previous=true`**) | Pre-restart container logs after a crash | `query_logs` — Loki only has logs that shipped; the dying instance's last lines are usually here |
| **`pods_top`** / **`nodes_top`** | Live CPU / memory **right now** | `query_metrics` — instant gauge, no PromQL needed; requires metrics-server |
| **`events_list`** | "What happened in the last 30 min?" — `OOMKilled`, `BackOff`, `FailedScheduling`, `NodeNotReady` | grepping Loki for k8s events |
| **`nodes_stats_summary`** | PSI (cgroup v2 pressure stalls) + per-pod kubelet stats; node-level `memory.psi` confirms saturation | metrics that may not be scraped frequently enough |
| **`resources_list`**, **`resources_get`** | Karpenter **`NodeClaim`** / **`NodePool`**, generic CRDs | `kubectl get <kind>` |

When the **MCP is not** wired into the runtime, fall back to **`kubectl`** (`kubectl get`, `kubectl describe`, `kubectl logs --previous`, `kubectl top`) and the Prometheus / Loki paths below.

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
