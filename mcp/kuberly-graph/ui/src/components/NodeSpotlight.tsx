import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";

import { api } from "../api/client";
import type { NodeDetail, SearchHit } from "../api/types";
import { CATEGORY_COLORS } from "../lib/categories";
import { useUI } from "../store/uiStore";

const CATEGORY_FILTERS = [
  { id: "all", label: "all" },
  { id: "iac_files", label: "IaC" },
  { id: "tg_state", label: "state" },
  { id: "k8s_resources", label: "k8s" },
  { id: "docs", label: "docs" },
  { id: "cue", label: "CUE" },
  { id: "ci_cd", label: "CI/CD" },
  { id: "applications", label: "apps" },
];

export function NodeSpotlight() {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const selectedId = useUI((s) => s.selectedNodeId);
  const selectNode = useUI((s) => s.selectNode);
  const setTab = useUI((s) => s.setTab);

  const search = useQuery({
    queryKey: ["search", query],
    queryFn: () => api.search(query, 50),
    enabled: query.length >= 2,
    staleTime: 15_000,
  });

  const detail = useQuery({
    queryKey: ["node", selectedId],
    queryFn: () => api.nodeDetail(selectedId!),
    enabled: !!selectedId,
  });

  const neighbors = useQuery({
    queryKey: ["neighbors", selectedId],
    queryFn: () => api.nodeNeighbors(selectedId!),
    enabled: !!selectedId,
  });

  const hits = (search.data?.hits ?? []).filter((h) =>
    filter === "all" ? true : (h.layer.includes(filter) || h.type.includes(filter.replace("_resources", "")))
  );

  return (
    <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* Search panel */}
      <div className="bg-bg-card border border-border rounded-lg p-4 flex flex-col gap-3">
        <div>
          <h3 className="text-sm font-medium text-text">Node spotlight</h3>
          <p className="text-xs text-text-muted">
            What was created by what — every node, with its inbound + outbound edges.
          </p>
        </div>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by id, label, type, or layer…"
          className="px-3 py-2 rounded-md bg-bg-panel border border-border text-sm
                     text-text placeholder:text-text-dim
                     focus:outline-none focus:border-accent-blue"
        />
        <div className="flex flex-wrap gap-1.5">
          {CATEGORY_FILTERS.map((f) => (
            <button
              key={f.id}
              onClick={() => setFilter(f.id)}
              className={clsx(
                "pill text-[11px] border transition-colors",
                filter === f.id
                  ? "bg-accent-blue/20 border-accent-blue/50 text-text"
                  : "bg-bg-panel border-border text-text-muted hover:text-text"
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="max-h-72 overflow-auto -mx-2">
          {query.length < 2 && (
            <div className="px-2 py-1 text-xs text-text-muted">type at least 2 characters to search</div>
          )}
          {search.isLoading && (
            <div className="px-2 py-1 text-xs text-text-muted">searching…</div>
          )}
          {search.error && (
            <div className="px-2 py-1 text-xs text-accent-red">
              search failed: {(search.error as Error).message}
            </div>
          )}
          {hits.map((h: SearchHit) => (
            <button
              key={h.id}
              onClick={() => selectNode(h.id)}
              className={clsx(
                "w-full text-left px-2 py-1.5 rounded-md hover:bg-bg-hover flex items-center gap-2",
                selectedId === h.id && "bg-bg-hover"
              )}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{
                  background: CATEGORY_COLORS[layerToCategory(h.layer)] ?? "#888",
                }}
              />
              <span className="text-sm text-text truncate">{h.label}</span>
              <span className="ml-auto text-[10px] font-mono text-text-muted shrink-0">
                {h.type}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Detail / neighborhood panel */}
      <div className="bg-bg-card border border-border rounded-lg p-4 flex flex-col gap-3 min-h-[18rem]">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-medium text-text">Neighborhood</h3>
          {selectedId && (
            <button
              onClick={() => setTab("graph")}
              className="text-xs text-accent-blue hover:underline"
            >
              focus in 3D graph →
            </button>
          )}
        </div>
        {!selectedId && (
          <div className="text-xs text-text-muted">
            Pick a node on the left to see its inbound and outbound edges. Click any neighbor to walk the
            graph.
          </div>
        )}
        {selectedId && (
          <div className="flex flex-col gap-3">
            <NodeHeader detail={detail.data} loading={detail.isLoading} />
            <Edges
              title="inbound"
              edges={neighbors.data?.inbound ?? []}
              loading={neighbors.isLoading}
              onPick={selectNode}
            />
            <Edges
              title="outbound"
              edges={neighbors.data?.outbound ?? []}
              loading={neighbors.isLoading}
              onPick={selectNode}
            />
          </div>
        )}
      </div>
    </section>
  );
}

function NodeHeader({ detail, loading }: { detail?: NodeDetail; loading: boolean }) {
  if (loading) return <div className="text-xs text-text-muted">loading…</div>;
  if (!detail) return null;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-sm font-medium text-text break-all">{detail.label || detail.id}</span>
      <span className="text-[11px] font-mono text-text-muted">
        {detail.type} · {detail.layer}
      </span>
    </div>
  );
}

function Edges({
  title,
  edges,
  loading,
  onPick,
}: {
  title: string;
  edges: { source: string; target: string; relation: string; other: { id: string; label: string; type: string } }[];
  loading: boolean;
  onPick: (id: string) => void;
}) {
  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-wider text-text-muted mb-1.5">
        {title} ({edges.length})
      </div>
      {loading && <div className="text-xs text-text-muted">…</div>}
      <div className="flex flex-col gap-0.5 max-h-32 overflow-auto">
        {edges.map((e, i) => (
          <button
            key={`${e.source}-${e.relation}-${e.target}-${i}`}
            onClick={() => onPick(e.other.id)}
            className="text-left px-2 py-1 rounded hover:bg-bg-hover text-xs flex items-center gap-2"
          >
            <span className="font-mono text-text-muted shrink-0 w-24 truncate">{e.relation}</span>
            <span className="text-text truncate">{e.other.label || e.other.id}</span>
            <span className="ml-auto text-[10px] font-mono text-text-muted shrink-0">{e.other.type}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function layerToCategory(layer: string): string {
  if (layer.startsWith("iac") || layer === "code") return "iac_files";
  if (layer.includes("state")) return "tg_state";
  if (layer.includes("k8s") || layer.includes("kubernetes")) return "k8s_resources";
  if (layer.includes("doc")) return "docs";
  if (layer.includes("cue") || layer.includes("schema")) return "cue";
  if (layer.includes("ci") || layer.includes("workflow") || layer.includes("image")) return "ci_cd";
  if (layer.includes("app") || layer.includes("rendered")) return "applications";
  if (layer.startsWith("aws")) return "aws";
  return "dependency";
}
