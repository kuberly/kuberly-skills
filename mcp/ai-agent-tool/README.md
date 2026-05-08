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

- Routes log/metric/trace queries through the cluster's Grafana / Loki /
  Prometheus / Tempo MCP servers (auto-detected via DNS).
- Falls back to the bundled CLI (`kubectl`, `logcli`, `promtool`,
  `tempo-cli`) if any upstream MCP is unavailable.
- Surfaces a curated set of skills, commands, and system prompts via MCP's
  `prompts/list`.

## What it exposes

### Tools

| Tool | Use it for |
|---|---|
| `query_logs` | LogQL query (Loki MCP → `logcli`) |
| `query_metrics` | PromQL instant or range (Prometheus MCP → `promtool`) |
| `query_traces` | TraceQL search or trace-by-id (Tempo MCP → Tempo HTTP) |
| `describe_resource` | `kubectl describe` for one resource |
| `list_namespaces` | List cluster namespaces |
| `observability_status` | Which upstream MCPs are reachable right now |
| `terminal` | Bash escape hatch (read-only) |

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

## Relationship to the consolidated Kuberly MCP

Agents should use `kuberly-platform` as the primary MCP. Its `troubleshoot` tool
starts with graph context, blast radius, ownership, and persisted overlays, then
calls **`ai-agent-tool`** only when live runtime signal is needed.

For non-trivial questions, call `kuberly-platform.platform_index` first. It acts
as the graph index/router and tells the agent whether to stay inside graph tools
or proceed to `troubleshoot` for a possible live `ai-agent-tool` handoff.

- `kuberly-platform` — consolidated agent-facing MCP and graph-first
  troubleshooting entrypoint.
- **`ai-agent-tool` (this doc)** — downstream runtime cluster signal for logs,
  metrics, traces, and live Kubernetes reads.
- legacy repo-graph stdio code — kept for compatibility while graph
  functionality moves behind the consolidated `kuberly-platform` MCP.

The `agent-sre` persona should normally call `kuberly-platform.platform_index`,
then `kuberly-platform.troubleshoot` only for runtime-shaped incidents.
