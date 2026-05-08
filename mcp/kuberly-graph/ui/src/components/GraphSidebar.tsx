import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { useUI } from "../store/uiStore";

// Slide-in detail panel pinned to the right side of the Graph tab. Shows
// the selected node's full attributes + neighbor list. Empty when nothing
// is selected.
export function GraphSidebar() {
  const id = useUI((s) => s.selectedNodeId);
  const close = () => useUI.getState().selectNode(null);

  const detail = useQuery({
    queryKey: ["node", id],
    queryFn: () => api.nodeDetail(id!),
    enabled: !!id,
  });
  const neighbors = useQuery({
    queryKey: ["neighbors", id],
    queryFn: () => api.nodeNeighbors(id!),
    enabled: !!id,
  });

  if (!id) {
    return (
      <aside className="w-80 shrink-0 border-l border-border bg-bg-panel p-4 text-xs text-text-muted">
        Click any node to inspect it.
      </aside>
    );
  }

  return (
    <aside className="w-96 shrink-0 border-l border-border bg-bg-panel overflow-auto">
      <div className="px-4 py-3 border-b border-border flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-text break-all">{detail.data?.label || id}</div>
          <div className="text-[11px] font-mono text-text-muted mt-0.5">
            {detail.data?.type} · {detail.data?.layer}
          </div>
        </div>
        <button onClick={close} className="text-text-muted hover:text-text" title="Close">
          ×
        </button>
      </div>

      {detail.isLoading && <div className="px-4 py-3 text-xs text-text-muted">loading…</div>}
      {detail.error && (
        <div className="px-4 py-3 text-xs text-accent-red">{(detail.error as Error).message}</div>
      )}
      {detail.data && (
        <div className="px-4 py-3">
          <div className="text-[10px] font-mono uppercase tracking-wider text-text-muted mb-1.5">
            attributes
          </div>
          <pre className="text-[11px] font-mono text-text whitespace-pre-wrap break-all">
            {JSON.stringify(detail.data.attributes ?? detail.data, null, 2)}
          </pre>
        </div>
      )}

      {neighbors.data && (
        <div className="px-4 py-3 border-t border-border flex flex-col gap-3">
          <Section
            title={`inbound · ${neighbors.data.inbound.length}`}
            edges={neighbors.data.inbound}
          />
          <Section
            title={`outbound · ${neighbors.data.outbound.length}`}
            edges={neighbors.data.outbound}
          />
        </div>
      )}
    </aside>
  );
}

function Section({
  title,
  edges,
}: {
  title: string;
  edges: { source: string; target: string; relation: string; other: { id: string; label: string; type: string } }[];
}) {
  const select = useUI((s) => s.selectNode);
  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-wider text-text-muted mb-1.5">
        {title}
      </div>
      <div className="flex flex-col gap-0.5">
        {edges.map((e, i) => (
          <button
            key={`${e.source}-${e.relation}-${e.target}-${i}`}
            onClick={() => select(e.other.id)}
            className="text-left px-2 py-1 rounded hover:bg-bg-hover text-xs flex items-center gap-2"
          >
            <span className="font-mono text-text-muted shrink-0 w-20 truncate">{e.relation}</span>
            <span className="text-text truncate flex-1">{e.other.label || e.other.id}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
