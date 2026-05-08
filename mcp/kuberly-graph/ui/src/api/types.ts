// Types mirror the JSON responses from the kuberly-platform dashboard API
// (see mcp/kuberly-graph/src/kuberly_graph/dashboard/api.py). They are not
// auto-generated — when an endpoint shape changes, update the matching type
// here. Endpoints are versioned at /api/v1/*.

export type Category =
  | "iac_files"
  | "tg_state"
  | "k8s_resources"
  | "docs"
  | "cue"
  | "ci_cd"
  | "applications"
  | "live_observability"
  | "aws"
  | "dependency"
  | "meta";

// Node shape returned by /api/v1/graph (compact projection).
export interface GraphNode {
  id: string;
  type: string;
  layer: string;
  label: string;
  category: Category | string;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
}

export interface GraphResponse {
  layer: string | null;
  type: string | null;
  limit: number;
  node_count: number;
  edge_count: number;
  total_count: number;
  next_cursor: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// /api/v1/layers — one row per layer.
//
// The endpoint returns objects shaped like
//   { name, type, refresh_trigger, last_refresh, node_count, edge_count }
// where `name` is the layer id (e.g. "code", "k8s", "aws"), and `type`
// is the layer kind (e.g. "cold", "meta"). `last_refresh` is null when
// the layer has never been populated.
export interface LayerSummary {
  name: string;
  type: string;
  refresh_trigger: string;
  last_refresh: string | null;
  node_count: number;
  edge_count: number;
  [k: string]: unknown;
}

// /api/v1/stats — totals + per-layer breakdown.
//
// Field names match the LanceDB store's `stats()` output, which the dashboard
// API returns verbatim: `total_nodes` / `total_edges` (not the shorter
// `node_count` / `edge_count` you might expect from the layer summary
// endpoint), plus a `per_layer` dict keyed by layer name with per-layer
// node/edge counts and last_refresh.
export interface StatsResponse {
  mode: string;
  persist_dir: string;
  total_nodes: number;
  total_edges: number;
  per_layer: Record<string, { nodes: number; edges: number; last_refresh: string }>;
  scalar_indices?: Record<string, string>;
  [k: string]: unknown;
}

// /api/v1/aws-services — drives the Dashboard arch grid.
export interface AwsService {
  service: string;
  category: string;
  node_type: string;
  count: number;
  sample_id: string;
  sample_label: string;
}

export interface AwsServiceCategory {
  category: string;
  service_count: number;
  resource_count: number;
  services: AwsService[];
}

export interface AwsServicesResponse {
  total_resources: number;
  category_count: number;
  service_categories: AwsServiceCategory[];
}

// /api/v1/meta-overview — graph_layer self-describing nodes.
export interface MetaLayerNode {
  id: string;
  name: string;
  type: "graph_layer";
  layer_type: string;
  refresh_trigger: string;
  node_count: number;
  edge_count: number;
  last_refresh: string;
  node_types: string[];
}

export interface MetaOverviewResponse {
  node_count: number;
  edge_count: number;
  nodes: MetaLayerNode[];
  links: GraphEdge[];
}

// /api/v1/nodes/{id} — single-node detail. Backend wraps the node payload
// under a `node` key; the inner object's schema is type-dependent (every
// node carries id/type/layer/label, the rest is per-type metadata).
export interface NodeDetailInner {
  id: string;
  type: string;
  layer: string;
  label?: string;
  category?: string;
  [k: string]: unknown;
}

export interface NodeDetail {
  node: NodeDetailInner;
}

// /api/v1/nodes/{id}/neighbors — `incoming` and `outgoing` arrays. Edges
// are NOT pre-resolved — the other endpoint is just a string id, plus
// optional `label`/`type`/`layer` if the backend cached them. Resolve
// label client-side via the loaded graph nodes when needed.
export interface NeighborEdge {
  source: string;
  target: string;
  relation: string;
  label: string | null;
  type: string | null;
  layer: string | null;
}

export interface NeighborsResponse {
  node: string;
  node_info: NodeDetailInner;
  incoming: NeighborEdge[];
  outgoing: NeighborEdge[];
}

// /api/v1/search — substring across labels + ids.
export interface SearchHit {
  id: string;
  type: string;
  layer: string;
  label: string;
  score?: number;
}

export interface SearchResponse {
  hits: SearchHit[];
  count: number;
}
