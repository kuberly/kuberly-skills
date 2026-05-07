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

## Decision tree (when kuberly-ai-agent MCP is wired)

| Symptom | First call (live) | Second call (correlate) | Third call (history) |
|---|---|---|---|
| **Pod restarting / CrashLoopBackOff** | **`events_list`** namespace=X (filter by `reason=BackOff,OOMKilled,Killing`) | **`pods_log`** with **`previous=true`** — the dying instance's last lines | `query_logs` Loki for the same window if Loki has the pod's labels |
| **OOM suspicion** | **`nodes_stats_summary`** node=X — read `.node.memory.psi` (any nonzero `avg60` is real pressure) | **`pods_top`** namespace=X + **`pods_get`** for the suspect — compare working set to limit | `query_metrics` `container_memory_working_set_bytes` over 1h |
| **Slow request** | **`query_traces`** TraceQL `{ status = error \|\| duration > 1s }` for service | **`find_slow_requests`** (Grafana MCP) for outlier-trace surfacing | `query_metrics` p95 latency over 1h |
| **5xx surge / error rate** | **`find_error_pattern_logs`** (Grafana MCP / Loki anomaly) — beats raw `\|~ "error"` | **`query_logs`** filtered to the matched pattern | **`list_alerts`** to confirm Alertmanager already saw it |
| **"Something is slow but I don't know what"** | **`query_traces`** TraceQL with no service filter, sorted by duration desc, last 15m | **`pods_top all_namespaces=true`** — find the noisy neighbor | `query_metrics` namespace-scoped CPU rate |
| **Capacity / "we're out of room"** | **`nodes_top`** + **`resources_list kind=Node`** | Per-node: **`pods_list fieldSelector=spec.nodeName=X`** sum requests | Apply **`kubernetes-finops-workloads`** skill for the 24h view |
| **Karpenter churn / "pods keep moving"** | **`resources_list apiVersion=karpenter.sh/v1 kind=NodeClaim`** | **`events_list`** filter `Disrupted`, `Underutilized`, `Drift` | NodePool spec → check `disruption.consolidationPolicy` |
| **Scrape job dropped / metrics gap** | **`prom_get_targets`** (Prometheus MCP) | **`query_metrics`** `up{job="X"}` to confirm | `events_list` namespace=monitoring |
| **Deploy / sync failed** | `kubectl describe app <name> -n argocd` (no MCP wrapper yet) | **`pods_list_in_namespace`** target namespace — check ImagePullBackOff | **`events_list`** target namespace |

When **NO** MCP wrappers — fall back to `kubectl describe`, `kubectl logs --previous`, `kubectl top`, and `kubectl get events --sort-by=.lastTimestamp`. Order is the same; the wrappers are just less hands-on.

**Output**

- **Likely layer** (one of: network / IAM / data / k8s / gitops / unknown).
- **Do this next** — numbered list (max **5** steps), each citing a specific tool call from the table above.
- **Escalate when:** 2–3 bullets (e.g. regional outage, data loss risk, repeated OOM).
