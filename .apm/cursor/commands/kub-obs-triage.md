---
name: /kub-obs-triage
id: kub-obs-triage
category: Operations
description: First hops for an incident — logs vs metrics vs K8s, which skill and which query next
---

Route an **operational issue** (latency, errors, deploy failed, “something is red”) for a **Kuberly-style** stack: AWS + EKS + Grafana/Prometheus/Loki where applicable.

**Inputs:** symptom, **since when**, **environment**, and whether they have **kubectl** / **AWS console** / **Grafana** access.

**Steps**

1. Load **`troubleshooting-aws-observability`** and, if workloads are in-cluster, **`eks-observability-stack`** — pick **one primary spine** (logs vs metrics vs events) first; avoid parallel thrash.
2. **Classify:** infra (node, CNI, LB) vs workload (app pod, HPA) vs data (RDS, cache) vs **GitOps** (Argo sync) — state which bucket fits the symptom.
3. Give **3 concrete next actions** in order, each with **tool + location** (e.g. “CloudWatch log group …”, “Grafana dashboard …”, `kubectl get … -n …`). Use generic names unless the user pasted real resource names.
4. If **kuberly-platform MCP** is available, **`query_nodes`** for the named module/app to suggest **blast** neighbors that might share failure (optional, short).

**Output**

- **Likely layer** (one of: network / IAM / data / k8s / gitops / unknown).
- **Do this next** — numbered list (max **5** steps).
- **Escalate when:** 2–3 bullets (e.g. regional outage, data loss risk, repeated OOM).
