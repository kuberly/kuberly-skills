// Types mirror the JSON responses from the kuberly-graph dashboard API
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
export interface LayerSummary {
  layer: string;
  node_count: number;
  edge_count: number;
  last_refresh: string;
  refresh_trigger: string;
  // The Python side may include other keys; keep an open record.
  [k: string]: unknown;
}

// /api/v1/stats — totals + per-layer breakdown.
export interface StatsResponse {
  node_count: number;
  edge_count: number;
  layers: Record<string, { node_count: number; edge_count: number }>;
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

// /api/v1/nodes/{id} — single-node detail (variable schema by node type).
export interface NodeDetail {
  id: string;
  type: string;
  layer: string;
  label?: string;
  category?: string;
  attributes?: Record<string, unknown>;
  [k: string]: unknown;
}

// /api/v1/nodes/{id}/neighbors — inbound + outbound edges with the other
// endpoint pre-resolved.
export interface NeighborEdge {
  source: string;
  target: string;
  relation: string;
  other: GraphNode;
}

export interface NeighborsResponse {
  inbound: NeighborEdge[];
  outbound: NeighborEdge[];
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
