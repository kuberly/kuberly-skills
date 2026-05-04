---
name: kubernetes-finops-workloads
description: >-
  FinOps on EKS/Kubernetes: use Prometheus (and optionally Grafana) to rank workloads by real CPU/memory
  use vs requests/limits over ~24h, suggest right-sized resources, and optionally patch Helm values via git — not live kubectl apply.
---

# Kubernetes FinOps — requests, limits, and actual usage

Use this skill when the goal is **cost / stability tuning**: find **who burns the most** CPU and memory compared to **declared requests and limits**, over roughly the **last 24 hours**, then **recommend** (or **prepare git changes** for) **`resources.requests` / `resources.limits`** in Helm or manifest repos.

Pair with **`eks-observability-stack`** for where **Prometheus** and **Grafana** usually live and how to reach the cluster.

## Preconditions

- **`kubectl`** context points at the **correct** cluster (see **`eks-observability-stack`** for EKS auth).
- **Prometheus** (or **VictoriaMetrics**, **Thanos** query frontend) reachable:
  - **Port-forward** (example pattern): `kubectl -n <prometheus-namespace> port-forward svc/<prometheus-service> 9090:9090` — **service names differ** per Helm release; discover with `kubectl get svc -A | rg -i prom`.
  - Or use **Grafana → Explore** against the Prometheus datasource (same PromQL).
- **kube-state-metrics** (or equivalent) exposing **`kube_pod_container_resource_requests`** / **`limits`** — standard on **kube-prometheus-stack**.

## What to measure (last ~24 hours)

| Signal | Prometheus idea | Notes |
|--------|-----------------|-------|
| **CPU used** | `rate(container_cpu_usage_seconds_total{container!=""}[5m])` aggregated over time | Use **`max_over_time`** / **`quantile_over_time`** over 24h windows — see **`references/promql-finops.md`**. |
| **Memory used** | `container_memory_working_set_bytes{container!=""}` | Prefer **working set** over **RSS** for OOM relevance. |
| **Declared requests / limits** | `kube_pod_container_resource_requests` / `kube_pod_container_resource_limits` | Join on **`namespace`**, **`pod`**, **`container`**, **`resource`** (`cpu`, `memory`). |

Aggregate to **namespace + controller** (Deployment/StatefulSet) when possible so FinOps owners get actionable rows — join via **`kube_pod_owner`** where available.

## Ranking “largest consumers”

Build a **table** (export CSV for spreadsheets if useful):

1. **Per container**: max or high percentile CPU cores and memory bytes over 24h.
2. **Side by side** with **request** and **limit** (converted to cores and bytes for comparison).
3. **Derived columns**: e.g. **used ÷ request** (overcommit reality), **used ÷ limit** (headroom to throttling / OOM risk).

Sort by **waste** (high request, low use) for savings candidates, and separately by **pressure** (use near/limit or above request with noisy throttling) for reliability fixes.

## Suggested requests and limits (heuristics, not laws)

- **Requests** (scheduling / guaranteed): anchor to **sustained** load — e.g. **~p90–p95** of 24h CPU **cores**, **~p95–p99** of memory, then multiply by a small headroom (**1.1–1.3**). Round to sensible fractions (**50m–250m** steps for CPU; **Mi** boundaries for memory).
- **Limits** (burst cap): **≥ requests**; avoid setting limits **below** observed spikes unless you accept throttling. Memory limits **at or slightly above** peak working set reduce OOM risk; **no limit** is valid for some batch workloads with tight node protection — document trade-offs.
- **Very low use vs high request**: candidate to **shrink requests** (save quota / improve packing); validate with owners and **HPA / VPA** if present.
- Consider **Vertical Pod Autoscaler** or **HPA** instead of one-off guesses for volatile services.

Always state **assumptions** (sampling step, missing series, `image=""` / `pause` exclusions) in the summary.

## Grafana

- **Explore** → Prometheus → run the same PromQL as **`references/promql-finops.md`**.
- Dashboards: reuse **Kubernetes / Compute Resources / Namespace** style boards if already installed; still export **top N** for the ticket.

## Changing Helm values (preferred path)

**Do not** `kubectl apply` ad-hoc production changes from an agent session as the default FinOps outcome.

1. **Report first**: ranked table + 5–10 bullet recommendations (**`short-session-memory`** — keep raw time series out of durable docs).
2. **Git edits** (when the user explicitly wants automation): locate **Helm values** or **CUE / Argo app** definitions in the **infra or app repo**, patch **`resources`** blocks, open a **PR**, run **`helm template`** / **`helm lint`** (or your CI) for the affected chart(s).
3. **Rollout**: human or pipeline runs **`helm upgrade`** / GitOps sync after review — same separation as **plan-only** Terragrunt for cluster modules (**`kuberly-stack-context`**).

If values are generated from **kuberly-stack** **`applications/`** JSON (CUE path), trace back to the **application JSON** or **HelmRelease** source of truth before editing.

## Agent checklist

- [ ] Confirm **cluster** and **namespace** for Prometheus.
- [ ] Run **narrow** queries (one namespace or one team) before “all namespaces” to avoid Prometheus timeouts.
- [ ] Exclude **`container="POD"`**, **`pause`**, and idle **`namespace`=`kube-system`** helpers unless the question is about them.
- [ ] Separate **CPU** (compressible) vs **memory** (OOM) recommendations.
- [ ] Call out workloads **without** requests/limits (policy gap).

## Related skills

- **`eks-observability-stack`** — Grafana / Prometheus namespaces and access.
- **`short-session-memory`** — ephemeral tables vs PR / ticket.
- **`infra-change-git-pr-workflow`**, **`git-pr-templates`** — when FinOps becomes a values change PR.

## PromQL library

See **`references/promql-finops.md`**.
