# PromQL patterns — FinOps (24h-ish window)

Adjust **`namespace`**, **`cluster`**, and **recording rule** names to your Prometheus deployment. Prefer **recording rules** or **Grafana recorded queries** if you run these often (cheaper than huge ad-hoc ranges).

## Time range note

Prometheus range vectors have a max duration per query. For “24 hours” use **Grafana** with a 24h dashboard range, or **subqueries** with a resolution step (examples use **`[24h:5m]`** — tune step vs load).

## CPU — container max cores over 24h (approx)

Excludes empty container names (common cAdvisor pattern):

```promql
max_over_time(
  sum by (namespace, pod, container) (
    rate(container_cpu_usage_seconds_total{container!=""}[5m])
  )[24h:5m]
)
```

Compare to **requests** (cores):

```promql
sum by (namespace, pod, container) (
  kube_pod_container_resource_requests{resource="cpu"}
)
```

**Inspect one raw series** in Prometheus: KSM versions differ (cores vs millicores in the **`value`** / scrape presentation). Align units before dividing usage by request.

## Memory — working set max over 24h

```promql
max_over_time(
  sum by (namespace, pod, container) (
    container_memory_working_set_bytes{container!=""}
  )[24h:5m]
)
```

Requests / limits (bytes):

```promql
sum by (namespace, pod, container) (
  kube_pod_container_resource_requests{resource="memory"}
)
```

## One-shot “waste” ratio (memory example)

High ratio ⇒ request likely too large vs observed peaks (tune carefully if HPA uses % of request):

```promql
clamp_max(
  sum by (namespace, pod, container) (kube_pod_container_resource_requests{resource="memory"})
  /
  on(namespace,pod,container) group_left()
  max_over_time(
    sum by (namespace, pod, container) (
      container_memory_working_set_bytes{container!=""}
    )[24h:5m]
  ),
  100
)
```

## Map pod → workload (Deployment / StatefulSet)

Naming differs by **kube-state-metrics** version and whether you use **recording rules** (e.g. **`node_namespace_pod_container:*`** from **kube-prometheus-stack**). Prefer whatever **`kubernetes_pod_info`**-style metric your cluster already exposes; otherwise join **`kube_pod_owner`** to **ReplicaSet → Deployment** using your installed metric names (verify in **Graph** before baking into automation).

## Throttling hint (CPU)

```promql
sum by (namespace, pod, container) (
  rate(container_cpu_cfs_throttled_seconds_total{container!=""}[5m])
)
```

Non-zero sustained throttling often means **limits** too tight vs burst shape.
