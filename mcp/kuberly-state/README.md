# kuberly-state — live cluster observability MCP (stub)

Companion to `kuberly-platform`. Where `kuberly-platform` is the **static
graph** (modules / components / applications as declared in HCL+JSON),
`kuberly-state` answers **runtime questions**:

- which pods are running, where, what's their status
- recent error logs from Loki / CloudWatch
- recent metrics from Prometheus / CloudWatch
- recent slow / failing traces from Tempo

## Status: stub

Every tool currently returns a structured `not_implemented` response.
The contract (`TOOLS[]` in `kuberly_state.py`) is the stable surface;
implementations land as the consumer's observability MCP / backends
become available.

## Wiring (opt-in)

Not auto-registered via `apm.yml` until the implementations are real.
Add manually to `.mcp.json` when you want to use it:

```json
{
  "mcpServers": {
    "kuberly-state": {
      "command": "python3",
      "args": [
        "apm_modules/kuberly/kuberly-skills/mcp/kuberly-state/kuberly_state.py",
        "mcp"
      ]
    }
  }
}
```

## Tool catalog

| Tool | Purpose | Backend (TBD) |
|---|---|---|
| `pod_status` | Phase/restarts/image of a K8s pod | kubectl / k8s API |
| `service_status` | Ready replicas + endpoints for k8s svc OR ECS service | kubectl / ECS DescribeServices |
| `recent_logs` | Lines + count from Loki / CloudWatch | logcli / aws logs |
| `recent_metrics` | Headline metric for a target | Prometheus HTTP API / CloudWatch |
| `trace_search` | Slow / failing traces | Tempo HTTP API |

## Replacing the stub

Edit `_dispatch(name, args)` in `kuberly_state.py`. Each tool name
maps to a handler — replace the call to `_stub(...)` with your
backend integration. Keep the `TOOLS[]` schema stable so personas
that already declared `mcp__kuberly-state__*` in their tools list
keep working.

## Why a separate MCP

- `kuberly-platform` should stay focused on the static graph; merging
  observability would inflate its cold-start cost (kubectl/AWS SDK
  imports, network probes) for every session that doesn't need it.
- Personas can opt in to `kuberly-state` only when they need runtime
  signals (`agent-sre` mostly).
- A separate process can crash, retry, or be replaced without
  destabilizing the static graph reads.
