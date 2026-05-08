// Thin fetch wrapper around the kuberly-graph dashboard API. In dev Vite
// proxies /api → http://127.0.0.1:8000; in prod nginx does the same.
//
// Design notes:
//  - All endpoints are GET; no auth surface yet.
//  - Errors raise so TanStack Query can put them in `query.error`.
//  - Cursor-paginated /api/v1/graph is exposed as fetchAllGraph() that walks
//    pages until next_cursor is null. For the MVP we keep it linear (no
//    background streaming); that's fine up to ~10k nodes.

import type {
  AwsServicesResponse,
  GraphResponse,
  LayerSummary,
  MetaOverviewResponse,
  NeighborsResponse,
  NodeDetail,
  SearchResponse,
  StatsResponse,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

// Encode a node id for use as a Starlette `{node_id:path}` route param.
//
// The standard `encodeURIComponent` percent-encodes `/`, but Starlette's
// path-converter wants raw slashes — feeding it `%2F` returns a 404 for
// ids like "tf_state_resource:foo/bar/baz". Encode everything else
// (especially `?`, `#`, and the rest of the reserved set) but leave `/`
// intact.
function encodeNodeId(id: string): string {
  return encodeURIComponent(id).replace(/%2F/g, "/");
}

async function getJSON<T>(path: string): Promise<T> {
  // No `credentials: "include"` — the dashboard API has no auth surface,
  // and browsers reject responses that combine wildcard `Access-Control-
  // Allow-Origin: *` (the backend default) with credentials.
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    let body = "";
    try {
      body = await res.text();
    } catch {
      // best-effort
    }
    throw new Error(`${res.status} ${res.statusText}: ${path} — ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  layers: () => getJSON<LayerSummary[]>("/api/v1/layers"),
  stats: () => getJSON<StatsResponse>("/api/v1/stats"),

  graph: (params: { layer?: string; type?: string; limit?: number; cursor?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.layer) q.set("layer", params.layer);
    if (params.type) q.set("type", params.type);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.cursor) q.set("cursor", params.cursor);
    const qs = q.toString();
    return getJSON<GraphResponse>(`/api/v1/graph${qs ? "?" + qs : ""}`);
  },

  /**
   * Walks all pages of /api/v1/graph and concatenates them. Caps at maxNodes
   * (default 20k) so a runaway graph doesn't OOM the browser.
   */
  graphAll: async (params: { layer?: string; type?: string; pageSize?: number; maxNodes?: number } = {}) => {
    const pageSize = params.pageSize ?? 5000;
    const maxNodes = params.maxNodes ?? 20_000;
    const all: GraphResponse = {
      layer: params.layer ?? null,
      type: params.type ?? null,
      limit: pageSize,
      node_count: 0,
      edge_count: 0,
      total_count: 0,
      next_cursor: null,
      nodes: [],
      edges: [],
    };
    let cursor: string | undefined;
    while (true) {
      const page = await api.graph({
        layer: params.layer,
        type: params.type,
        limit: pageSize,
        cursor,
      });
      all.nodes.push(...page.nodes);
      all.edges.push(...page.edges);
      all.total_count = page.total_count;
      if (!page.next_cursor || all.nodes.length >= maxNodes) {
        break;
      }
      cursor = page.next_cursor;
    }
    all.node_count = all.nodes.length;
    all.edge_count = all.edges.length;
    return all;
  },

  awsServices: () => getJSON<AwsServicesResponse>("/api/v1/aws-services"),
  metaOverview: () => getJSON<MetaOverviewResponse>("/api/v1/meta-overview"),

  nodeDetail: (id: string) => getJSON<NodeDetail>(`/api/v1/nodes/${encodeNodeId(id)}`),
  nodeNeighbors: (id: string) =>
    getJSON<NeighborsResponse>(`/api/v1/nodes/${encodeNodeId(id)}/neighbors`),

  search: (q: string, limit = 25) =>
    getJSON<SearchResponse>(`/api/v1/search?q=${encodeURIComponent(q)}&limit=${limit}`),
};

export type Api = typeof api;
