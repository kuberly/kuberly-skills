# ai-agent-tool MCP integration

[ai-agent-tool](https://bitbucket.org/kuberly/ai-agent-tool) is the in-cluster
MCP server for read-only Kubernetes / observability investigations. It is
**deployed once per cluster** by `kuberly/infrastructure` and reached from
agent runtimes (Cursor, Claude Code, Codex) via the cluster's internal
gateway:

```
https://ai-agent.<cluster>.kuberly.io/mcp
```

The agent runtime never speaks LLM-to-cluster directly. Instead, it speaks
MCP to ai-agent-tool, which:

- Serves **first-class MCP tools** for Loki, Prometheus, Tempo, Pyroscope,
  Grafana, Alertmanager, and meta operations — implemented with **direct HTTP
  and bundled CLIs** inside the pod (not via separate "Loki/Prometheus MCP"
  child servers for those backends).
- Embeds **kubernetes-mcp-server** over Streamable HTTP and **re-registers**
  almost all of its tools on the same `/mcp` surface (one token, one tool list).
- Exposes **`mcp_passthrough`** for the long tail of kubernetes-mcp-server
  tools that are not re-wrapped as first-class names.
- Surfaces a curated set of skills, commands, and system prompts via MCP's
  `prompts/list`.

Registry source of truth in code: `ai-agent-tool/internal/tools/types.go`
(static list) plus `server.registerKubernetesUpstreamTools` (dynamic K8s tools).

## What it exposes

### Tools (static registry)

These are always registered from `tools.All()` (same names agents call):

| Tool | Use it for |
| --- | --- |
| `terminal` | Shell escape hatch (must align with mono hub `Terminal` wiring); use structured tools first when they exist. |
| `query_logs` | LogQL via Loki (`logql`, `since`, limits, etc.). |
| `loki_label_values` | Discover label values before writing LogQL. |
| `find_error_pattern_logs` | Clustered error log patterns from Loki (anomaly triage). |
| `query_metrics` | PromQL instant or range against Prometheus. |
| `prom_get_targets` | Scrape target health / debug missing metrics. |
| `prom_list_metrics` | List `__name__` values known to Prometheus. |
| `prom_metric_metadata` | HELP/TYPE for metrics (choose rate vs histogram, etc.). |
| `prom_label_values` | Label cardinalities / values for PromQL. |
| `prom_series` | Match series and label sets for non-trivial selectors. |
| `query_traces` | Tempo search or fetch by `trace_id`. |
| `find_slow_requests` | Outlier slow traces for a service (Tempo). |
| `tempo_search_tags` | List Tempo tag names for TraceQL. |
| `tempo_tag_values` | Enumerate values for a tag (e.g. `service.name`). |
| `tempo_traceql_metrics` | TraceQL metrics / aggregates over traces. |
| `query_profiles` | Pyroscope / continuous profiling render. |
| `list_alerts` | Alertmanager firing alerts (compact). |
| `list_alert_groups` | Alerts grouped by route (incident boundary). |
| `list_silences` | Active / pending silences. |
| `list_receivers` | Alertmanager receiver destinations. |
| `alertmanager_status` | Alertmanager version / peers. |
| `search_dashboards` | Grafana dashboard search by UID/title/tags. |
| `grafana_get_dashboard` | Full dashboard JSON by UID. |
| `grafana_get_panel_queries` | PromQL/LogQL/SQL per panel (cheaper than full JSON). |
| `grafana_list_datasources` | Grafana datasource inventory. |
| `observability_status` | Reachability of Loki, Prometheus, Tempo, Grafana, Alertmanager, **Kubernetes upstream**, etc. |
| `mcp_passthrough` | Call any **kubernetes-mcp-server** tool: `upstream` must be the literal `kubernetes`, plus `tool`, optional `arguments` object, optional `concise`. Prefer first-class K8s tools below when they exist. |

### Tools (Kubernetes — dynamic)

At boot, the server imports **every tool** advertised by the embedded
kubernetes-mcp-server and exposes it with the **same `name` and JSON schema** on
this MCP. Names therefore follow that upstream (snake_case).

Representative first-class-style tools (non-exhaustive; call
`observability_status` or list tools over MCP if you need the live set):

| Tool | Use it for |
| --- | --- |
| `pods_list`, `pods_list_in_namespace` | List pods (filters, label selectors). |
| `pods_get` | Pod object details. |
| `pods_log` | Container logs (`previous=true` for last crashed instance). |
| `pods_top` | CPU/memory from metrics-server. |
| `events_list` | Cluster or namespace events. |
| `namespaces_list` | All namespaces. |
| `resources_list`, `resources_get` | Generic `apiVersion` / `kind` list/get. |
| `nodes_top`, `nodes_log`, `nodes_stats_summary` | Node capacity / pressure / logs. |
| `configuration_view`, `configuration_contexts_list` | kubeconfig visibility (as exposed by upstream). |

**Note:** Legacy docs sometimes mentioned `describe_resource` /
`list_namespaces` as standalone tools. In current builds, use **`resources_get`**
/ **`resources_list`** (general) and **`namespaces_list`** — or whatever exact
names the child server advertises after connect.

### Prompts

Namespaced by kind so they don't collide with this package's own prompts:

- `skill:troubleshoot-cluster`, `skill:investigate-crashloop`,
  `skill:investigate-latency`, `skill:investigate-alert`,
  `skill:investigate-log-anomaly`, `skill:investigate-saturation`
- `skill:cli-kubectl`, `skill:cli-logcli`, `skill:cli-promtool`,
  `skill:api-tempo`
- `command:investigate`, `command:loki-search`, `command:prom-query`,
  `command:trace-find`, `command:kube-inspect`, `command:mcp-status`
- `prompt:system-sre`, `prompt:investigation-protocol`

`command:investigate` is the recommended top-level entrypoint — pass it a
`subject` (e.g., `"auth-service crashlooping"`) and it picks the right
sub-skill and drives the loop.

## Connecting from a consumer repo

This MCP is **intentionally not declared in `apm.yml`** — the cluster
gateway URL varies per env, and the bearer token is a per-cluster secret.
Wire it into the consumer's runtime config directly. Examples:

### Claude Code (`~/.claude/mcp.json` or repo `.mcp.json`)

```json
{
  "mcpServers": {
    "kuberly-ai-agent": {
      "type": "http",
      "url": "https://ai-agent.<cluster>.kuberly.io/mcp",
      "headers": {
        "Authorization": "Bearer ${KUBERLY_AI_AGENT_TOKEN}"
      }
    }
  }
}
```

### Cursor (`.cursor/mcp.json`)

Cursor uses the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
proxy when the runtime can't speak Streamable HTTP directly:

```json
{
  "mcpServers": {
    "kuberly-ai-agent": {
      "type": "stdio",
      "command": "npx",
      "args": [
        "mcp-remote",
        "https://ai-agent.<cluster>.kuberly.io/mcp",
        "--header",
        "Authorization: Bearer ${KUBERLY_AI_AGENT_TOKEN}",
        "--transport",
        "http-only"
      ]
    }
  }
}
```

`KUBERLY_AI_AGENT_TOKEN` is the value of the `SERVICE_ACCOUNT_SECRET` key in
the `ai-agent-tool-secret` Kubernetes Secret in the deployment namespace
(`kuberly` by default). For routine SRE use, fetch it via the standard
secrets workflow for the target cluster — do not check it into a repo.

## Relationship to the other MCPs in this package

- `kuberly-platform` — repo-graph queries (cross-repo deps, blast radius,
  doc index). Source of truth for "what depends on this", not for runtime.
- `kuberly-state` — generated state snapshot of the org's deployments.
- `infra-router` — routes infra-shaped questions across this package.
- **`ai-agent-tool` (this doc)** — runtime cluster signal. Use **after** the
  graph MCPs have answered "what is the implicated component" and you need
  to look at logs / metrics / traces / live K8s state.

The `agent-sre` persona is the natural caller of all four.
