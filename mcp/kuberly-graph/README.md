# kuberly-graph

FastMCP microservice exposing the multi-layer Kuberly knowledge graph (cold
IaC + live k8s/argo + logs/metrics/traces) over MCP.

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
kuberly-graph serve --transport stdio --repo .
```

streamable-http (microservice):

```
kuberly-graph serve --transport streamable-http --host 0.0.0.0 --port 8765
```

One-shot tool call:

```
kuberly-graph call regenerate_layer --args '{"layer":"cold","repo_root":".","persist_dir":".kuberly"}'
```

## Layers (11)

`cold` (meta), `code`, `components`, `applications`, `rendered`, `state`,
`k8s`, `argo`, `logs`, `metrics`, `traces`.

## Tools (19)

query_nodes, get_node, get_neighbors, blast_radius, shortest_path, drift,
stats, regenerate_graph, regenerate_layer, list_layers, semantic_search,
find_similar, graph_stats, find_log_anomalies,
find_high_cardinality_metrics, find_metric_owners, find_slow_operations,
find_error_hotspots, service_call_graph.
