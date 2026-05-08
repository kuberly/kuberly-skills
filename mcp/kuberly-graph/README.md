# kuberly-platform

FastMCP entrypoint exposing the Kuberly platform graph, graph heuristics, and
runtime troubleshooting handoff. Agents should talk to this MCP first; it uses
the local graph before optionally forwarding to the live `ai-agent-tool` MCP.

## Agent entrypoint

Call `platform_index` first for any non-trivial question. It acts as the index
over all graph layers: reports what data is populated, resolves likely nodes,
applies heuristic routing, and recommends the next graph tool before any live
handoff is considered.

```sh
kuberly-platform call platform_index --args '{"query":"checkout is crashlooping in prod","environment":"prod"}'
```

For runtime incidents, call `troubleshoot` after `platform_index`. It reuses the
persisted graph index and forwards to `ai-agent-tool` only when live logs,
metrics, traces, or Kubernetes reads are needed.

For multi-step work, call `orchestrate`. It wraps `platform_index`, evidence
collection, optional live troubleshooting, and a parallel agent fanout plan. By
default it is read-only and materializes a session under
`.agents/prompts/<session>/` for OpenCode/Claude subagents to execute.

```sh
kuberly-platform call orchestrate --args '{"goal":"backend is slow in dev","environment":"main","namespace":"traigent-dev"}'
```

Useful orchestration helpers:

- `plan_agent_fanout` — return the parallel phase DAG without writing files.
- `graph_evidence` — return concise node/relation/semantic evidence.
- `orchestrate_status` — inspect a `.agents/prompts/<session>/` session.
- `orchestrate_continue` — append decisions or update session status.
- `collect_agent_results` — read persona output files back into MCP context.

## Stack

- FastMCP (`mcp.server.fastmcp.FastMCP`) for the MCP server.
- rustworkx for graph algorithms (BFS, shortest path, blast radius).
- LanceDB for the embedding store (auto-embedding via sentence-transformers
  `all-MiniLM-L6-v2`). When `lancedb` is missing, the package falls back to
  an in-memory store and semantic tools return `{"error": "lancedb not installed"}`.

## Install

```
cd mcp/kuberly-graph
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Run

stdio (Claude Code / `.mcp.json`):

```
kuberly-platform serve --transport stdio --repo .
```

streamable-http (microservice):

```
kuberly-platform serve --transport streamable-http --host 0.0.0.0 --port 8765
```

One-shot tool call:

```
kuberly-platform call regenerate_layer --args '{"layer":"cold","repo_root":".","persist_dir":".kuberly"}'
```

## Quick refresh after `aws sso login` + `kubectl` + ai-agent-tool MCP

```sh
kuberly-platform call regenerate_all
```

Auto-discovers the live-cluster MCP URL from `.mcp.json` and refreshes every layer.
For a single layer: `kuberly-platform call regenerate_layer --args '{"layer":"k8s"}'`.

`--args` is optional for `kuberly-platform call`; tools that take no arguments
(like `regenerate_all`, `list_layers`, `stats`) just work with the bare
command.

## Consolidated troubleshooting

Use `troubleshoot` as the SRE follow-up tool. It classifies the subject with
simple heuristics, resolves likely graph nodes from the persisted platform
index plus the cold graph, summarizes blast radius, and only calls
`ai-agent-tool` when the issue looks runtime-shaped.

```sh
kuberly-platform call troubleshoot --args '{"subject":"checkout crashlooping in prod","environment":"prod","namespace":"checkout"}'
```

Live calls are made only when `use_live` is true and either `mcp_url` /
`mcp_stdio` is provided or an `ai-agent-tool` entry is discoverable from the
consumer repo's `.mcp.json`.

## Layers (11)

`cold` (meta), `code`, `components`, `applications`, `rendered`, `state`,
`k8s`, `argo`, `logs`, `metrics`, `traces`.

## Tools

platform_index, orchestrate, plan_agent_fanout, graph_evidence,
orchestrate_status, orchestrate_continue, collect_agent_results,
query_nodes, get_node, get_neighbors, blast_radius, shortest_path, drift,
stats, regenerate_graph, regenerate_layer, regenerate_all, list_layers,
semantic_search, find_similar, graph_stats, find_log_anomalies,
find_high_cardinality_metrics, find_metric_owners, find_slow_operations,
find_error_hotspots, service_call_graph, troubleshoot, plus fusion super-tools.
