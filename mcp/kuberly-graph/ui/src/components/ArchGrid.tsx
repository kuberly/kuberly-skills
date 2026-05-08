import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";

import { api } from "../api/client";
import type { AwsServicesResponse, GraphNode } from "../api/types";
import { AWS_CAT_COLORS, awsTypeMark } from "../lib/categories";
import { useUI } from "../store/uiStore";

interface Props {
  data?: AwsServicesResponse;
  loading: boolean;
  error?: string;
}

export function ArchGrid({ data, loading, error }: Props) {
  const selection = useUI((s) => s.awsTileSelection);
  const selectTile = useUI((s) => s.selectAwsTile);

  if (loading) {
    return <div className="text-xs text-text-muted">loading AWS scanner data…</div>;
  }
  if (error) {
    return <div className="text-xs text-accent-red">aws-services failed: {error}</div>;
  }
  if (!data || !data.service_categories.length) {
    return <div className="text-xs text-text-muted">No AWS resources scanned yet.</div>;
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 text-xs">
        <span className="font-mono text-text-muted">
          {data.total_resources.toLocaleString()} deployed resources
        </span>
        <span className="text-text-dim">·</span>
        <span className="font-mono text-text-muted">
          {data.service_categories.reduce((acc, c) => acc + c.service_count, 0)} AWS services
        </span>
        <span className="text-text-dim">·</span>
        <span className="font-mono text-text-muted">
          {data.category_count} categories
        </span>
      </div>

      <div className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-3">
        {data.service_categories.map((cat) => (
          <div key={cat.category} className="contents">
            <div className="flex flex-col">
              <span className="text-sm font-medium" style={{ color: AWS_CAT_COLORS[cat.category] ?? "#fff" }}>
                {cat.category}
              </span>
              <span className="text-[11px] text-text-muted">
                {cat.service_count} services · {cat.resource_count} resources
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {cat.services.map((svc) => {
                const active =
                  selection?.category === cat.category && selection?.nodeType === svc.node_type;
                return (
                  <button
                    key={svc.node_type}
                    className={clsx("arch-tile", active && "active")}
                    onClick={() =>
                      selectTile(
                        active ? null : { category: cat.category, nodeType: svc.node_type }
                      )
                    }
                  >
                    <span className="badge-count">{svc.count}</span>
                    <div className="flex items-center gap-2">
                      <span
                        className="w-6 h-6 flex items-center justify-center rounded-sm font-mono text-[10px] font-medium"
                        style={{
                          background: `${AWS_CAT_COLORS[cat.category] ?? "#888"}22`,
                          color: AWS_CAT_COLORS[cat.category] ?? "#fff",
                        }}
                      >
                        {awsTypeMark(svc.node_type)}
                      </span>
                      <span className="text-sm text-text">{svc.service}</span>
                    </div>
                    <span className="text-[10px] font-mono text-text-muted truncate max-w-[180px]">
                      {svc.sample_label}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      {selection && <ArchTileDetail selection={selection} />}
    </div>
  );
}

// When a tile is selected, list every resource of that type across the
// graph. Uses /api/v1/graph filtered by node type.
function ArchTileDetail({ selection }: { selection: { category: string; nodeType: string } }) {
  const setTab = useUI((s) => s.setTab);
  const selectNode = useUI((s) => s.selectNode);

  const q = useQuery({
    queryKey: ["graph", "by-type", selection.nodeType],
    queryFn: () => api.graph({ type: selection.nodeType, limit: 200 }),
    staleTime: 60_000,
  });

  return (
    <div className="mt-2 rounded-lg border border-accent-blue/40 bg-bg-card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center gap-3">
        <span
          className="w-2 h-2 rounded-full"
          style={{ background: AWS_CAT_COLORS[selection.category] ?? "#888" }}
        />
        <span className="text-sm font-medium text-text">{selection.nodeType}</span>
        <span className="text-xs text-text-muted">{q.data?.node_count ?? 0} resources</span>
        <button
          onClick={() => {
            setTab("graph");
          }}
          className="ml-auto text-xs text-accent-blue hover:underline"
        >
          open in 3D graph →
        </button>
      </div>
      <div className="max-h-72 overflow-auto">
        {q.isLoading && <div className="px-4 py-3 text-xs text-text-muted">loading…</div>}
        {q.error && <div className="px-4 py-3 text-xs text-accent-red">{(q.error as Error).message}</div>}
        {q.data?.nodes.length === 0 && (
          <div className="px-4 py-3 text-xs text-text-muted">no resources of this type yet</div>
        )}
        <table className="w-full text-xs font-mono">
          <thead className="text-text-muted">
            <tr className="text-left">
              <th className="px-4 py-2 font-normal">id</th>
              <th className="px-4 py-2 font-normal">layer</th>
              <th className="px-4 py-2 font-normal">label</th>
            </tr>
          </thead>
          <tbody>
            {q.data?.nodes.map((n: GraphNode) => (
              <tr
                key={n.id}
                className="border-t border-border hover:bg-bg-hover cursor-pointer"
                onClick={() => {
                  selectNode(n.id);
                  setTab("graph");
                }}
              >
                <td className="px-4 py-1.5 text-text">{n.id}</td>
                <td className="px-4 py-1.5 text-text-muted">{n.layer}</td>
                <td className="px-4 py-1.5 text-text-muted truncate max-w-[400px]">{n.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
